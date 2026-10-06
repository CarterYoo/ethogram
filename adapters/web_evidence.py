"""Web evidence (requests to websites as third parties recorded them) -> swarmgraph format.

For evidence bundles like Transluce's US/Canada government set: per section, `arquivo-captures.csv` (captures a web
archive made of URLs on request), `urlquery-reports.csv` + `urlquery-http.csv` (URL-scanner submissions and what the
scanner loaded), `sources.csv` + `responses/*` (bodies of some captured responses), `benchmark-excerpt.json` (a task the
requests appear to serve), `cited-wiki-*.txt` (wiki pages that carried links). Who made a request is not recorded:
requests whose query carries the same tag (a parameter such as `zz=oai...`) are grouped as one requester, named by
that tag; untagged requests form one unidentified requester per section. Nothing here says who the requesters are.

  action   a request: "capture via web archive: URL", "scan via URL scanner: URL", "link on a wiki page: URL"
  result   what came back (HTTP status, type, size; the body where the bundle has it); meta.error for 4xx/5xx/none
  message  context: the benchmark task of a section, a wiki page's text (author as the page names it)

    python3 adapters/web_evidence.py BUNDLE_DIR OUT_DIR
"""
import csv
import glob
import hashlib
import json
import os
import re
import sys
import time
from urllib.parse import parse_qsl, urlparse

TAGS = ("zz", "zzbulk", "prepnonce", "cb", "uniq", "nonce")


def tag_of(url):
    """('zz', 'oai') for ...&zz=oai123...; None when untagged"""
    q = parse_qsl(urlparse(url).query, keep_blank_values=True)
    for k, v in q:
        if k in TAGS and v:
            m = re.match(r"([A-Za-z]+)(\d+_)?", v)  # oai123 -> oai, g10_4_7 -> g10, hf889 -> hf
            return k, (m.group(1) + (m.group(2) or "").rstrip("_")) if m else "number"
    return None


def status_error(st):
    try:
        return int(st) >= 400 or int(st) == 0
    except (TypeError, ValueError):
        return True


def iso(t):
    t = (t or "").strip().replace(" ", "T")
    if not t:
        return None
    if t.endswith("Z") or "+" in t[10:]:
        return t if t.endswith("Z") else t
    return t + "Z"


