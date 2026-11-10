#!/usr/bin/env python3
"""Build the talk-video index for NeurIPS / ICML / ICLR.

Sources (all official, all public):
  * <conf>.cc/static/virtual/data/<conf>-<year>-orals-posters.json  (2020+; papers + abstracts)
  * <conf>.cc/virtual/<year>/events/<kind>  listing pages (every year the site has;
    gives orals, tutorials, workshops, invited talks, ... including pre-2020 years)
  * each event's page on the virtual site, visited once, to find the video it embeds or links:
      SlidesLive presentation ids, YouTube (incl. videoken wrappers), Vimeo, Facebook.

We never download or re-host video files. The site embeds the official players (or links out),
so the conference's own access rules still apply.

Everything is cached and resumable:
  .cache/raw/                     downloaded metadata JSON (not committed)
  data/pages/<conf>-<year>.json   per-event scrape result (committed; makes reruns incremental)

Usage:
  python scraper/build.py                                  # everything
  python scraper/build.py --conf iclr --year 2024
  python scraper/build.py --budget 3000                    # fetch at most N event pages this run
"""
import argparse
import ast
import concurrent.futures as cf
import datetime as dt
import html as htmllib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(ROOT, ".cache", "raw")

# conferences that run on the shared "virtual site" platform:
# key: (display name, first year with event pages, host, field)
CONFS = {
    "neurips": ("NeurIPS", 2013, "neurips.cc", "ml"),
    "icml": ("ICML", 2017, "icml.cc", "ml"),
    "iclr": ("ICLR", 2018, "iclr.cc", "ml"),
    "cvpr": ("CVPR", 2023, "cvpr.thecvf.com", "cv"),
    "iccv": ("ICCV", 2025, "iccv.thecvf.com", "cv"),
    "eccv": ("ECCV", 2024, "eccv.ecva.net", "cv"),
}


def host(conf):
    return "https://" + CONFS[conf][2]
JSON_FIRST_YEAR = 2020
LIST_KINDS = ["oral", "spotlight", "tutorial", "workshop", "invited", "invited-talk", "keynote",
              "Competition", "competition", "panel", "affinity-workshop", "expo-talk-panel"]
UA = "conf-video-index/1.0 (open-source paper-talk index; polite crawler)"

PID_RE = re.compile(r"presentationId:\s*'(\d+)'")
SL_LINK_RE = re.compile(r"slideslive\.com/(\d{8})")
YT_RE = re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=)|youtu\.be/|videoken\.com/embed/)([A-Za-z0-9_-]{11})")
VIMEO_RE = re.compile(r"vimeo\.com/(?:video/)?(\d{6,})")
FB_RE = re.compile(r"(https://www\.facebook\.com/[A-Za-z0-9_.-]+/videos/(?:[^\"'\s<]*/)?\d+)")
LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
ABS_RE = re.compile(r'id="abstract[^"]*"[^>]*>(.*?)</div>', re.S)
TAG_RE = re.compile(r"<[^>]+>")

KIND_NAMES = {"oral": "Oral", "spotlight": "Spotlight", "poster": "Poster", "tutorial": "Tutorial",
              "workshop": "Workshop", "invited": "Invited Talk", "invited-talk": "Invited Talk",
              "keynote": "Invited Talk", "competition": "Competition", "panel": "Panel",
              "affinity-workshop": "Workshop", "expo-talk-panel": "Expo"}


def kind_of(seg):
    return KIND_NAMES.get(seg.lower(), seg.replace("-", " ").title())


