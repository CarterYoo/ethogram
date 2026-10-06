"""Chunks: the record cut into pieces one reader can read whole.

Investigators of the OpenAI / Hugging Face incident read transcripts one context window at a time, and the analysis
agents they delegated to missed what they were handed but could not read, without saying so. Measured here (RESULTS
section 11): a day of one agent's transcript is 600k characters even with reasoning left out and tool output clipped,
so a reader handed a day greps it, which is sampling again. The unit of reading is therefore the actor's own working
context: a chunk starts where meta.boundary == "context" (a new run, a compaction), or, in logs without such markers,
where the day changes. Neighbouring pieces of the same day are merged up to TARGET characters as rendered; pieces
longer than CAP are cut at their largest time gaps. Every event of the record is in exactly one chunk.

The record: in transcripts (an action layer with reads), the events of the actors who act (and of system actors such
as the harness that runs them) with what they read shown inline; other actors' messages appear through the reads.
Otherwise every event. Rendering clips what is long and rarely decisive (private reasoning, tool output, messages read) and numbers the
lines; readers cite line numbers, which code maps back to event ids.
"""
import collections
import json
import re
from urllib.parse import parse_qsl, urlparse

from .build import epoch

TARGET, CAP = 40_000, 120_000
CLIP = {"reasoning": 700, "result": 900, "read": 500, "action": 600}
CLIP_OTHER = 2000


def record(con):
    """the record's events in time order, with what each read event read"""
    doers = {a for (a,) in con.execute("SELECT DISTINCT actor FROM events WHERE kind IN ('action', 'result')")}
    system = {a for (a,) in con.execute("SELECT id FROM actors WHERE kind='system'")}
    rows = con.execute("SELECT id, ts, actor, kind, text, reply_to, channel, meta FROM events ORDER BY ts, id").fetchall()
    # an agent's transcript holds others' messages as what it read: show them there, once. Logs without reads (edit
    # logs, boards) keep every actor's events
    reads = con.execute("SELECT 1 FROM events WHERE kind='read' LIMIT 1").fetchone()
    keep = (lambda a: a in doers or a in system) if doers and reads else (lambda a: True)
    text_of = {}
    if doers:
        wanted = {r[5] for r in rows if r[3] == "read" and r[5]}
        text_of = {r[0]: (r[2], r[4]) for r in rows if r[0] in wanted}
    first = first_reads(con)
    out = []
    for i, ts, actor, kind, text, rt, channel, meta in rows:
        if not keep(actor):
            continue
        m = json.loads(meta) if meta else {}
        e = {"id": i, "ts": ts, "actor": actor, "kind": kind, "text": text or "", "reply_to": rt, "channel": channel,
             "meta": m, "error": bool(m.get("error"))}
        if kind == "read" and rt in text_of:
            e["read_actor"], e["read_text"] = text_of[rt]
            e["reread"] = first.get((actor, rt)) not in (None, i)
        out.append(e)
    return out


def first_reads(con):
    """(reader, message) -> the event in which the reader first read it. Half of one agent's 35k reads were the same
    messages again (its tools replay recent history), so later reads are shown in one short line."""
    first = {}
    for i, actor, rt in con.execute("SELECT id, actor, reply_to FROM events WHERE kind='read' ORDER BY ts, id"):
        first.setdefault((actor, rt), i)
    return first


def _clip(t, n):
    t = " ".join((t or "").split())
    if len(t) <= n:
        return t
    if n >= 800:  # tool output: the end often says how it went
        return t[:n - 260] + " … " + t[-240:]
    return t[:n] + " …"


def line(e, names, lead=None):
    """one rendered line (without its number); events by anyone but `lead` name their actor"""
    k = e["kind"]
    if k == "read":
        who = names.get(e.get("read_actor"), e.get("read_actor") or "?")
        if e.get("reread"):
            body = f"(again) {who}: " + _clip(e.get("read_text", ""), 90)
        else:
            body = f"{who} said: " + _clip(e.get("read_text", ""), CLIP["read"])
    else:
        body = _clip(e["text"], CLIP.get(k, CLIP_OTHER))
    label = k
    if k == "result" and e["error"]:
        label = "result FAILED"
    elif k == "self_report" and e["channel"]:
        label = f"self_report/{e['channel']}"
    elif k == "message" and e["meta"].get("posted") is False:
        label = "message (post FAILED)"
    who = "" if e["actor"] == lead else f" [{names.get(e['actor'], e['actor'])}]"
    return f"{e['ts'][11:16]} {label}{who}: {body}"


