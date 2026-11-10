"""ACL-family venues (ACL / EMNLP / NAACL / EACL / Findings) from the ACL Anthology.

The Anthology publishes one XML file per venue-year in github.com/acl-org/acl-anthology
(data/xml/). Each <paper> may carry <video href="..."/>: either an mp4 the Anthology hosts
(https://aclanthology.org/<file>.mp4) or a SlidesLive / Vimeo / YouTube link. No page crawling needed.
"""
import datetime as dt
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

# key: (display name, pre-2020 one-letter Anthology prefix or None, first year with videos)
VENUES = {
    "acl": ("ACL", "P", 2017),
    "emnlp": ("EMNLP", "D", 2016),
    "naacl": ("NAACL", "N", 2018),
    "eacl": ("EACL", "E", 2021),
    "findings": ("Findings", None, 2020),
}
XML_URL = "https://raw.githubusercontent.com/acl-org/acl-anthology/master/data/xml/{}.xml"
SITE = "https://aclanthology.org/"
UA = "conf-video-index/1.0 (open-source paper-talk index)"
KIND_ORDER = {"Long": 0, "Main": 0, "Short": 1, "Findings": 2, "Tutorial": 3, "Demo": 4,
              "Industry": 5, "Student": 6, "Paper": 7}


def _get(url):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:
            pass
        time.sleep(2 ** attempt * 2)
    return None


def _text(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def _kind(booktitle, volume_id):
    b = booktitle.lower()
    for key, name in (("findings", "Findings"), ("tutorial", "Tutorial"), ("demonstration", "Demo"),
                      ("system demo", "Demo"), ("industry", "Industry"), ("student", "Student"),
                      ("short", "Short"), ("long", "Long")):
        if key in b:
            return name
    return {"long": "Long", "short": "Short", "main": "Main", "1": "Long", "2": "Short"}.get(volume_id, "Paper")


def _video(href):
    href = href.strip()
    m = re.search(r"slideslive\.com/(\d{6,})", href)
    if m:
        return ["sl", m.group(1)]
    m = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{11})", href)
    if m:
        return ["yt", m.group(1)]
    m = re.search(r"vimeo\.com/(?:video/)?(\d{6,})", href)
    if m:
        return ["vm", m.group(1)]
    if href.lower().endswith(".mp4"):
        return ["mp4", href if href.startswith("http") else SITE + href]
    return None


def _xml_name(venue, year):
    prefix = VENUES[venue][1]
    if year >= 2020:
        return f"{year}.{venue}"
    return f"{prefix}{year % 100:02d}" if prefix else None


def parse(xml_bytes):
    root = ET.fromstring(xml_bytes)
    coll = root.get("id", "")
    rows, abstracts = [], {}
    for vol in root.iter("volume"):
        booktitle = _text(vol.find("meta/booktitle"))
        vid = vol.get("id", "")
        kind = _kind(booktitle, vid)
        for p in vol.iter("paper"):
            # Anthology id: new style "2024.acl-long.1", old style "P17-1001"
            pid = p.get("id", "")
            aid = _text(p.find("url"))
            if not aid and pid.isdigit():
                aid = f"{coll}-{vid}.{pid}" if "." in coll else f"{coll}-{vid}{int(pid):03d}"
            title = _text(p.find("title"))
            if not aid or not title or aid.startswith("http"):
                continue
            authors = []
            for a in p.findall("author"):
                name = (_text(a.find("first")) + " " + _text(a.find("last"))).strip()
                if name:
                    authors.append(name)
            row = {"id": aid, "t": title, "a": authors, "k": kind, "u": SITE + aid + "/",
                   "pdf": SITE + aid + ".pdf"}
            vids = [v for v in (_video(e.get("href", "")) for e in p.findall("video")) if v]
            if vids:
                row["vs"] = vids
            rows.append(row)
            ab = _text(p.find("abstract"))
            if ab:
                abstracts[aid] = ab
    rows.sort(key=lambda r: (not r.get("vs"), KIND_ORDER.get(r["k"], 9), r["t"].lower()))
    return rows, abstracts


def build(venues, years, data_dir, save_json):
    this_year = dt.date.today().year
    raw_dir = os.path.join(os.path.dirname(data_dir), ".cache", "raw")
    os.makedirs(raw_dir, exist_ok=True)
    for venue in venues:
        first = VENUES[venue][2]
        for year in (years or range(this_year, first - 1, -1)):
            if year < first:
                continue
            name = _xml_name(venue, year)
            if not name:
                continue
            path = os.path.join(raw_dir, f"anth-{name}.xml")
            if year >= this_year - 1 or not os.path.exists(path):
                body = _get(XML_URL.format(name))
                if body is None:
                    continue
                with open(path, "wb") as f:
                    f.write(body)
            with open(path, "rb") as f:
                rows, abstracts = parse(f.read())
            if not rows:
                continue
            save_json(os.path.join(data_dir, "papers", f"{venue}-{year}.json"), rows)
            save_json(os.path.join(data_dir, "abstracts", f"{venue}-{year}.json"), abstracts)
            print(f"{venue}-{year}: {len(rows)} papers, {sum(1 for r in rows if r.get('vs'))} with video",
                  flush=True)
