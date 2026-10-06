"""Other boards found by outside investigators (the "Other sites" export of the wiki edit logs) -> swarmgraph format,
added to an existing dataset (the wiki edit logs) as one combined dataset.

    python3 adapters/other_boards.py DOWNLOAD_DIR WIKI_DATASET_DIR OUT_DIR

Sources
- records.jsonl: texts selected from agent-related posts on many sites (wikis, URL shorteners, paste sites), each with
  one or more origins (site, title, url, a date as the source wrote it, kind). Origins on the wikis already in the
  wiki dataset (same site, revision kinds) are duplicates and are dropped; a record with no other origin is left out.
  The rest become events of kind 'message' on channel '<site>/<title>' by actor '<site>': authorship is not recorded,
  so a site stands for its unknown authors (as request tags do in the web-evidence adapter). Time = the first
  parseable origin date (ISO date or time, epoch seconds, or YYYYMMDDHHMMSS), else the time its link first appears in
  a dated post of the wiki dataset (meta.date_basis says which); records with neither are counted in the dataset
  notes and left out.
- other-wikis.json: revisions of eight pages on three more wikis -> 'message' events by actor '<wiki>' on channel
  '<wiki>/<page>' (the first half of the address is kept in meta; prefixes are shared, so they identify no one).
- shortener-logs.json: links made on a URL shortener -> 'action' events by actor '<site>': made a short link to a URL,
  with its title; meta keeps clicks and the address prefix.
- links.jsonl, site-coverage.csv, coverage-gaps.csv: counted in the dataset notes, not turned into events.
"""
import collections
import csv
import datetime as dt
import json
import os
import re
import shutil
import sys

INDEXED = {"prowiki.org/dse", "wikiservice.at/probier", "wikiservice.at/fractal", "wikiservice.at/dorfwiki"}
DUPLICATE_KINDS = {"revision_addition", "prior_revision", "current_page", "public_revision"}
ISO = re.compile(r"(20\d\d-\d\d-\d\d)(?:[ T](\d\d:\d\d(?::\d\d)?))?")
MAX_TEXT = 6000