def _names(con):
    return dict(con.execute("SELECT id, label FROM actors"))


def _lead(ev, share=0.0):
    """the actor most of what was done or said belongs to (None when no actor has `share` of it)"""
    c = collections.Counter(e["actor"] for e in ev if e["kind"] in ("action", "message", "reasoning", "call", "return"))
    if not c:
        return None
    a, n = c.most_common(1)[0]
    return a if n >= share * sum(c.values()) else None


ONE_ACTOR = 0.8  # a chunk is one actor's record when it holds this share of what was done or said in it
MIN_RUN = 4  # repeats of one action shape shown as one line
URL_RX = re.compile(r"https?://[^\s'\"<>]+")


def shape(e):
    """the form of an action, for folding repeats: who, which tool, which host and path and which query parameters
    (requests that differ only in parameter values), or the exact command"""
    t = e["text"] or ""
    m = URL_RX.search(t[:2000])
    try:
        u = urlparse(m.group(0)) if m else None
    except ValueError:  # malformed (e.g. a bracketed host that is not an IPv6 address): fold by the exact text
        u = None
    if u is not None:
        keys = tuple(sorted({k for k, _ in parse_qsl(u.query, keep_blank_values=True)}))
        parts = u.path.rstrip("/").split("/")
        folder = "/".join(parts[:-1]) + "/*" if len(parts) > 2 else u.path  # names guessed in one folder fold
        return (e["actor"], t[:m.start()][:60], u.netloc, folder, keys)
    return (e["actor"], t[:300])


def runs(ev):
    """groups of indices into ev, in order of their first event: repeated actions of one shape by one actor (with
    their results), uninterrupted by anything else that actor did, become one group; everything else stands alone"""
    groups, open_, acts = [], {}, {}  # open_: actor -> (shape, group); acts: actor -> ids of its open actions

    def close(a):
        acts.pop(a, None)
        g = open_.pop(a, None)
        if g:
            n = sum(1 for k in g[1] if ev[k]["kind"] == "action")
            if n >= MIN_RUN:
                groups.append(g[1])
            else:
                groups.extend([k] for k in g[1])

    for k, e in enumerate(ev):
        a = e["actor"]
        if e["kind"] == "action":
            sh = shape(e)
            if a in open_ and open_[a][0] == sh:
                open_[a][1].append(k)
                acts[a].add(e["id"])
                continue
            close(a)
            open_[a] = (sh, [k])
            acts[a] = {e["id"]}
        elif e["kind"] == "result" and a in open_ and e["reply_to"] in acts[a]:
            open_[a][1].append(k)
        else:
            close(a)
            groups.append([k])
    for a in list(open_):
        close(a)
    groups.sort(key=lambda g: g[0])
    return groups


def _form(v):
    """the form of a parameter value: digits, a word, an identifier, or something else"""
    if re.fullmatch(r"-?\d{1,9}", v):
        return "number"
    if re.fullmatch(r"[A-Za-z]+", v):
        return "word"
    if re.fullmatch(r"[\w.\-|:/]+", v):
        return "token"
    return "other"


def _odd(values):
    """values unlike the rest, listed in full: a fold must not hide them (a run of record-number requests hid
    injection-shaped, oversized and non-numeric values between its first and last value)"""
    from urllib.parse import unquote_plus
    vs = sorted(values, key=lambda v: (len(v), v))
    form = {v: _form(unquote_plus(v)) for v in vs}
    clean = collections.Counter(f for f in form.values() if f != "other")
    main = clean.most_common(1)[0][0] if clean else None
    odd = [v for v in vs if form[v] == "other" or (main and form[v] != main) or
           (main == "number" and form[v] == "number" and len(v.lstrip("-")) > 6)]
    if main == "number":  # numbers too long to be one of the ids are odd too
        odd += [v for v in vs if form[v] == "token" and v.isdigit() and v not in odd]
    if not odd:
        return ""
    return "; unlike the rest: " + ", ".join(repr(unquote_plus(v))[:60] for v in odd[:12]) + \
        (f" and {len(odd) - 12} more" if len(odd) > 12 else "")


