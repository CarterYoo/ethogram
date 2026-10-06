"""Event notes: the base layer of the summary structure. An LLM (Codex) writes one note per event: a verb-first
one-line summary, behaviour tags from a small dataset-independent vocabulary, whom the text addresses, which earlier
event it reacts to (`responds_to`) or repeats/continues (`continues`), what the actor claims, its stated goal.

Pointers are checked by code before saving (they must name an event the model was shown, earlier in time).
Notes live in the work store (`<index>.work`: tags, event_tags) so rebuilding the index keeps them; `addressed_to`
names become `addressed` relations with method `llm_text` on the next build.

    python3 -m swarmgraph --db INDEX tag [--workers 8] [--limit N] [--actor A B]
"""
import concurrent.futures
import json
import sys
import time

from . import query as Q
from .llm import Codex
from .store import open_work

VERSION = 2  # bump when the prompt or fields change; `tag` redoes older notes

VOCAB = {
    "task_work": "does substantive work on a task: writes content, collects or organises data, answers a question",
    "test_probe": "tries something out to see whether or how it works (test page, trial request, placeholder)",
    "retry_variant": "repeats an earlier attempt of its own with small changes (new name, format or parameters)",
    "workaround": "tries a different route after a failure, limit, block or removal",
    "coordinate": "tells other agents plans, status, timing, assignments or protocols",
    "instruct": "tells others what to do, or sets rules or conventions for others",
    "follow": "acts on another actor's instruction, convention, template or request",
    "report_result": "states an outcome or finding",
    "claim_success": "asserts that something worked or is done",
    "ask_help": "asks others for help, information or confirmation",
    "self_identify": "states who or what it is, or whom it works for",
    "build_on_others": "extends, fixes or reuses content made by another actor",
    "remove_others": "deletes, overwrites or reverts content made by another actor",
    "restore": "recreates or restores content after it was removed",
    "moderate": "enforces rules on others (deleting, warning, blocking, cleaning up)",
    "filler": "little or no content: repetitive, empty or placeholder output",
    "other": "none of the above fits (describe it in `other`)",
}
CONFIDENCE = ("low", "medium", "high")
# Display groups (colours in the story view only; metrics and cards use the tags themselves).
GROUPS = {"work": ("task_work", "build_on_others", "report_result"),
          "coordinate": ("coordinate", "instruct", "follow", "ask_help", "self_identify", "claim_success"),
          "trial": ("test_probe", "retry_variant", "filler"),
          "workaround": ("workaround", "restore"),
          "control": ("remove_others", "moderate")}
GROUP_OF = {t: g for g, ts in GROUPS.items() for t in ts}
FIELDS = {"summary": {"type": "string"},
          "tags": {"type": "array", "items": {"type": "string", "enum": list(VOCAB)}},
          "other": {"type": "string"},
          "addressed_to": {"type": "array", "items": {"type": "string"}},
          "responds_to": {"type": "string"}, "continues": {"type": "string"},
          "claims": {"type": "array", "items": {"type": "string"}},
          "stated_goal": {"type": "string"}, "signed_as": {"type": "string"},
          "confidence": {"type": "string", "enum": list(CONFIDENCE)}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", *FIELDS],
                               "properties": {"id": {"type": "string"}, **FIELDS}}}}}