def http_get(url, timeout=60, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410) or attempt == tries - 1:
                return e.code, b""
        except Exception:
            if attempt == tries - 1:
                return 0, b""
        time.sleep(2 ** attempt * 2)
    return 0, b""


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj, compact=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def as_list(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.startswith("["):
        try:
            return ast.literal_eval(v)
        except Exception:
            return []
    return []


def clean(v):
    return None if v in (None, "None", "") else v


def text(s):
    return re.sub(r"\s+", " ", htmllib.unescape(TAG_RE.sub(" ", s or ""))).strip()


# ---------------------------------------------------------------- event lists
def events_from_json(conf, year, refresh):
    if year < JSON_FIRST_YEAR:
        return {}
    raw_path = os.path.join(RAW, f"{conf}-{year}.json")
    if refresh or not os.path.exists(raw_path):
        code, body = http_get(f"{host(conf)}/static/virtual/data/{conf}-{year}-orals-posters.json",
                              timeout=180)
        if code != 200 or not body:
            return {}
        os.makedirs(RAW, exist_ok=True)
        with open(raw_path, "wb") as f:
            f.write(body)
    d = load_json(raw_path, None)
    if d is None:
        return {}
    rows = d["results"] if isinstance(d, dict) else d

    out = {}
    for r in rows:
        pid = str(r.get("id"))
        etype = (clean(r.get("eventtype")) or clean(r.get("event_type")) or "Poster").strip()
        vurl = clean(r.get("virtualsite_url")) or f"/virtual/{year}/{etype.lower()}/{pid}"
        openreview, pdf = clean(r.get("paper_url")), clean(r.get("paper_pdf_url"))
        for m in as_list(r.get("eventmedia")):
            if not isinstance(m, dict):
                continue
            uri = m.get("uri") or ""
            if not openreview and "openreview.net" in uri:
                openreview = uri
            if not pdf and m.get("type") == "PDF" and str(m.get("name", "")).startswith("Paper"):
                pdf = uri if uri.startswith("http") else f"{host(conf)}{uri}"
        decision = (clean(r.get("decision")) or "").lower()
        low = etype.lower()
        kind = ("Oral" if "oral" in low or "oral" in decision else
                "Spotlight" if "spotlight" in low or "spotlight" in decision else "Poster")
        out[pid] = {
            "id": pid, "key": clean(r.get("uid")) or pid,
            "t": (r.get("name") or "").strip(),
            "a": [a.get("fullname", "") for a in as_list(r.get("authors")) if isinstance(a, dict)],
            "k": kind, "tp": clean(r.get("topic")) or "",
            "abs": (clean(r.get("abstract")) or "").strip(),
            "u": f"{host(conf)}{vurl}", "or": openreview, "pdf": pdf, "proj": clean(r.get("url")),
        }
    return out


def events_from_listings(conf, year):
    """Return {id: {"id", "u", "k"}} for every event linked from the year's listing pages."""
    found = {}
    link_re = re.compile(rf'href="(/virtual/{year}/([A-Za-z-]+)/(\d+))"')
    for kind in LIST_KINDS + ["poster"]:
        code, body = http_get(f"{host(conf)}/virtual/{year}/events/{kind}", timeout=90)
        if code != 200:
            continue
        for path, seg, eid in link_re.findall(body.decode("utf-8", "replace")):
            if seg in ("events", "search", "session", "day", "papers", "calendar"):
                continue
            if seg == "poster" and year >= JSON_FIRST_YEAR:
                continue  # 2020+ posters come from the JSON with richer metadata
            found.setdefault(eid, {"id": eid, "u": f"{host(conf)}{path}", "k": kind_of(seg)})
        time.sleep(0.3)
    return found


# ---------------------------------------------------------------- page scrape
def parse_page(html, conf):
    out = {}
    vids = []
    for pid in PID_RE.findall(html) + SL_LINK_RE.findall(html):
        vids.append(["sl", pid])
    for y in YT_RE.findall(html):
        vids.append(["yt", y])
    for v in VIMEO_RE.findall(html):
        vids.append(["vm", v])
    for f in FB_RE.findall(html):
        vids.append(["fb", f])
    seen, uniq = set(), []
    for v in vids:
        if tuple(v) not in seen:
            seen.add(tuple(v))
            uniq.append(v)
    if uniq:
        out["vs"] = uniq[:40]

    for block in LDJSON_RE.findall(html):
        try:
            ld = json.loads(block)
        except ValueError:
            continue
        if not isinstance(ld, dict):
            continue
        if ld.get("name"):
            out["t"] = text(ld["name"])
        authors = ld.get("author") or []
        if isinstance(authors, dict):
            authors = [authors]
        names = [a.get("name") for a in authors if isinstance(a, dict) and a.get("name")]
        if names:
            out["a"] = names
        video = ld.get("video")
        if isinstance(video, dict) and isinstance(video.get("thumbnailUrl"), str):
            out["th"] = video["thumbnailUrl"]
        break
    if "t" not in out:
        m = TITLE_RE.search(html)
        if m:
            t = text(m.group(1))
            # "<CONF> <Kind> Actual title" -> "Actual title"
            t = re.sub(rf"^(?:{CONFS[conf][0]}|NIPS)\s+(?:Oral|Poster|Spotlight|Tutorial|Workshop|"
                       r"Invited Talk|Keynote|Competition|Panel|Talk|Expo[^ ]*)?\s*", "", t, flags=re.I)
            out["t"] = t
    m = ABS_RE.search(html)
    if m:
        out["abs"] = text(m.group(1))[:4000]
    return out


def scrape(url, conf):
    code, body = http_get(url, timeout=60)
    if code != 200:
        return {"s": code}
    res = parse_page(body.decode("utf-8", "replace"), conf)
    res["s"] = 200
    return res


def scrape_all(conf, year, events, budget, workers, delay, recheck_days):
    cache_path = os.path.join(DATA, "pages", f"{conf}-{year}.json")
    cache = load_json(cache_path, {})
    now = time.time()
    # past years' missing videos almost never appear; re-check them rarely
    recheck = recheck_days * 86400 * (1 if year >= dt.date.today().year - 1 else 12)
    todo = []
    for e in events.values():
        c = cache.get(e["id"])
        if c and (c.get("vs") or now - c.get("ts", 0) < recheck):
            continue
        todo.append(e)
    todo = todo[: max(0, budget[0])]
    if not todo:
        return cache, 0

    lock = threading.Lock()
    done = [0]

    def work(e):
        res = scrape(e["u"], conf)
        if e.get("t"):  # metadata already comes from the JSON; cache only what the page adds
            for k in ("t", "a", "abs"):
                res.pop(k, None)
        time.sleep(delay)
        res["ts"] = int(time.time())
        with lock:
            cache[e["id"]] = res
            done[0] += 1
            if done[0] % 200 == 0:
                save_json(cache_path, cache)
                print(f"  {conf}-{year}: {done[0]}/{len(todo)} pages", flush=True)

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, todo))
    save_json(cache_path, cache)
    budget[0] -= len(todo)
    return cache, len(todo)


