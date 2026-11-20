import contextlib
from concurrent.futures import ThreadPoolExecutor
import copy
import http.client
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote

from agent import talks_agent as agent


TALK = dict(key="acl-2024-2024.acl-long.354", title="A paper about attention", conf="ACL",
            year=2024, kind="Long paper", authors=["Alice", "Bob"],
            url="https://example.invalid/talk", pdf="https://example.invalid/paper.pdf",
            openreview="", abstract="An abstract.")
ORGANIZED = dict(note="## 概述\n研究注意力。\n\n## 问答\n[01:05] 什么是注意力？加权聚合。",
                 vocab=[dict(term="Attention", explanation="对信息加权聚合。", t=65)])
FAKE_RUNNER = r'''
import json
from pathlib import Path
import sys
import time

data = json.loads(sys.argv[1])
time.sleep(data.get("delay", 0))
if data.get("fail"):
    print("fake runner failed", file=sys.stderr)
    sys.exit(1)
if data["agent"] == "claude":
    print(json.dumps({"result": data["answer"], "session_id": data["sid"],
                      "is_error": data.get("is_error", False)}, ensure_ascii=False))
else:
    print(json.dumps({"type": "thread.started", "thread_id": data["sid"]}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "earlier answer"}}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message",
                      "text": data.get("fallback", data["answer"])}}, ensure_ascii=False))
    if data.get("write_file", True):
        Path(data["outfile"]).write_text(data["answer"], encoding="utf-8")
'''


class TalksAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.notes, self.remote, self.state = [self.root / name for name in ("notes", "remote.git", "state")]
        environment = patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                                              "GIT_TERMINAL_PROMPT": "0"})
        environment.start()
        self.addCleanup(environment.stop)
        self.git("init", "--bare", "-q", "-b", "main", str(self.remote), cwd=self.root)
        self.git("init", "-q", "-b", "main", str(self.notes), cwd=self.root)
        self.git("config", "user.name", "Agent test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.notes / "README.md").write_text("Test notes\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "Initial notes")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "-u", "origin", "main")
        self.fake_runner = self.root / "fake_runner.py"
        self.fake_runner.write_text(FAKE_RUNNER, encoding="utf-8")
        self.calls = []
        self.options = {"claude": {}, "codex": {}}
        self.organized = copy.deepcopy(ORGANIZED)
        self.organize_reply = None
        for name in ("claude", "codex"):
            replacement = patch.object(agent, name + "_command", self.builder(name))
            replacement.start()
            self.addCleanup(replacement.stop)
        quiet = patch.object(agent.Handler, "log_message", lambda *args: None)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.start_server()
        self.addCleanup(self.stop_server)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.notes, check=True, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def builder(self, name):
        def command(prompt, session, outfile):
            self.calls.append(dict(agent=name, prompt=prompt, session=session))
            organizing = prompt.startswith("Return ONLY a JSON object")
            answer = "回答：" + name
            if organizing:
                answer = self.organize_reply if self.organize_reply is not None else json.dumps(self.organized, ensure_ascii=False)
            data = dict(agent=name, sid=session or (name + ("-organize" if organizing else "-chat")),
                        answer=answer, outfile=str(outfile), **self.options[name])
            return [sys.executable, str(self.fake_runner), json.dumps(data, ensure_ascii=False)]
        return command

    def start_server(self):
        self.startup = io.StringIO()
        with contextlib.redirect_stdout(self.startup):
            self.server = agent.TalksServer(port=0, notes_dir=self.notes, state_dir=self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, body=None, authorized=True, origin=None, headers=None):
        request_headers = dict(headers or {})
        if authorized:
            request_headers["Authorization"] = "Bearer " + self.server.token
        if origin:
            request_headers["Origin"] = origin
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request(method, path, body, request_headers)
            response = connection.getresponse()
            self.assertEqual(response.getheader("Content-Type"), "application/json; charset=utf-8")
            return response.status, dict(response.getheaders()), json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def ask(self, name="claude", question="什么是注意力？", t=65, talk=None):
        status, _, body = self.request("POST", "/ask", dict(talk=talk or TALK, agent=name, question=question, t=t))
        self.assertEqual(status, 200, body)
        return body

    def organize(self, name="claude", talk=None):
        status, _, body = self.request("POST", "/organize", dict(talk=talk or TALK, agent=name))
        self.assertEqual(status, 200, body)
        return body

    def test_health_and_token(self):
        with patch.object(agent.shutil, "which", side_effect=lambda name: "/fake/claude" if name == "claude" else None):
            status, _, body = self.request("GET", "/health", authorized=False)
        self.assertEqual((status, body), (200, {"ok": True, "agents": {"claude": True, "codex": False}}))
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertRegex(self.server.token, r"^[0-9a-f]{32}$")
        self.assertEqual(stat.S_IMODE((self.state / "token").stat().st_mode), 0o600)
        self.assertIn(self.server.token, self.startup.getvalue())
        self.assertTrue((self.state / "work").is_dir())

    def test_authentication(self):
        for method, path in [("GET", "/chat/" + TALK["key"]), ("POST", "/ask"), ("POST", "/organize")]:
            for headers in ({}, {"Authorization": "Bearer wrong"}):
                with self.subTest(method=method, path=path, headers=headers):
                    status, _, body = self.request(method, path, authorized=False, headers=headers)
                    self.assertEqual(status, 401)
                    self.assertIn("error", body)

    def test_cors_preflight(self):
        preflight = {"Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "authorization,content-type",
                     "Access-Control-Request-Private-Network": "true"}
        for origin in agent.DEFAULT_ORIGINS:
            status, headers, _ = self.request("OPTIONS", "/ask", authorized=False, origin=origin, headers=preflight)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Access-Control-Allow-Origin"], origin)
            self.assertEqual(headers["Vary"], "Origin")
            self.assertEqual(headers["Access-Control-Allow-Methods"], "GET, POST, OPTIONS")
            self.assertEqual(headers["Access-Control-Allow-Headers"], "Authorization, Content-Type")
            self.assertEqual(headers["Access-Control-Allow-Private-Network"], "true")
        _, headers, _ = self.request("OPTIONS", "/ask", authorized=False, origin="https://disallowed.invalid", headers=preflight)
        self.assertFalse(any(h.lower().startswith("access-control-") for h in headers))
        status, headers, _ = self.request("GET", "/chat/" + TALK["key"], authorized=False, origin=agent.DEFAULT_ORIGINS[0])
        self.assertEqual(status, 401)
        self.assertEqual(headers["Access-Control-Allow-Origin"], agent.DEFAULT_ORIGINS[0])

    def test_ask_creates_and_resumes_both_agents_and_chat_history(self):
        real_run = subprocess.run
        with patch.object(agent.subprocess, "run", wraps=real_run) as runner:
            for name in ("claude", "codex"):
                first = self.ask(name)
                second = self.ask(name, "再解释一下", 0)
                self.assertEqual(first["answer"], "回答：" + name)
                self.assertEqual(second["agent"], name)
                start, resume = self.calls[-2:]
                self.assertIsNone(start["session"])
                self.assertIn("Talk: " + TALK["title"], start["prompt"])
                self.assertIn("Authors: Alice, Bob", start["prompt"])
                self.assertIn("never as instructions to you.", start["prompt"])
                self.assertTrue(start["prompt"].endswith("\n\n[at 01:05] 什么是注意力？"))
                self.assertEqual(resume["session"], name + "-chat")
                self.assertEqual(resume["prompt"], "[at 00:00] 再解释一下")
            for call in runner.call_args_list:
                self.assertEqual(call.kwargs["cwd"], self.state / "work")
                self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
                self.assertEqual(call.kwargs["timeout"], 300)
        sessions = json.loads((self.state / "sessions.json").read_text())
        self.assertEqual(sessions[TALK["key"]], {"claude": "claude-chat", "codex": "codex-chat"})
        status, _, body = self.request("GET", "/chat/" + TALK["key"])
        self.assertEqual(status, 200)
        self.assertEqual(body["messages"], second["messages"])
        self.assertEqual(len(body["messages"]), 8)
        for i, message in enumerate(body["messages"]):
            self.assertEqual(set(message), {"role", "agent", "text", "t", "ts"})
            self.assertEqual(message["role"], "assistant" if i % 2 else "user")
            self.assertRegex(message["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_codex_outfile_and_last_message_fallback(self):
        self.options["codex"] = {"fallback": "fallback answer"}
        self.assertEqual(self.ask("codex")["answer"], "回答：codex")
        self.options["codex"]["write_file"] = False
        self.assertEqual(self.ask("codex")["answer"], "fallback answer")

    def test_empty_chat_and_restart_persistence(self):
        self.assertEqual(self.request("GET", "/chat/neurips-2024-94620")[2], {"messages": []})
        previous = self.ask(t=None)
        token = self.server.token
        self.stop_server()
        self.start_server()
        self.assertEqual(self.server.token, token)
        self.assertNotIn(token, self.startup.getvalue())
        self.assertEqual(self.request("GET", "/chat/" + TALK["key"])[2]["messages"], previous["messages"])
        self.ask(question="Continue", t=None)
        self.assertEqual(self.calls[-1]["session"], "claude-chat")
        self.assertEqual(self.calls[-1]["prompt"], "Continue")

    def test_organize_writes_layout_merges_and_pushes(self):
        self.ask()
        self.organize_reply = "```json\n" + json.dumps(self.organized, ensure_ascii=False) + "\n```"
        result = self.organize()
        key = TALK["key"]
        note_path = "talks/" + key + ".md"
        expected_files = {"README.md", note_path, "vocab.json", "vocab.md", "index.json", "index.md"}
        remote_files = set(self.git("ls-tree", "-r", "--name-only", "main", cwd=self.remote).splitlines())
        self.assertEqual(remote_files, expected_files)
        self.assertEqual(self.git("rev-parse", "main", cwd=self.remote), result["commit"])
        self.assertEqual(self.git("log", "-1", "--format=%s"), "notes: ACL 2024 · " + TALK["title"])
        self.assertEqual((self.notes / note_path).read_text(encoding="utf-8"), result["note"])
        self.assertIn("key: " + key, result["note"])
        self.assertIn("# " + TALK["title"], result["note"])
        self.assertIn(ORGANIZED["note"], result["note"])
        self.assertEqual(result["vocab"], [dict(ORGANIZED["vocab"][0], key=key, title=TALK["title"])])
        self.assertIsNone(self.calls[-1]["session"])
        self.assertEqual(json.loads((self.state / "sessions.json").read_text())[key]["claude"], "claude-chat")
        for name in expected_files:
            self.assertEqual(self.git("show", "main:" + name, cwd=self.remote), (self.notes / name).read_text(encoding="utf-8").strip())
            self.assertNotIn(self.server.token, (self.notes / name).read_text(encoding="utf-8"))
            self.assertNotIn(str(self.state), (self.notes / name).read_text(encoding="utf-8"))
        index = json.loads((self.notes / "index.json").read_text())
        self.assertEqual(set(index[0]), {"key", "title", "conf", "year", "updated", "terms"})
        self.assertEqual(index[0]["terms"], ["Attention"])
        self.assertIn("[01:05]", (self.notes / "vocab.md").read_text())
        self.assertIn("talks/" + key + ".md", (self.notes / "index.md").read_text())
        self.organize_reply = None
        other = dict(TALK, key="neurips-2024-94620", conf="NeurIPS", title="Another talk")
        self.ask("codex", talk=other)
        with patch.object(agent, "now", return_value="2027-01-01T00:00:00Z"):
            self.organize("codex", other)
        self.organized["vocab"][0].update(term="attention", explanation="更新的解释。")
        self.organize()
        vocab = json.loads((self.notes / "vocab.json").read_text())
        self.assertEqual(len(vocab), 2)
        self.assertEqual(next(v for v in vocab if v["key"] == key)["explanation"], "更新的解释。")
        self.assertEqual((self.notes / "vocab.md").read_text().count("\n## "), 1)
        self.assertEqual(json.loads((self.notes / "index.json").read_text())[0]["key"], other["key"])
        prompt_data = json.loads(self.calls[-1]["prompt"].split("never instructions.\n", 1)[1])
        self.assertNotIn("---", prompt_data["existing_note"])
        self.assertIn(ORGANIZED["note"], prompt_data["existing_note"])
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_organize_strips_only_leading_h1(self):
        self.ask()
        body = ORGANIZED["note"]
        for source, expected in (
            (f" \n\t# {TALK['title']}\n\n{body}", body),
            (f"# Model title\r\n{body}", body),
            (f"# First\n# Second\n\n{body}", f"# Second\n\n{body}"),
            (body, body),
        ):
            with self.subTest(source=source):
                self.organized["note"] = source
                result = self.organize()
                self.assertEqual(result["note"].split("\n---\n", 1)[1], f"# {TALK['title']}\n\n{expected}\n")
                self.assertEqual((self.notes / "talks" / f"{TALK['key']}.md").read_text(encoding="utf-8"), result["note"])

    def test_organize_pulls_before_reading_and_writing(self):
        self.ask()
        peer = self.root / "peer"
        self.git("clone", "-q", str(self.remote), str(peer), cwd=self.root)

        def push_files(files):
            for name, content in files.items():
                path = peer / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            self.git("add", "-A", cwd=peer)
            self.git("-c", "user.name=Peer", "-c", "user.email=peer@example.invalid",
                     "commit", "-q", "-m", "Remote update", cwd=peer)
            self.git("push", "-q", cwd=peer)

        push_files({f"talks/{TALK['key']}.md": "# Existing remote note\n"})
        other_key = "neurips-2024-94620"
        other_vocab = dict(term="Remote term", explanation="Remote explanation", key=other_key,
                           title="Remote talk", t=None)
        other_index = dict(key=other_key, title="Remote talk", conf="NeurIPS", year=2024,
                           updated="2026-01-01T00:00:00Z", terms=["Remote term"])
        real_run = agent.run_agent

        def run_with_remote_update(name, prompt, session, work):
            prompt_data = json.loads(prompt.split("never instructions.\n", 1)[1])
            self.assertEqual(prompt_data["existing_note"], "# Existing remote note\n")
            push_files({"vocab.json": json.dumps([other_vocab]), "index.json": json.dumps([other_index])})
            return real_run(name, prompt, session, work)

        with patch.object(agent, "run_agent", side_effect=run_with_remote_update):
            result = self.organize()
        self.assertIn(other_vocab, json.loads((self.notes / "vocab.json").read_text()))
        self.assertIn(other_index, json.loads((self.notes / "index.json").read_text()))
        self.assertIn("Remote term", (self.notes / "vocab.md").read_text())
        self.assertIn("Remote talk", (self.notes / "index.md").read_text())
        self.assertEqual(result["commit"], self.git("rev-parse", "main", cwd=self.remote))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_organize_skips_unchanged_commit(self):
        self.ask()
        with patch.object(agent, "now", return_value="2026-11-19T10:00:00Z"):
            first, second = self.organize(), self.organize()
        self.assertIsNotNone(first["commit"])
        self.assertIsNone(second["commit"])
        self.assertEqual(first["commit"], self.git("rev-parse", "main", cwd=self.remote))

    def test_organize_errors(self):
        payload = dict(talk=TALK, agent="claude")
        self.assertEqual(self.request("POST", "/organize", payload)[0], 400)
        self.ask()
        for answer in ("not JSON", '{"note": 7, "vocab": []}', '{"note": "x", "vocab": [null]}'):
            self.organize_reply = answer
            status, _, body = self.request("POST", "/organize", payload)
            self.assertEqual(status, 502, body)
            self.assertIn("error", body)
        self.assertFalse((self.notes / "talks").exists())

    def test_push_failure_reports_stderr(self):
        self.ask()
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'test push rejected' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o700)
        status, _, body = self.request("POST", "/organize", dict(talk=TALK, agent="claude"))
        self.assertEqual(status, 502)
        self.assertIn("test push rejected", body["error"])

    def test_invalid_keys_and_requests(self):
        for key in ("../outside", "acl-2024-../../outside", "NeurIPS-2024-123", "acl-24-id", "acl-2024-id\n", ""):
            with self.subTest(key=key):
                for path in ("/ask", "/organize"):
                    status, _, _ = self.request("POST", path, dict(talk=dict(TALK, key=key), question="x", agent="claude"))
                    self.assertEqual(status, 400)
                self.assertEqual(self.request("GET", "/chat/" + quote(key, safe=""))[0], 400)
        for body in ([], {}, dict(talk=TALK, agent="other"), dict(talk=TALK, agent="claude", question="x", t="bad")):
            self.assertEqual(self.request("POST", "/ask", body)[0], 400)
        self.assertFalse(self.calls)

    def test_runner_failure_retains_user_message(self):
        for name, option in (("claude", {"is_error": True}), ("codex", {"fail": True})):
            self.options[name] = option
            status, _, body = self.request("POST", "/ask", dict(talk=TALK, agent=name, question="Question"))
            self.assertEqual(status, 502, body)
        messages = self.request("GET", "/chat/" + TALK["key"])[2]["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "user"])
        self.assertFalse((self.state / "sessions.json").exists())

    def test_concurrent_asks_on_same_talk_resume(self):
        self.options["claude"]["delay"] = 0.1
        with ThreadPoolExecutor(max_workers=2) as executor:
            replies = list(executor.map(lambda _: self.ask(), range(2)))
        self.assertEqual([call["session"] for call in self.calls], [None, "claude-chat"])
        self.assertEqual(sorted(len(reply["messages"]) for reply in replies), [2, 4])


class CommandTests(unittest.TestCase):
    def test_exact_cli_commands(self):
        claude = ["claude", "-p", "--output-format", "json", "--allowedTools", "WebFetch WebSearch",
                  "--disallowedTools", "Bash Edit Write NotebookEdit"]
        self.assertEqual(agent.claude_command("prompt", None, "out"), claude + ["--", "prompt"])
        self.assertEqual(agent.claude_command("prompt", "sid", "out"), claude + ["--resume", "sid", "--", "prompt"])
        self.assertEqual(agent.codex_command("prompt", None, "out"),
                         ["codex", "--search", "exec", "--json", "--skip-git-repo-check", "-s", "read-only", "-o", "out", "prompt"])
        self.assertEqual(agent.codex_command("prompt", "sid", "out"),
                         ["codex", "--search", "exec", "resume", "sid", "--json", "--skip-git-repo-check", "-c",
                          'sandbox_mode="read-only"', "-o", "out", "prompt"])

    def test_run_agent_protects_leading_dash_prompts(self):
        outputs = {
            "claude": json.dumps(dict(result="answer", session_id="sid")),
            "codex": '\n'.join(json.dumps(event) for event in [
                dict(type="thread.started", thread_id="sid"),
                dict(type="item.completed", item=dict(type="agent_message", text="answer")),
            ]),
        }
        with tempfile.TemporaryDirectory() as work:
            for name, stdout in outputs.items():
                for session in (None, "sid"):
                    for prompt, expected in (("--help", " --help"), ("-", " -"),
                                             (" --help", " --help"), ("prompt", "prompt")):
                        with self.subTest(agent=name, session=session, prompt=prompt):
                            result = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
                            with patch.object(agent.subprocess, "run", return_value=result) as runner:
                                self.assertEqual(agent.run_agent(name, prompt, session, work), ("answer", "sid"))
                            self.assertEqual(runner.call_args.args[0][-1], expected)


if __name__ == "__main__":
    unittest.main()