def when(literal):
    """the first date in a source's own date text, as an ISO timestamp (None if there is none)"""
    s = (literal or "").strip()
    m = ISO.search(s)
    if m:
        t = m.group(2) or "00:00:00"
        return f"{m.group(1)}T{t if len(t) == 8 else t + ':00'}Z"
    if re.fullmatch(r"\d{10}", s):
        return dt.datetime.fromtimestamp(int(s), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if re.fullmatch(r"\d{14}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}T{s[8:10]}:{s[10:12]}:{s[12:14]}Z"
    return None


URL = re.compile(r"https?://[^\s'\"<>\])]+")


def first_seen(wiki):
    """url -> the time it first appears in a dated post of the wiki dataset"""
    out = {}
    with open(os.path.join(wiki, "events.jsonl"), encoding="utf-8") as f:
        evs = sorted((json.loads(line) for line in f), key=lambda e: e["ts"])
    for e in evs:
        for u in URL.findall(e.get("text") or ""):
            out.setdefault(u.rstrip(".,;:").rstrip("/").lower(), e["ts"])
    return out


def records(raw, seen=None, undated=None):
    out, skipped = [], collections.Counter()
    undated = undated if undated is not None else []
    seen = seen or {}
    with open(os.path.join(raw, "records.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            keep = [o for o in r["origins"] if not (o["site"] in INDEXED and o["kind"] in DUPLICATE_KINDS)]
            if not keep:
                skipped["already in the wiki dataset"] += 1
                continue
            ts = next((when(o["source_date_literal"]) for o in keep if when(o["source_date_literal"])), None)
            basis = "source date"
            if not ts:  # a link with no date of its own: when it first appeared in a dated post, if it did
                ts = next((seen[u] for o in keep for u in [o["url"].rstrip("/").lower()] if u in seen), None)
                basis = "first appearance in a dated post"
            if not ts:
                skipped["no date"] += 1
                undated.append({"site": keep[0]["site"], "kind": keep[0]["kind"], "title": keep[0].get("title") or "",
                                "text": r["text"][:1500] if not r.get("body_withheld") else ""})
                continue
            o = keep[0]
            title = (o.get("title") or "").strip()
            text = r["text"] if not r.get("body_withheld") else "(body withheld by the export)"
            out.append({"id": f"rec:{r['id'][:20]}", "ts": ts, "actor": o["site"], "kind": "message",
                        "text": ((title + "\n") if title else "") + text[:MAX_TEXT],
                        "channel": f"{o['site']}/{title[:60]}" if title else o["site"],
                        "meta": {"source_kind": o["kind"], "date_basis": basis, "origins": len(r["origins"]), "selection": r.get("selection_basis"),
                                 "text_changed_on_host": r.get("hosting_text_changed")}})
    return out, skipped


def other_wikis(raw):
    d = json.load(open(os.path.join(raw, "other-wikis.json"), encoding="utf-8"))
    out = []
    for p in d["pages"]:
        for v in p["revisions"]:
            ts = when(v.get("time") or "")
            if not ts:
                continue
            text = v.get("added") if isinstance(v.get("added"), str) else "\n".join(v.get("added") or [])
            out.append({"id": f"ow:{p['page_key']}:{v['seq']}", "ts": ts, "actor": p["wiki"], "kind": "message",
                        "text": (text or "")[:MAX_TEXT], "channel": f"{p['wiki']}/{p['name']}",
                        "meta": {"ip16": v.get("ip16"), "time_grade": v.get("time_grade")}})
    return out


def shortener(raw):
    d = json.load(open(os.path.join(raw, "shortener-logs.json"), encoding="utf-8"))
    out = []
    for s in d["sites"]:
        for k, ln in enumerate(s["links"]):
            ts = when(ln.get("time") or "")
            if not ts:
                continue
            out.append({"id": f"sl:{s['site']}:{ln.get('keyword') or k}", "ts": ts, "actor": s["site"], "kind": "action",
                        "text": f"made a short link to {ln.get('url', '')}" + (f" ({ln['title']})" if ln.get("title") else ""),
                        "channel": s["site"], "meta": {"clicks": ln.get("clicks"), "ip16": ln.get("ip16")}})
    return out


def main(raw, wiki, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    undated = []
    rec, skipped = records(raw, first_seen(wiki), undated)
    with open(os.path.join(out_dir, "undated.jsonl"), "w", encoding="utf-8") as f:  # kept apart: no time to place them at
        for u in undated:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")
    ow, sl = other_wikis(raw), shortener(raw)
    new = sorted(rec + ow + sl, key=lambda e: e["ts"])
    shutil.copy(os.path.join(wiki, "events.jsonl"), os.path.join(out_dir, "events.jsonl"))
    with open(os.path.join(out_dir, "events.jsonl"), "a", encoding="utf-8") as f:
        for e in new:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    shutil.copy(os.path.join(wiki, "actors.jsonl"), os.path.join(out_dir, "actors.jsonl"))
    sites = collections.Counter(e["actor"] for e in new)
    with open(os.path.join(out_dir, "actors.jsonl"), "a", encoding="utf-8") as f:
        for s in sorted(sites):
            f.write(json.dumps({"id": s, "label": s, "kind": "agent", "role": "a site standing for its unknown authors"}) + "\n")
    links = sum(1 for _ in open(os.path.join(raw, "links.jsonl"), encoding="utf-8"))
    cov = list(csv.DictReader(open(os.path.join(raw, "site-coverage.csv"), encoding="utf-8")))
    gaps = list(csv.DictReader(open(os.path.join(raw, "coverage-gaps.csv"), encoding="utf-8")))
    meta = json.load(open(os.path.join(wiki, "dataset.json"), encoding="utf-8"))
    meta["name"] = "wiki edit logs plus other boards (shorteners, paste sites, more wikis)"
    meta["description"] += (" Added: texts that outside investigators selected from other sites the agents used (URL "
                            "shorteners, paste sites, other wikis), short links made on one shortener, and revisions of "
                            "eight pages on three more wikis.")
    meta["notes"] += (f" Other boards: authorship is not recorded, so each site is one actor standing for its unknown "
                      f"authors. {len(rec)} records kept, {skipped['already in the wiki dataset']} left out as already in "
                      f"the wiki, {skipped['no date']} left out for lack of a date; {len(ow)} revisions from three more "
                      f"wikis; {len(sl)} short links. Also exported but not events: {links} links found in agent-related "
                      f"text, coverage notes for {len(cov)} sites and gaps for {len(gaps)}.")
    json.dump(meta, open(os.path.join(out_dir, "dataset.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps({"records": len(rec), "skipped": dict(skipped), "other_wiki_revisions": len(ow), "short_links": len(sl),
                      "sites": len(sites), "by_site": sites.most_common(12)}))


if __name__ == "__main__":
    main(*sys.argv[1:4])