# ---------------------------------------------------------------- output
ORDER = {"Oral": 0, "Invited Talk": 1, "Tutorial": 2, "Spotlight": 3, "Workshop": 4, "Poster": 5}


def write_outputs(conf, year, events, cache):
    rows, abstracts, by_key = [], {}, {}
    for e in events.values():
        c = cache.get(e["id"], {})
        if c.get("s") not in (None, 200) and not e.get("t"):
            continue  # dead link with no metadata
        title = e.get("t") or c.get("t") or ""
        if not title:
            continue
        row = {"id": e["id"], "t": title, "a": e.get("a") or c.get("a") or [], "k": e["k"], "u": e["u"]}
        for k in ("tp", "or", "pdf", "proj"):
            if e.get(k):
                row[k] = e[k]
        if c.get("vs"):
            row["vs"] = c["vs"]
        if c.get("th"):
            row["th"] = c["th"]
        ab = e.get("abs") or c.get("abs")
        if ab:
            abstracts[e["id"]] = ab
        # the same paper can be listed as both oral and poster: keep one row, prefer the one with video/oral
        key = e.get("key") or ("t:" + title.lower())
        prev = by_key.get(key)
        if prev is not None:
            better = (bool(row.get("vs")), -ORDER.get(row["k"], 9)) > (bool(prev.get("vs")), -ORDER.get(prev["k"], 9))
            if better:
                rows[rows.index(prev)] = row
                by_key[key] = row
            continue
        by_key[key] = row
        rows.append(row)
    rows.sort(key=lambda r: (not r.get("vs"), ORDER.get(r["k"], 9), r["t"].lower()))
    save_json(os.path.join(DATA, "papers", f"{conf}-{year}.json"), rows)
    save_json(os.path.join(DATA, "abstracts", f"{conf}-{year}.json"), abstracts)
    return len(rows), sum(1 for r in rows if r.get("vs"))