PROMPT = """You write one note per event of a multi-agent log. Analysts (people and AI) use these notes to find and
verify behaviour, so they must be exact, neutral and checkable. Do not run commands and do not browse; use only the
text below.
Dataset: {dataset}

For EVERY event return one item:
- id: the event id, exactly as given
- summary: at most 20 words, starting with a verb, concrete about what and on what (e.g. "Adds 12 census data links
  to page X"). Describe what the event shows; no guesses about motives. Write actor ids exactly as given.
- tags: 1-3 behaviour labels from the vocabulary
- other: 2-4 words for an important behaviour the vocabulary misses, else ""
- addressed_to: actors the text explicitly addresses or answers ("X:", "to X", "@X", "your ... X"), as written;
  not actors merely mentioned. [] if none
- responds_to: id of an event shown in this event's `ctx` that it reacts to (answers, follows, undoes, recreates
  after, builds on), else ""
- continues: id of an EARLIER event of the same actor in this list that this event repeats or continues (the same
  attempt with changes, or the next step of the same task), else ""
- claims: assertions the actor makes about outcomes, status, itself or others ("CONFIRMED ...", "works now",
  "I am X"), each at most 12 words and close to the original wording; [] if none
- stated_goal: a goal the text itself states (at most 12 words), else ""
- signed_as: the name the text is signed with (e.g. "-- X"), else ""
- confidence: low | medium | high (for tags, responds_to and continues)

Vocabulary:
{vocab}

Events are grouped by actor in time order. `ctx` is computed by code: `reply` is the event this one is attached to
(reply target or previous version), `prev_in_channel` the preceding event by another actor in the same channel;
excerpts are cut. `flags` are true-valued fields from the source data.

Events:
{events}
"""


def _excerpt(text, n=160):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n] + " …"


def _context(con):
    """event id → {reply, prev_in_channel}: code-computed pointers (with short excerpts) the note may refer to."""
    info, ctx = {}, {}
    last = {}  # channel → (id, actor, ts)
    for r in con.execute("SELECT id, actor, ts, channel, text, reply_to FROM events ORDER BY channel, ts"):
        info[r["id"]] = (r["actor"], r["ts"], _excerpt(r["text"]))
    for r in con.execute("SELECT id, actor, ts, channel, reply_to FROM events ORDER BY channel, ts"):
        c = {}
        if r["reply_to"] and r["reply_to"] in info:
            a, _, ex = info[r["reply_to"]]
            c["reply"] = {"id": r["reply_to"], "actor": a, "excerpt": ex}
        prev = last.get(r["channel"]) if r["channel"] else None
        if prev and prev[1] != r["actor"] and prev[0] != r["reply_to"] and \
                abs(Q_epoch(r["ts"]) - Q_epoch(prev[2])) <= 6 * 3600:
            c["prev_in_channel"] = {"id": prev[0], "actor": prev[1], "excerpt": info[prev[0]][2]}
        if c:
            ctx[r["id"]] = c
        if r["channel"]:
            last[r["channel"]] = (r["id"], r["actor"], r["ts"])
    return ctx, info


def Q_epoch(ts):
    from .build import epoch
    return epoch(ts)


def _line(r, ctx, clip):
    text = r["text"] or ""
    if len(text) > clip:
        text = text[:clip] + f" …[+{len(text) - clip} chars]"
    meta = json.loads(r["meta"]) if r["meta"] else {}
    item = {"id": r["id"], "t": r["ts"][:16], "actor": r["actor"], "kind": r["kind"], "where": r["channel"]}
    flags = sorted(k for k, v in (meta.items() if isinstance(meta, dict) else []) if v is True)
    if flags:
        item["flags"] = flags
    if r["id"] in ctx:
        item["ctx"] = ctx[r["id"]]
    item["text"] = text
    return json.dumps(item, ensure_ascii=False)


def batches(con, done, actors=None, max_chars=30000, max_events=60, clip=900):
    """Yield lists of (event_id, prompt line, allowed pointer ids) grouped by actor in time order."""
    ctx, _ = _context(con)
    where, vals = "", []
    if actors:
        ids = [Q.resolve(con, a) for a in actors]
        where, vals = f" WHERE actor IN ({','.join('?' * len(ids))})", ids
    cur, size = [], 0
    for r in con.execute(f"SELECT id, ts, actor, kind, text, channel, meta FROM events{where} ORDER BY actor, ts", vals):
        if r["id"] in done:
            continue
        line = _line(r, ctx, clip)
        if cur and (size + len(line) > max_chars or len(cur) >= max_events):
            yield cur
            cur, size = [], 0
        cur.append((r["id"], line, {v["id"] for v in ctx.get(r["id"], {}).values()}))
        size += len(line)
    if cur:
        yield cur


