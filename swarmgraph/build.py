"""Generic builder: canonical dataset directory → one SQLite index. No dataset-specific logic here."""
import bisect
import calendar
import collections
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta

from .format import load

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE actors(id TEXT PRIMARY KEY, label TEXT, kind TEXT, role TEXT, model TEXT, parent TEXT, lineage TEXT);
CREATE TABLE aliases(alias TEXT, actor_id TEXT);
CREATE TABLE segments(id INTEGER PRIMARY KEY, label TEXT, start TEXT, end TEXT);
CREATE TABLE events(rid INTEGER PRIMARY KEY, id TEXT UNIQUE, ts TEXT, actor TEXT, kind TEXT, text TEXT,
                    channel TEXT, reply_to TEXT, url TEXT, segment_id INTEGER, meta TEXT);
CREATE VIRTUAL TABLE events_fts USING fts5(text, content='events', content_rowid='rid');
-- One row per observed relation. type: addressed | replied | invoked | returned | read
-- method says how it was found (to_field, reply_field, at_mention, call_kind, return_kind, read_kind,
-- actor_parent, reply_window, llm_text = named as addressee by the LLM tagger); confidence reflects the method.
CREATE TABLE relations(id INTEGER PRIMARY KEY, type TEXT, src TEXT, dst TEXT, ts TEXT, segment_id INTEGER,
                       event_id TEXT, method TEXT, confidence REAL, answered_by TEXT, answers TEXT);
-- A node is one actor in one segment.
CREATE TABLE nodes(actor TEXT, segment_id INTEGER, first_ts TEXT, last_ts TEXT, n_events INTEGER,
                   n_out INTEGER, n_in INTEGER, self_report TEXT, summary TEXT, PRIMARY KEY(actor, segment_id));