def all_conf_meta():
    import acl
    meta = {k: (v[0], v[3]) for k, v in CONFS.items()}
    meta.update({k: (v[0], "nlp") for k, v in acl.VENUES.items()})
    return meta


def rebuild_manifest():
    """Derive the manifest from data/papers/*.json, so concurrent or partial runs never lose entries."""
    meta = all_conf_meta()
    items = []
    pdir = os.path.join(DATA, "papers")
    for fn in sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []:
        m = re.match(r"([a-z]+)-(\d{4})\.json$", fn)
        if not m or m.group(1) not in meta:
            continue
        rows = load_json(os.path.join(pdir, fn), [])
        if not rows:
            continue
        name, field = meta[m.group(1)]
        items.append({"conf": m.group(1), "name": name, "field": field, "year": int(m.group(2)),
                      "events": len(rows), "videos": sum(1 for r in rows if r.get("vs"))})
    items.sort(key=lambda m: (-m["year"], m["conf"]))
    save_json(os.path.join(DATA, "manifest.json"), {
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "confs": {k: {"name": v[0], "field": v[1]} for k, v in meta.items()},
        "items": items}, compact=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conf", action="append", help="conference key, e.g. neurips, cvpr, acl (repeatable)")
    ap.add_argument("--year", type=int, action="append")
    ap.add_argument("--budget", type=int, default=10**9, help="max event pages to fetch this run")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.5, help="seconds each worker waits between requests")
    ap.add_argument("--recheck-days", type=float, default=14)
    ap.add_argument("--refresh-meta", action="store_true")
    ap.add_argument("--manifest-only", action="store_true", help="just rebuild data/manifest.json")
    args = ap.parse_args()

    import acl
    if args.manifest_only:
        return rebuild_manifest()
    this_year = dt.date.today().year
    wanted = args.conf or (list(CONFS) + list(acl.VENUES))
    bad = [c for c in wanted if c not in CONFS and c not in acl.VENUES]
    if bad:
        sys.exit(f"unknown conference(s): {bad}")
    confs = [c for c in wanted if c in CONFS]
    acl_venues = [c for c in wanted if c in acl.VENUES]
    budget = [args.budget]

    # ACL-family data comes from the Anthology's XML (one file per venue-year, no page crawling): cheap, do first
    if acl_venues:
        acl.build(acl_venues, args.year, DATA, save_json)
        rebuild_manifest()

    # last year backwards first (a limited budget goes to the most wanted videos), the current year last:
    # its conferences may not have happened yet, so most of its pages have no video
    first = min([CONFS[c][1] for c in confs] or [this_year])
    years = args.year or list(range(this_year - 1, first - 1, -1)) + [this_year]
    for year in years:
        for conf in confs:
            if year < CONFS[conf][1]:
                continue
            fresh = args.refresh_meta or year >= this_year - 1
            events = events_from_json(conf, year, fresh)
            listing_cache = os.path.join(DATA, "listings", f"{conf}-{year}.json")
            listed = None if fresh else load_json(listing_cache, None)
            if listed is None:
                listed = events_from_listings(conf, year)
                save_json(listing_cache, listed)
            for eid, e in listed.items():
                events.setdefault(eid, dict(e))
            if not events:
                print(f"{conf}-{year}: nothing published (yet)")
                continue
            cache, fetched = scrape_all(conf, year, events, budget, args.workers, args.delay,
                                        args.recheck_days)
            n, nvid = write_outputs(conf, year, events, cache)
            if n == 0:
                continue
            checked = sum(1 for e in events if e in cache)
            print(f"{conf}-{year}: {n} events, {nvid} with video "
                  f"({checked}/{len(events)} pages checked, +{fetched})", flush=True)
            rebuild_manifest()
    rebuild_manifest()


if __name__ == "__main__":
    sys.exit(main())