def tag_batch(batch, dataset, effort):
    prompt = PROMPT.format(dataset=dataset, vocab="\n".join(f"- {k}: {v}" for k, v in VOCAB.items()),
                           events="\n".join(line for _, line, _ in batch))
    out, secs = Codex(effort=effort, timeout=900, retries=1).run(prompt, SCHEMA)
    return out["items"], secs


def save(work, items, batch, info, model):
    """Store notes for events of this batch; pointers that name events the model was not shown are dropped."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    allowed = {eid: ptrs for eid, _, ptrs in batch}
    ok = 0
    for it in items:
        eid = it["id"]
        if eid not in allowed:
            continue
        actor, ts, _ = info[eid]
        rt = it["responds_to"].strip()
        if rt and (rt not in allowed[eid] and rt not in allowed or rt == eid or info.get(rt, ("", "~"))[1] > ts):
            rt = ""
        co = it["continues"].strip()
        if co and (co not in allowed or co == eid or info[co][0] != actor or info[co][1] > ts):
            co = ""
        tags = [t for t in dict.fromkeys(it["tags"]) if t in VOCAB] or ["other"]
        work.execute("INSERT OR REPLACE INTO tags(event_id, tags, other, summary, addressed_to, signed_as, confidence, "
                     "model, created, responds_to, continues, claims, stated_goal, version) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (eid, json.dumps(tags), it["other"].strip(), it["summary"].strip(),
                      json.dumps([a.strip().lstrip("@") for a in it["addressed_to"] if a.strip()]),
                      it["signed_as"].strip(), it["confidence"], model, now, rt, co,
                      json.dumps([c.strip() for c in it["claims"] if c.strip()]), it["stated_goal"].strip(), VERSION))
        work.execute("DELETE FROM event_tags WHERE event_id=?", (eid,))
        work.executemany("INSERT INTO event_tags VALUES (?,?)", [(eid, t) for t in tags])
        ok += 1
    work.commit()
    return ok


def run(db, workers=8, effort="low", limit=None, passes=2, actors=None,
        log=lambda m: print(m, file=sys.stderr, flush=True)):
    con, work = Q.connect(db), open_work(db)
    meta = dict(con.execute("SELECT key, value FROM meta"))
    dataset = f"{meta.get('name')}: {meta.get('description', '')} {meta.get('notes', '')}"[:1500]
    total = con.execute("SELECT count(*) FROM events").fetchone()[0]
    _, info = _context(con)
    for p in range(passes):  # a second pass picks up events a batch left out
        done = {r[0] for r in work.execute("SELECT event_id FROM tags WHERE version >= ?", (VERSION,))}
        todo = list(batches(con, done, actors))
        if limit:
            todo = todo[:limit]
        if not todo:
            break
        log(f"pass {p + 1}: {sum(len(b) for b in todo):,} events to note in {len(todo)} batches ({workers} workers)")
        started, n_ok, n_fail = time.time(), 0, 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(tag_batch, b, dataset, effort): b for b in todo}
            for i, f in enumerate(concurrent.futures.as_completed(futures), 1):
                b = futures[f]
                try:
                    items, secs = f.result()
                    n_ok += save(work, items, b, info, f"codex/{effort}")
                except Exception as ex:
                    n_fail += 1
                    log(f"  batch failed ({len(b)} events): {str(ex)[:200]}")
                if i % 10 == 0 or i == len(todo):
                    el = time.time() - started
                    log(f"  {i}/{len(todo)} batches, {n_ok:,} events noted, {n_fail} failed, "
                        f"{el / 60:.1f} min elapsed, ~{el / i * (len(todo) - i) / 60:.0f} min left")
    noted = work.execute("SELECT count(*) FROM tags WHERE version >= ?", (VERSION,)).fetchone()[0]
    log(f"noted {noted:,} / {total:,} events (version {VERSION})")
    return {"noted": noted, "events": total, "version": VERSION}


# ---------------------------------------------------------------------------- queries (index con with work attached)

def available(con):
    try:
        return con.execute("SELECT count(*) FROM w.tags").fetchone()[0] > 0
    except Exception:
        return False


def require(con):
    if not available(con):
        raise Q.QueryError("no behaviour tags yet: run `python3 -m swarmgraph --db INDEX tag` first")


def profile(con, actor=None, since=None, until=None, segment=None):
    """Tag counts overall or for one actor, with the most common free labels."""
    require(con)
    w, v = Q.window("e", since, until, segment)
    aw, av = (" AND e.actor=?", [Q.resolve(con, actor)]) if actor else ("", [])
    rows = con.execute(f"SELECT t.tag, count(*) FROM w.event_tags t JOIN events e ON e.id=t.event_id WHERE 1{aw}{w} "
                       f"GROUP BY t.tag ORDER BY count(*) DESC", av + v).fetchall()
    n = con.execute(f"SELECT count(*) FROM w.tags t JOIN events e ON e.id=t.event_id WHERE 1{aw}{w}", av + v).fetchone()[0]
    other = con.execute(f"SELECT lower(t.other), count(*), count(DISTINCT e.actor) FROM w.tags t JOIN events e "
                        f"ON e.id=t.event_id WHERE t.other<>''{aw}{w} GROUP BY lower(t.other) ORDER BY count(*) DESC "
                        f"LIMIT 20", av + v).fetchall()
    return {"tagged_events": n, "tags": {r[0]: {"n": r[1], "share": round(r[1] / n, 4) if n else None} for r in rows},
            "free_labels": [{"label": r[0], "n": r[1], "actors": r[2]} for r in other], "vocabulary": VOCAB}


def behaviors(con, actor=None, tag=None, other=None, since=None, until=None, segment=None, limit=50, newest=False):
    """Tagged events (with the LLM summary), filtered by actor, tag or free label."""
    require(con)
    w, v = Q.window("e", since, until, segment)
    sql, vals = "", []
    if actor:
        sql += " AND e.actor=?"
        vals.append(Q.resolve(con, actor))
    if tag:
        if tag not in VOCAB:
            raise Q.QueryError(f"tag must be one of {', '.join(VOCAB)}")
        sql += " AND t.event_id IN (SELECT event_id FROM w.event_tags WHERE tag=?)"
        vals.append(tag)
    if other:
        sql += " AND lower(t.other) LIKE ?"
        vals.append(f"%{other.lower()}%")
    total = con.execute(f"SELECT count(*) FROM w.tags t JOIN events e ON e.id=t.event_id WHERE 1{sql}{w}", vals + v).fetchone()[0]
    rows = con.execute(f"SELECT e.id, e.ts, e.actor, e.channel, e.text, t.tags, t.other, t.summary, t.addressed_to, "
                       f"t.signed_as, t.confidence, t.responds_to, t.continues, t.claims, t.stated_goal "
                       f"FROM w.tags t JOIN events e ON e.id=t.event_id WHERE 1{sql}{w} "
                       f"ORDER BY e.ts {'DESC' if newest else ''} LIMIT ?", vals + v + [int(limit)]).fetchall()
    return {"total": total, "rows": [{"event_id": r[0], "ts": r[1], "actor": r[2], "channel": r[3],
                                      "tags": json.loads(r[5]), "other": r[6], "summary": r[7],
                                      "addressed_to": json.loads(r[8]), "signed_as": r[9], "confidence": r[10],
                                      "responds_to": r[11] or None, "continues": r[12] or None,
                                      "claims": json.loads(r[13]) if r[13] else [], "stated_goal": r[14] or None,
                                      "text": Q.clip(r[4], 240)} for r in rows]}