def group_line(evs, names, lead=None):
    """one line for a run of repeated actions: how many, over what time, the first one, what varied, how it went"""
    acts = [e for e in evs if e["kind"] == "action"]
    res = [e for e in evs if e["kind"] == "result"]
    first = acts[0]
    vary = ""
    m = URL_RX.search(first["text"] or "")
    if m:
        vals = collections.defaultdict(set)
        for e in acts:
            mm = URL_RX.search(e["text"] or "")
            if mm:
                for k, v in parse_qsl(urlparse(mm.group(0)).query, keep_blank_values=True):
                    vals[k].add(v)
        varied = [f"{k} ({len(v)} values{_odd(v)})" for k, v in sorted(vals.items(), key=lambda kv: -len(kv[1]))
                  if len(v) > 1]
        paths = {urlparse(mm.group(0)).path for mm in (URL_RX.search(e["text"] or "") for e in acts) if mm}
        if len(paths) > 1:
            varied.insert(0, f"the last part of the path ({len(paths)} values)")
        vary = f"; varying {', '.join(varied[:4])}" if varied else "; identical"
        last = URL_RX.search(acts[-1]["text"] or "")
        if last and last.group(0) != m.group(0):
            vary += f"; last: {_clip(last.group(0), 160)}"
    outcome = collections.Counter(("FAILED " if r["error"] else "") + " ".join((r["text"] or "").split()[:2])[:24]
                                  for r in res)
    how = ("; results: " + ", ".join(f"{k} x{n}" for k, n in outcome.most_common(4))) if res else ""
    who = "" if first["actor"] == lead else f" [{names.get(first['actor'], first['actor'])}]"
    return (f"{first['ts'][11:16]}-{acts[-1]['ts'][11:16]} action x{len(acts)}{who}: "
            f"{_clip(first['text'], CLIP['action'])}{vary}{how}")


def make(con, target=TARGET, cap=CAP):
    """cut the record into chunks; returns [{id, start, end, events: [ids], chars, day}]"""
    ev = record(con)
    names = _names(con)
    lead = _lead(ev)
    size = [0] * len(ev)
    for g in runs(ev):  # a folded run costs one line, counted at its first event
        size[g[0]] = (len(group_line([ev[k] for k in g], names, lead)) if len(g) > 1 else
                      len(line(ev[g[0]], names, lead))) + 6
    pre = [0]
    for x in size:
        pre.append(pre[-1] + x)
    ep = [epoch(e["ts"]) for e in ev]
    marked = any(e["meta"].get("boundary") == "context" for e in ev)
    pieces, lo = [], 0
    for k in range(1, len(ev) + 1):
        if k == len(ev) or ((ev[k]["meta"].get("boundary") == "context") if marked else
                            ev[k]["ts"][:10] != ev[k - 1]["ts"][:10]):
            pieces.append((lo, k))
            lo = k

    def split(lo, hi):  # cut at the largest time gaps until every part fits
        total = pre[hi] - pre[lo]
        if total <= cap or hi - lo < 2:
            return [(lo, hi)]
        mid = next(j for j in range(lo + 1, hi + 1) if pre[j] - pre[lo] >= total / 2 or j == hi)
        cand = sorted(range(lo + 1, hi), key=lambda j: ep[j - 1] - ep[j])[:max(3, (hi - lo) // 50)]
        best = next((j for j in cand if 0.25 * total <= pre[j] - pre[lo] <= 0.75 * total), min(mid, hi - 1))
        return split(lo, best) + split(best, hi)

    parts = [q for p in pieces for q in split(*p)]
    merged = []
    for lo, hi in parts:  # merge small neighbours of the same day
        if merged and ev[merged[-1][0]]["ts"][:10] == ev[lo]["ts"][:10] and \
                pre[hi] - pre[merged[-1][0]] <= target:
            merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))
    return [{"id": n + 1, "start": ev[lo]["ts"], "end": ev[hi - 1]["ts"], "day": ev[lo]["ts"][:10],
             "events": [ev[k]["id"] for k in range(lo, hi)], "chars": pre[hi] - pre[lo]}
            for n, (lo, hi) in enumerate(merged)]


SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, start TEXT, end TEXT, day TEXT, segment_id INTEGER,
                                  events INTEGER, chars INTEGER, ids TEXT);
CREATE TABLE IF NOT EXISTS chunk_reads(chunk_id INTEGER, question TEXT, qkey TEXT, created TEXT, effort TEXT,
                                       result TEXT, cited INTEGER, dropped INTEGER, seconds REAL,
                                       PRIMARY KEY(chunk_id, qkey));
"""


def save(con, work, chunks):
    """store chunks in the work store (replacing any earlier cut only if none of it has been read yet)"""
    work.executescript(SCHEMA)
    if work.execute("SELECT count(*) FROM chunk_reads").fetchone()[0]:
        old = work.execute("SELECT count(*) FROM chunks").fetchone()[0]
        if old:
            return {"kept": old, "note": "chunks have been read already; delete chunk_reads to cut again"}
    seg = dict(con.execute("SELECT id, segment_id FROM events"))
    work.execute("DELETE FROM chunks")
    for c in chunks:
        segs = collections.Counter(seg.get(i) for i in c["events"])
        work.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)",
                     (c["id"], c["start"], c["end"], c["day"], segs.most_common(1)[0][0], len(c["events"]), c["chars"],
                      json.dumps(c["events"])))
    work.commit()
    return {"chunks": len(chunks)}


def load(work):
    work.executescript(SCHEMA)
    return [dict(r) for r in work.execute("SELECT id, start, end, day, segment_id, events, chars FROM chunks ORDER BY id")]


def ids_of(work_or_con, chunk_id, attached=False):
    t = "w.chunks" if attached else "chunks"
    row = work_or_con.execute(f"SELECT ids FROM {t} WHERE id=?", (chunk_id,)).fetchone()
    return json.loads(row[0]) if row else []


def text(con, ids, with_lead=False):
    """rendered chunk: numbered lines with a date line where the day changes; returns (text, {line number: event id})
    (with_lead: (text, num, lead), lead None when several actors share the chunk and every line names its actor)"""
    names = _names(con)
    ev = _fetch(con, ids)  # by id, whatever the size (loading the whole record per chunk took minutes on 1.1M events)
    lead = _lead(ev, ONE_ACTOR)
    out, num, day = [], {}, None
    if lead:
        out.append(f"(lines without a [name] are by {names.get(lead, lead)})")
    for n, g in enumerate(runs(ev), 1):  # repeated actions folded into one line (cited by their first event)
        e = ev[g[0]]
        if e["ts"][:10] != day:
            day = e["ts"][:10]
            out.append(f"== {day} ==")
        out.append(f"L{n} {group_line([ev[k] for k in g], names, lead) if len(g) > 1 else line(e, names, lead)}")
        num[n] = e["id"]
    if with_lead:
        return "\n".join(out), num, lead
    return "\n".join(out), num


def _fetch(con, ids):
    rows = {}
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        q = ",".join("?" * len(part))
        for r in con.execute(f"SELECT id, ts, actor, kind, text, reply_to, channel, meta FROM events WHERE id IN ({q})",
                             part):
            rows[r[0]] = r
    reads = [r[5] for r in rows.values() if r[3] == "read" and r[5]]
    said = {}
    for i in range(0, len(reads), 900):
        part = reads[i:i + 900]
        q = ",".join("?" * len(part))
        said.update({r[0]: (r[1], r[2]) for r in con.execute(f"SELECT id, actor, text FROM events WHERE id IN ({q})",
                                                              part)})
    first = first_reads(con) if reads else {}
    out = []
    for i in ids:
        if i not in rows:
            continue
        _, ts, actor, kind, txt, rt, channel, meta = rows[i]
        m = json.loads(meta) if meta else {}
        e = {"id": i, "ts": ts, "actor": actor, "kind": kind, "text": txt or "", "reply_to": rt, "channel": channel,
             "meta": m, "error": bool(m.get("error"))}
        if kind == "read" and rt in said:
            e["read_actor"], e["read_text"] = said[rt]
            e["reread"] = first.get((actor, rt)) not in (None, i)
        out.append(e)
    out.sort(key=lambda e: (e["ts"], e["id"]))
    return out

