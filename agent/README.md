# Talks agent

Requires Python 3.10+, Git, and authenticated `claude` and/or `codex` commands on
the server's PATH. The notes directory must be a clone on branch `main` with an
upstream remote, Git author identity configured, and push access.

From the project root on the server:

```sh
nohup python3 -u agent/talks_agent.py \
  --notes-dir /ssd1/kaijing/ml-talks-notes \
  > "$HOME/talks-agent.log" 2>&1 &
```

The server binds only `127.0.0.1:8787`. On first start it creates and prints a
32-character token. Read it from `~/.local/share/talks-agent/token` (mode `600`),
or `<state-dir>/token` when using `--state-dir`. Enter it in the site's agent
connection settings; authenticated requests use `Authorization: Bearer <token>`.
Chat logs and session IDs stay in this state directory.

On your laptop, keep this SSH tunnel running:

```sh
ssh -N -L 8787:localhost:8787 <user>@<server>
```

Connect the site's notes panel to `http://localhost:8787`. You can check
`http://localhost:8787/health` without a token. The default allowed origins are
`https://mkj69.github.io`, `http://localhost:8000`, and `http://127.0.0.1:8000`.
Use `--allow-origin ORIGIN ...` to replace them, or `--port` to change the port.

Run the tests from the project root:

```sh
python3 -m unittest agent/test_talks_agent.py
```

Tests use fake CLI subprocesses and temporary notes/state directories, with a
local bare Git remote. No real model CLI calls or external network access occur.