class Bundle:
    def __init__(self, root):
        self.root = root
        self.events, self.actors = [], {}

    def actor(self, section, url):
        t = tag_of(url)
        if t:
            aid = f"tag:{t[0]}={t[1]}"
            label = f"requests tagged {t[0]}={t[1]}..."
        else:
            aid = f"untagged:{section}"
            label = f"untagged requests ({section.split('-', 1)[-1]})"
        self.actors.setdefault(aid, {"id": aid, "label": label, "kind": "agent",
                                     "role": "requester (identity not recorded)"})
        return aid

    def emit(self, **e):
        self.events.append(e)

    def bodies(self, section):
        """captured bodies by source URL"""
        out = {}
        p = os.path.join(self.root, section, "sources.csv")
        if not os.path.exists(p):
            return out
        for r in csv.DictReader(open(p)):
            f = r.get("file")
            if f and os.path.exists(os.path.join(self.root, section, f)):
                with open(os.path.join(self.root, section, f), errors="replace") as fh:
                    out[r["source_url"]] = fh.read(3000)
        return out

    def captures(self, section):
        p = os.path.join(self.root, section, "arquivo-captures.csv")
        if not os.path.exists(p):
            return
        bodies = self.bodies(section)
        for r in csv.DictReader(open(p)):
            url = r["target_url"]
            ts = iso(r["captured_at_utc"])
            aid = self.actor(section, url)
            eid = f"cap:{section}:{r['record_number']}"
            host = urlparse(url).netloc.split(":")[0]
            self.emit(id=eid, ts=ts, actor=aid, kind="action", channel=host, url=r.get("archive_url"),
                      text=f"capture via web archive: {url}",
                      meta={"tool": "web archive capture", "section": section})
            st = r.get("indexed_http_status") or r.get("reviewed_origin_status")
            body = bodies.get(r.get("archive_url"), "")
            self.emit(id="res:" + eid, ts=ts, actor=aid, kind="result", reply_to=eid, channel=host,
                      text=f"HTTP {st} {r.get('mime', '')}" + (f": {body}" if body else ""),
                      meta={"tool": "web archive capture", "error": status_error(st), "status": st,
                            "origin_status": r.get("reviewed_origin_status"), "section": section})

    def sources_only(self, section, captured):
        """captured responses listed in sources.csv without a capture record (some sections hold only these)"""
        p = os.path.join(self.root, section, "sources.csv")
        if not os.path.exists(p):
            return
        for k, r in enumerate(csv.DictReader(open(p))):
            src = r.get("source_url") or ""
            if not src or src in captured:
                continue
            m = re.search(r"/(?:wayback|web)/(\d{8,14})(?:id_)?/(https?://.+)$", src)
            url = m.group(2) if m else src
            ts = iso(r.get("captured_at_utc"))
            aid = self.actor(section, url)
            eid = f"src:{section}:{k}"
            host = urlparse(url).netloc.split(":")[0]
            via = "web archive capture" if m else "fetch"
            self.emit(id=eid, ts=ts, actor=aid, kind="action", channel=host, url=src,
                      text=f"capture via web archive: {url}", meta={"tool": via, "section": section})
            body = ""
            f = r.get("file")
            if f and os.path.exists(os.path.join(self.root, section, f)):
                with open(os.path.join(self.root, section, f), errors="replace") as fh:
                    body = fh.read(3000)
            st = r.get("http_status")
            self.emit(id="res:" + eid, ts=ts, actor=aid, kind="result", reply_to=eid, channel=host,
                      text=f"HTTP {st}" + (f": {body}" if body else ""),
                      meta={"tool": via, "error": status_error(st), "status": st, "section": section})

    def scans(self, section):
        rp, hp = (os.path.join(self.root, section, f) for f in ("urlquery-reports.csv", "urlquery-http.csv"))
        if not os.path.exists(rp):
            return
        loads = {}
        if os.path.exists(hp):
            for h in csv.DictReader(open(hp)):
                loads.setdefault(h["report_id"], []).append(h)
        for r in csv.DictReader(open(rp)):
            url = r["submitted_url"]
            aid = self.actor(section, url)
            eid = f"scan:{r['report_id']}"
            host = urlparse(url).netloc.split(":")[0]
            self.emit(id=eid, ts=iso(r["report_at_utc"]), actor=aid, kind="action", channel=host, url=r["report_url"],
                      text=f"scan via URL scanner: {url}", meta={"tool": "URL scanner", "section": section})
            hs = loads.get(r["report_id"], [])
            stat = {}
            for h in hs:
                stat[h["http_status"]] = stat.get(h["http_status"], 0) + 1
            first = next((h for h in hs if urlparse(h["url"]).netloc == urlparse(url).netloc), hs[0] if hs else None)
            main = (first or {}).get("http_status")
            self.emit(id="res:" + eid, ts=iso(r["report_at_utc"]), actor=aid, kind="result", reply_to=eid, channel=host,
                      text=f"scanner loaded {len(hs)} resources (statuses {stat}); the page itself: HTTP {main}",
                      meta={"tool": "URL scanner", "error": status_error(main), "status": main, "section": section})

    def context(self, section, first_ts):
        b = os.path.join(self.root, section, "benchmark-excerpt.json")
        if os.path.exists(b) and first_ts:
            x = json.load(open(b))
            self.actors.setdefault("benchmark", {"id": "benchmark", "label": "benchmark task", "kind": "system"})
            self.emit(id=f"task:{section}", ts=first_ts, actor="benchmark", kind="message", channel="task",
                      text=f"task ({x.get('problem_category', '')}, {x.get('source', '')}): {x.get('problem', '')}",
                      meta={"section": section, "answer_type": x.get("answer_type")})
        for f in sorted(glob.glob(os.path.join(self.root, section, "cited-wiki-*.txt"))):
            txt = open(f, errors="replace").read()
            lines = txt.splitlines()
            m = re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", lines[1] if len(lines) > 1 else "")
            ts = iso(m.group(0)) if m else first_ts
            who = lines[2].strip() if len(lines) > 2 and m else "unknown"
            aid = "wiki:" + hashlib.sha1(who.encode()).hexdigest()[:8]
            self.actors.setdefault(aid, {"id": aid, "label": f"wiki author ({who})", "kind": "agent",
                                         "role": "wrote a wiki page with links (identity as the page names it)"})
            self.emit(id=f"wiki:{section}:{os.path.basename(f)}", ts=ts or first_ts, actor=aid, kind="message",
                      channel="wiki", text="wiki page: " + " ".join(txt.split())[:4000], meta={"section": section})

    def run(self):
        for d in sorted(os.listdir(self.root)):
            if not os.path.isdir(os.path.join(self.root, d)):
                continue
            n0 = len(self.events)
            self.captures(d)
            self.sources_only(d, {e.get("url") for e in self.events[n0:]})
            self.scans(d)
            ts = sorted(e["ts"] for e in self.events[n0:] if e.get("ts"))
            self.context(d, ts[0] if ts else None)


def main(root, out):
    t0 = time.time()
    b = Bundle(root)
    b.run()
    os.makedirs(out, exist_ok=True)
    ev = sorted((e for e in b.events if e.get("ts")), key=lambda e: (e["ts"], e["id"]))
    with open(os.path.join(out, "events.jsonl"), "w") as f:
        for e in ev:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    with open(os.path.join(out, "actors.jsonl"), "w") as f:
        for a in b.actors.values():
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
    sections = {}
    for e in ev:
        s = (e.get("meta") or {}).get("section")
        if s:
            lo, hi = sections.get(s, (e["ts"], e["ts"]))
            sections[s] = (min(lo, e["ts"]), max(hi, e["ts"]))
    meta = {"name": "Requests to US and Canadian government websites, as third parties recorded them",
            "description": ("Evidence bundle: captures a web archive made of government URLs on request, URL-scanner "
                            "submissions, bodies of some responses, the benchmark tasks the requests appear to serve, "
                            "and wiki pages that carried links. Who made the requests is not recorded: requests whose "
                            "query carries the same tag are grouped as one requester, named by the tag; untagged "
                            "requests form one requester per section. Sections: " +
                            ", ".join(f"{s} ({lo[:10]} to {hi[:10]})" for s, (lo, hi) in sorted(sections.items()))),
            "notes": "action = a request made through a third party (tool = which); result = HTTP status and type, "
                     "with the body where the bundle has it; meta.error = 4xx/5xx or no status.",
            "lead_kinds": ["action", "message"]}
    with open(os.path.join(out, "dataset.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f"{len(ev):,} events, {len(b.actors)} actors, {len(sections)} sections in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