"""

INDEXES = """
CREATE INDEX ix_ev_actor ON events(actor, ts);
CREATE INDEX ix_ev_ts ON events(ts);
CREATE INDEX ix_ev_seg ON events(segment_id, actor);
CREATE INDEX ix_rel_src ON relations(src, type, ts);
CREATE INDEX ix_rel_dst ON relations(dst, type, ts);
CREATE INDEX ix_rel_seg ON relations(segment_id, type);
CREATE INDEX ix_rel_event ON relations(event_id);
CREATE INDEX ix_alias ON aliases(alias);
"""

CONFIDENCE = {"to_field": 1.0, "call_kind": 1.0, "return_kind": 1.0, "read_kind": 1.0, "actor_parent": 1.0,
              "reply_field": 0.95, "at_mention": 0.9, "reply_window": 0.9, "llm_text": 0.7}


def epoch(ts):
    return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%d %H:%M:%S"))


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)  # stdout belongs to MCP/JSON


def mention_regex(aliases):
    """@alias, longest first (so 'GPT-5.1' beats 'GPT-5'); refuses partial words and version suffixes."""
    if not aliases:
        return None
    alt = "|".join(re.escape(a) for a in sorted(set(aliases), key=len, reverse=True))
    return re.compile(r"@(" + alt + r")(?!\w)(?!\.\d)", re.I)


def make_segments(events, phases):
    if phases:
        rows = sorted(phases, key=lambda p: p["start"])
        return [(i + 1, p["label"], p["start"], p["end"]) for i, p in enumerate(rows)]
    first, last = events[0]["ts"], events[-1]["ts"]
    span = (epoch(last) - epoch(first)) / 86400
    start = datetime.strptime(first[:10], "%Y-%m-%d")
    if span > 60:
        start -= timedelta(days=start.weekday())
        step, fmt = timedelta(days=7), "week of %Y-%m-%d"
    elif span > 3:
        step, fmt = timedelta(days=1), "%Y-%m-%d"
    else:
        start = datetime.strptime(first[:13], "%Y-%m-%d %H")
        step, fmt = timedelta(hours=1), "%Y-%m-%d %H:00"
    segs, cur, end = [], start, datetime.strptime(last[:19], "%Y-%m-%d %H:%M:%S")
    while cur <= end:
        nxt = cur + step
        segs.append((len(segs) + 1, cur.strftime(fmt), cur.strftime("%Y-%m-%d %H:%M:%S"), nxt.strftime("%Y-%m-%d %H:%M:%S")))
        cur = nxt
    return segs


def llm_addressees(db_path):
    """event id → names the LLM tagger found explicitly addressed in the text (from a previous `tag` run)."""
    path = db_path + ".work"
    if not os.path.exists(path):
        return {}
    w = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {r[0]: json.loads(r[1]) for r in w.execute("SELECT event_id, addressed_to FROM tags WHERE addressed_to <> '[]'")}
    except sqlite3.OperationalError:
        return {}
    finally:
        w.close()


def build(dataset_dir, db_path, reply_window=15 * 60):
    started = time.time()
    actors, events, phases, meta = load(dataset_dir)
    if not events:
        raise SystemExit("events.jsonl is empty")
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    log(f"{len(events):,} events, {len(actors):,} actors")

    con.executemany("INSERT INTO actors VALUES (?,?,?,?,?,?,?)",
                    [(a["id"], a["label"], a["kind"], a["role"], a["model"], a["parent"], a["lineage"]) for a in actors.values()])
    alias_rows = {}
    for a in actors.values():
        for name in {a["id"], a["label"], *a["aliases"]}:
            if name and len(name) >= 2:
                alias_rows.setdefault(name.lower(), set()).add(a["id"])
    alias_rows = {k: v.pop() for k, v in alias_rows.items() if len(v) == 1}  # ambiguous aliases are dropped
    con.executemany("INSERT INTO aliases VALUES (?,?)", alias_rows.items())

    segs = make_segments(events, phases)
    con.executemany("INSERT INTO segments VALUES (?,?,?,?)", segs)
    starts = [s[2] for s in segs]

    def seg_of(ts):
        i = bisect.bisect_right(starts, ts) - 1
        return segs[max(i, 0)][0]

    con.executemany("INSERT INTO events(id, ts, actor, kind, text, channel, reply_to, url, segment_id, meta) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(e["id"], e["ts"], e["actor"], e["kind"], e["text"], e["channel"], e["reply_to"], e["url"],
                      seg_of(e["ts"]), json.dumps(e["meta"]) if e["meta"] is not None else None) for e in events])

    log("relations")
    author = {e["id"]: e["actor"] for e in events}
    rx = mention_regex([k for k in alias_rows])
    inferred = llm_addressees(db_path)
    if inferred:
        log(f"{len(inferred):,} events with LLM-found addressees")
    rel = []  # [type, src, dst, ts, seg, event_id, method, conf, answered_by, answers]

    def add(t, src, dst, e, method):
        if src and dst and src != dst:
            rel.append([t, src, dst, e["ts"], seg_of(e["ts"]), e["id"], method, CONFIDENCE[method], None, None])

    for e in events:
        k, src, done = e["kind"], e["actor"], set()
        if k == "call":
            for d in e["to"]:
                add("invoked", src, d, e, "call_kind")
            continue
        if k == "return":
            for d in e["to"]:
                add("returned", src, d, e, "return_kind")
            continue
        if k == "read":
            add("read", src, author.get(e["reply_to"]), e, "read_kind")
            continue
        if k in ("self_report", "reasoning", "result"):
            continue  # private thinking and tool output address nobody, whatever names they contain
        for d in e["to"]:
            if d not in done:
                done.add(d)
                add("addressed", src, d, e, "to_field")
        parent_author = author.get(e["reply_to"]) if e["reply_to"] else None
        if parent_author and parent_author not in done:
            done.add(parent_author)
            add("addressed", src, parent_author, e, "reply_field")
        if rx:
            for m in rx.finditer(e["text"]):
                d = alias_rows.get(m.group(1).lower())
                if d and d not in done:
                    done.add(d)
                    add("addressed", src, d, e, "at_mention")
        for name in inferred.get(e["id"], ()):
            d = alias_rows.get(name.lower())
            if d and d not in done:
                done.add(d)
                add("addressed", src, d, e, "llm_text")

    first_event = {}
    for e in events:
        first_event.setdefault(e["actor"], e)
    for a in actors.values():
        if a["parent"] and a["id"] in first_event:
            add("invoked", a["parent"], a["id"], first_event[a["id"]], "actor_parent")

    # replied: B addresses A back within the window after A addressed B (one row per reply event).
    addressed = [r for r in rel if r[0] == "addressed"]
    for r in addressed:
        r.append(epoch(r[3]))
    by_pair = collections.defaultdict(list)
    for r in addressed:
        by_pair[(r[1], r[2])].append(r)
    pair_times = {k: [r[10] for r in v] for k, v in by_pair.items()}
    replies, counted = [], set()
    for r in addressed:
        back = (r[2], r[1])
        if back not in by_pair:
            continue
        i = bisect.bisect_right(pair_times[back], r[10])
        if i < len(by_pair[back]) and by_pair[back][i][10] - r[10] <= reply_window:
            rep = by_pair[back][i]
            r[8] = rep[5]
            if (rep[5], back) not in counted:
                counted.add((rep[5], back))
                replies.append(["replied", rep[1], rep[2], rep[3], rep[4], rep[5], "reply_window",
                                CONFIDENCE["reply_window"], None, r[5]])

    # Sanity signal: how often an addressed actor speaks at all within the window vs. at random times.
    times = collections.defaultdict(list)
    for e in events:
        times[e["actor"]].append(epoch(e["ts"]))

    def speaks(a, t):
        lst = times.get(a, [])
        i = bisect.bisect_right(lst, t)
        return i < len(lst) and lst[i] - t <= reply_window
    hit = sum(speaks(r[2], r[10]) for r in addressed)
    base = sum(speaks(r[2], r[10] + (7200 if k % 2 else -7200)) for k, r in enumerate(addressed))

    rows = [r[:10] for r in rel] + replies
    con.executemany("INSERT INTO relations(type, src, dst, ts, segment_id, event_id, method, confidence, answered_by, "
                    "answers) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

    log("nodes")
    con.executescript(INDEXES)
    con.execute("INSERT INTO events_fts(events_fts) VALUES ('rebuild')")
    build_nodes(con)

    counts = collections.Counter(r[0] for r in rows)
    methods = collections.Counter(r[6] for r in rows)
    info = {"name": meta.get("name") or os.path.basename(os.path.abspath(dataset_dir)),
            "description": meta.get("description", ""), "notes": meta.get("notes", ""),
            "lead_kinds": json.dumps(meta.get("lead_kinds") or []),
            "dataset_dir": os.path.abspath(dataset_dir), "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "segmenting": "phases.jsonl" if phases else "automatic", "reply_window_s": str(reply_window),
            "relations": json.dumps(counts), "methods": json.dumps(methods),
            "addressed_speaks_rate": f"{hit / max(1, len(addressed)):.3f}",
            "addressed_speaks_base_rate": f"{base / max(1, len(addressed)):.3f}"}
    con.executemany("INSERT INTO meta VALUES (?,?)", info.items())
    con.commit()
    con.execute("VACUUM")
    con.close()
    log(f"done in {time.time() - started:.0f}s → {db_path}; relations {dict(counts)}")
    return info


def build_nodes(con):
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT actor, segment_id, min(ts) f, max(ts) l, count(*) n FROM events "
                       "GROUP BY actor, segment_id").fetchall()
    labels = {r["id"]: r["label"] for r in con.execute("SELECT id, label FROM actors")}
    out = []
    for r in rows:
        a, s = r["actor"], r["segment_id"]
        n_out = con.execute("SELECT count(*) FROM relations WHERE src=? AND segment_id=? AND type='addressed'", (a, s)).fetchone()[0]
        n_in = con.execute("SELECT count(*) FROM relations WHERE dst=? AND segment_id=? AND type='addressed'", (a, s)).fetchone()[0]
        sr = con.execute("SELECT id FROM events WHERE actor=? AND segment_id=? AND kind='self_report' ORDER BY ts DESC LIMIT 1",
                         (a, s)).fetchone()
        peers = lambda me, other: ", ".join(f"{labels.get(x[0], x[0])} {x[1]}" for x in con.execute(
            f"SELECT {other}, count(*) n FROM relations WHERE {me}=? AND segment_id=? AND type='addressed' "
            f"GROUP BY {other} ORDER BY n DESC LIMIT 5", (a, s)))
        answered = con.execute("SELECT count(answered_by) FROM relations WHERE src=? AND segment_id=? AND type='addressed'",
                               (a, s)).fetchone()[0]
        summary = [f"{r['n']} events ({r['f'][:16]} → {r['l'][:16]})",
                   f"addressed others {n_out}×" + (f" ({answered / n_out:.0%} got a reply)" if n_out else "")
                   + (f": {peers('src', 'dst')}" if n_out else ""),
                   f"addressed by others {n_in}×" + (f": {peers('dst', 'src')}" if n_in else "")]
        for t, label in (("invoked", "invoked"), ("returned", "returned results to")):
            x = con.execute(f"SELECT count(*) FROM relations WHERE src=? AND segment_id=? AND type=?", (a, s, t)).fetchone()[0]
            if x:
                summary.append(f"{label} others {x}×")
        out.append((a, s, r["f"], r["l"], r["n"], n_out, n_in, sr[0] if sr else None, "\n".join(summary)))
    con.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?)", out)

