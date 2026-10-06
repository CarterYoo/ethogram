"""Summary structure, LLM half: cards written by Codex from event notes + code stats (never from raw text).

  trajectory card  one entity (actor, channel, or the whole swarm 'all'): role, phases, change points with
                   triggers, claims, comparison with others, unknowns
  link card        one pair of actors: relationship, key exchanges, effect on the other's behaviour

The LLM fills sentences {text, voice, evidence}; code renders them in one fixed format, checks every cited id,
number and trigger (structure.verify) and stores the card with its issues.

    python3 -m swarmgraph --db INDEX cards [--workers 8] [--effort medium]
"""
import collections
import concurrent.futures
import json
import re
import sys
import time

from . import query as Q
from . import structure as ST
from .build import epoch
from .llm import Codex
from .store import open_work

VERSION = 1
S = {"type": "object", "additionalProperties": False, "required": ["text", "voice", "evidence"],
     "properties": {"text": {"type": "string"}, "voice": {"type": "string", "enum": ["observed", "claim", "inferred"]},
                    "evidence": {"type": "array", "items": {"type": "string"}}}}
LIST = {"type": "array", "items": S}
PHASE = {"type": "object", "additionalProperties": False, "required": ["start", "end", "title", "summary"],
         "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "title": {"type": "string"},
                        "summary": S}}
CHANGE = {"type": "object", "additionalProperties": False, "required": ["at", "trigger", "trigger_kind", "before_after"],
          "properties": {"at": {"type": "string"}, "trigger": {"type": "array", "items": {"type": "string"}},
                         "trigger_kind": {"type": "string", "enum": ["external", "self", "unknown"]},
                         "before_after": {"type": "string"}}}


def obj(props):
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


TRAJ_SCHEMA = obj({"role": S, "phases": {"type": "array", "items": PHASE}, "changes": {"type": "array", "items": CHANGE},
                   "claims": LIST, "unusual": LIST, "unknowns": LIST})
LINK_SCHEMA = obj({"relationship": S, "exchanges": LIST, "effect": LIST, "unknowns": LIST})
SWARM_SCHEMA = obj({"phases": {"type": "array", "items": PHASE}, "changes": {"type": "array", "items": CHANGE},
                    "key_points": LIST, "unknowns": LIST})

RULES = """Writing rules (analysts read these cards to decide what to check next):
1. Say what was done with verbs: who (ids exactly as given) + what (behaviour vocabulary words + concrete object) +
   how much/when. Not "did research" but "posted 12 pages of data links".
2. Mark every sentence's voice: observed (the notes show it), claim (the actor says it), inferred (your reading).
   Motives only when the actor states them, as a claim.
3. Cite event ids from the input for every sentence (comparisons may cite stats as "stats:<key.path>"). Use only
   numbers that appear in the input.
4. Describe changes as before → trigger → after.
5. Fold repetition into one statement with a count and time range.
6. Compare with others only through the stats given.
7. Say what the data cannot show (identity, whether something was read, hidden reasons).
8. Neutral words; describe behaviour whether or not it is risky; no labels the data does not use.
9. Keep it short: titles <= 6 words, sentences <= 30 words."""

TRAJ_PROMPT = """You summarise the trajectory of one {kind} in a multi-agent dataset. Do not run commands or browse.
Dataset: {dataset}

Entity: {kind} `{key}`
Stats (computed by code): {stats}
Change-point candidates (computed by code: reasons, trigger event ids): {candidates}
Events in time order (one note per event; similar consecutive events are folded as ×n; ids in [..]):
{lines}
{extra}
Return:
- role: one sentence on what this {kind} mainly does or is used for
- phases: 1-7 contiguous phases in order covering all events; start/end are event ids; title is a verb phrase.
  A phase normally spans several events; do not give a single event its own phase unless it marks a real change.
  Do not state how many events a phase has (code adds counts and times)
- changes: one per boundary between phases: at = first event id of the new phase, trigger = ids of events that
  plausibly caused it (from the candidates or the notes; [] if none), trigger_kind external (another actor's event),
  self (its own result or decision) or unknown, before_after = one plain sentence "what it did before → what
  it did after" (no voice prefix)
- claims: up to 5 things it asserts (outcomes, status, identity) with evidence (voice claim)
- unusual: up to 3 comparisons with others using only the stats
- unknowns: up to 3 things the data cannot show about it

{rules}"""

LINK_PROMPT = """You summarise how two actors in a multi-agent dataset are connected. Do not run commands or browse.
Dataset: {dataset}

Pair: `{a}` and `{b}`
Stats (computed by code: useful links by type and direction): {stats}
Linked events in time order (notes; `A→B type` says who acted toward whom):
{lines}
Behaviour changes of either actor that code linked to these events: {changes}

Return:
- relationship: one sentence on the nature of the connection (who drives, what flows: instructions, information,
  artifacts, removals)
- exchanges: up to 4 key exchanges, each with evidence
- effect: up to 2 sentences on how one actor's behaviour changed after the other's actions (only if the changes
  above or the notes show it; else [])
- unknowns: up to 2 things the data cannot show about this connection

{rules}"""

SWARM_PROMPT = """You summarise how the behaviour of a whole multi-agent swarm developed over time. Do not run commands
or browse.
Dataset: {dataset}

Totals (computed by code): {stats}
Time buckets (label | events | active actors | behaviour mix | examples as id(tag)):
{lines}
Candidate shifts between buckets (computed by code): {candidates}
Most connected actors (useful links in/out by type): {actors}

Return:
- phases: 2-7 phases of the swarm; start/end are bucket labels exactly as given; summary cites example ids
- changes: one per boundary: at = first bucket label of the new phase, trigger = example ids that plausibly
  caused it ([] if none), trigger_kind, before_after
- key_points: up to 5 sentences on who drove the swarm and how actors were connected, with evidence
- unknowns: up to 3 things the data cannot show

{rules}"""


# ---------------------------------------------------------------------------- inputs

def dataset_line(con):
    m = dict(con.execute("SELECT key, value FROM meta"))
    return f"{m.get('name')}: {m.get('description', '')} {m.get('notes', '')}"[:1500]


def trajectory_input(con, kind, key, note):
    evs = ST.entity_events(con, kind, key, note)
    if not evs:
        raise Q.QueryError(f"no events for {kind} {key}")
    cands = ST.candidates(con, kind, key, evs)
    st = ST.stats(con, kind, key, evs)
    lines = ST.collapse(evs)
    trig = [t for c in cands for t in c["trigger"]]
    extra = ""
    if trig:  # show the trigger events themselves (they belong to other actors)
        rows = con.execute(f"SELECT id, ts, actor FROM events WHERE id IN ({','.join('?' * len(trig))})", trig).fetchall()
        extra = "Trigger events by others:\n" + "\n".join(
            f"[{r[0]}] {r[1][5:16]} {r[2]}: {note.get(r[0], {}).get('summary', '(no note)')}" for r in rows)
    ids = {e["id"] for e in evs} | set(trig)
    ts_of = {e["id"]: e["ts"] for e in evs}
    ts_of.update(dict(con.execute(f"SELECT id, ts FROM events WHERE id IN ({','.join('?' * len(trig))})", trig).fetchall())
                 if trig else {})
    prompt = TRAJ_PROMPT.format(kind=kind, key=key, dataset=dataset_line(con), stats=json.dumps(st),
                                candidates=json.dumps([{k: c[k] for k in ("at", "ts", "reasons", "trigger")} for c in cands]),
                                lines="\n".join(lines), extra=extra, rules=RULES)
    return prompt, {"entity_ids": {e["id"] for e in evs}, "allowed": ids, "ts_of": ts_of, "stats": st,
                    "candidates": cands, "evs": evs}


def link_input(con, a, b, note):
    rows = con.execute("SELECT src, dst, type, event_id, ts FROM w.links WHERE (src=? AND dst=?) OR (src=? AND dst=?) "
                       "ORDER BY ts", (a, b, b, a)).fetchall()
    if not rows:
        raise Q.QueryError(f"no useful links between {a} and {b}")
    by = collections.Counter((r[0], r[2]) for r in rows)
    st = {"events": len(rows), "first": rows[0][4][:16], "last": rows[-1][4][:16],
          "by_direction_and_type": {f"{s}→{b if s == a else a} {t}": n for (s, t), n in by.items()}}
    shown = rows if len(rows) <= 80 else rows[:40] + rows[-40:]
    lines = [f"[{r[3]}] {r[4][5:16]} {r[0]}→{r[1]} {r[2]}: {note.get(r[3], {}).get('summary', '(no note)')}" for r in shown]
    ids = {r[3] for r in rows}
    ts_of = {r[3]: r[4] for r in rows}
    return (LINK_PROMPT.format(dataset=dataset_line(con), a=a, b=b, stats=json.dumps(st), lines="\n".join(lines),
                               changes="(filled in after trajectory cards exist)", rules=RULES),
            {"entity_ids": ids, "allowed": ids, "ts_of": ts_of, "stats": st})


def buckets(con):
    """Calendar buckets for the swarm view: days, with busy days (>= 8% of all events) split into 3-hour slots."""
    days = con.execute("SELECT substr(ts, 1, 10), count(*) FROM events GROUP BY 1 ORDER BY 1").fetchall()
    total = sum(n for _, n in days)
    out = []
    for d, n in days:
        if n >= 0.08 * total:
            for h in range(0, 24, 3):
                out.append((f"{d} {h:02d}h", f"{d} {h:02d}:00:00", f"{d} {h + 3:02d}:00:00" if h < 21 else f"{d} 24:00:00"))
        else:
            out.append((d, f"{d} 00:00:00", f"{d} 24:00:00"))
    return out


def swarm_input(con, note):
    lines, labels, mixes = [], [], []
    ts_of, allowed = {}, set()
    for label, lo, hi in buckets(con):
        rows = con.execute("SELECT id, actor FROM events WHERE ts >= ? AND ts < ? ORDER BY ts", (lo, hi)).fetchall()
        if not rows:
            continue
        c = collections.Counter(t for r in rows for t in note.get(r[0], {}).get("tags", []))
        n = sum(c.values()) or 1
        mix = {k: v / n for k, v in c.items()}
        ex = []
        for tag, _ in c.most_common(3):  # one example per main behaviour, plus removals
            eid = next((r[0] for r in rows if tag in note.get(r[0], {}).get("tags", [])), None)
            if eid:
                ex.append(f"{eid}({tag})")
                allowed.add(eid)
        lines.append(f"{label} | {len(rows)} | {len({r[1] for r in rows})} | "
                     + ", ".join(f"{k} {v:.0%}" for k, v in c.most_common(4)) + " | " + " ".join(ex))
        labels.append(label)
        mixes.append((label, mix, len(rows)))
        ts_of[label] = lo
    cands = []
    for (l1, m1, n1), (l2, m2, n2) in zip(mixes, mixes[1:]):
        d = ST._tvd(m1, m2)
        if d >= 0.3 or (n2 >= 3 * n1 and n2 >= 100):
            cands.append({"at": l2, "distance": round(d, 2), "events_before": n1, "events_after": n2})
    actors = [{"actor": r[0], "out": r[1]} for r in con.execute(
        "SELECT src, group_concat(type || ':' || n) FROM (SELECT src, type, count(*) n FROM w.links GROUP BY src, type) "
        "GROUP BY src ORDER BY sum(n) DESC LIMIT 12")]
    st = {"events": con.execute("SELECT count(*) FROM events").fetchone()[0],
          "actors": con.execute("SELECT count(DISTINCT actor) FROM events").fetchone()[0],
          "useful_links": dict(con.execute("SELECT type, count(*) FROM w.links GROUP BY type").fetchall())}
    prompt = SWARM_PROMPT.format(dataset=dataset_line(con), stats=json.dumps(st), lines="\n".join(lines),
                                 candidates=json.dumps(cands), actors=json.dumps(actors), rules=RULES)
    return prompt, {"entity_ids": set(labels), "allowed": allowed | set(labels), "ts_of": ts_of, "stats": st,
                    "candidates": cands}


# ---------------------------------------------------------------------------- rendering (fixed format)

def _s(x):
    if not x:
        return ""
    ev = " ".join(x.get("evidence") or [])
    return f"[{x['voice']}] {x['text']}" + (f" [{ev}]" if ev else "")


def render(kind, key, card, ctx):
    st, ts = ctx["stats"], ctx["ts_of"]
    if kind == "link":
        out = [f"{key} · {st['events']} linked events · {st['first']} → {st['last']}",
               "Types: " + " · ".join(f"{k} {v}" for k, v in st["by_direction_and_type"].items()),
               "Relationship " + _s(card["relationship"])]
        out += ["  - " + _s(x) for x in card["exchanges"]]
        out += ["Effect " + _s(x) for x in card["effect"]]
        out += ["Unknown: " + x["text"] for x in card["unknowns"]]
        return "\n".join(out)
    head = f"{key} · {kind} · {st['events']} events · {st.get('first')} → {st.get('last')}"
    if kind == "actor":
        head += f" · {st.get('channels')} channels"
    elif kind == "channel":
        head += f" · {st.get('actors')} actors"
    out = [head]
    if card.get("role"):
        out.append("Role " + _s(card["role"]))
    changes = {c["at"]: c for c in card.get("changes") or []}
    evs = ctx.get("evs")
    for j, p in enumerate(card["phases"], 1):
        if j > 1 and p["start"] in changes:
            c = changes[p["start"]]
            tr = " ".join(c["trigger"])
            out.append(f"   ↓ {c['trigger_kind']}: {c['before_after']}" + (f" [{tr}]" if tr else ""))
        span = f"{ts.get(p['start'], p['start'])[5:16]} – {ts.get(p['end'], p['end'])[5:16]}"
        n = ""
        if evs:
            inside = [e for e in evs if ts.get(p["start"], "") <= e["ts"] <= ts.get(p["end"], "")]
            tags = collections.Counter(t for e in inside for t in e.get("tags") or [])
            tot = sum(tags.values()) or 1
            n = f" · {len(inside)} events (" + ", ".join(f"{k} {v / tot:.0%}" for k, v in tags.most_common(2)) + ")"
        out.append(f"{j}. {p['title']} · {span}{n}")
        out.append("   " + _s(p["summary"]))
    for k, label in (("claims", "Claims"), ("unusual", "vs others"), ("key_points", "Key")):
        for x in card.get(k) or []:
            out.append(f"{label}: " + _s(x))
    if st.get("links_out") or st.get("links_in"):
        fmt = lambda xs: ", ".join(f"{x['type']} {x['actor']} {x['n']}" for x in xs[:5]) or "–"
        out.append(f"Links out: {fmt(st.get('links_out', []))} | in: {fmt(st.get('links_in', []))}")
    out += ["Unknown: " + x["text"] for x in card.get("unknowns") or []]
    return "\n".join(out)


# ---------------------------------------------------------------------------- selection and run

def select(con, min_actor=20, min_actor_links=10, min_channel=10, min_pair=3):
    ev_by_actor = dict(con.execute("SELECT actor, count(*) FROM events GROUP BY actor").fetchall())
    links_by_actor = collections.Counter()  # removals are one-way moderation: they show up in both actors' cards
    for s, d, n in con.execute("SELECT src, dst, count(*) FROM w.links WHERE type <> 'removes' GROUP BY src, dst"):
        links_by_actor[s] += n
        links_by_actor[d] += n
    actors = sorted(a for a in ev_by_actor if ev_by_actor[a] >= min_actor or links_by_actor[a] >= min_actor_links)
    channels = [r[0] for r in con.execute("SELECT channel FROM events WHERE channel IS NOT NULL GROUP BY channel "
                                          "HAVING count(*) >= ? AND count(DISTINCT actor) >= 2", (min_channel,))]
    pairs = collections.Counter()
    for s, d, n in con.execute("SELECT src, dst, count(*) FROM w.links WHERE type NOT IN ('responds', 'removes') "
                               "GROUP BY src, dst"):
        pairs[tuple(sorted((s, d)))] += n
    return {"all": ["all"], "actor": actors, "channel": channels,
            "link": [f"{a}~{b}" for (a, b), n in pairs.items() if n >= min_pair]}


def make(con, kind, key, note):
    if kind == "all":
        prompt, ctx = swarm_input(con, note)
        return prompt, ctx, SWARM_SCHEMA
    if kind == "link":
        a, b = key.split("~", 1)
        prompt, ctx = link_input(con, a, b, note)
        return prompt, ctx, LINK_SCHEMA
    prompt, ctx = trajectory_input(con, kind, key, note)
    return prompt, ctx, TRAJ_SCHEMA


def write_card(db, kind, key, effort, log=None):
    """Worker thread: own connection, one Codex call."""
    t0 = time.time()
    con = Q.connect(db)
    try:
        note = ST.notes(con)
        prompt, ctx, schema = make(con, kind, key, note)
    finally:
        con.close()
    t1 = time.time()
    card, secs = Codex(effort=effort, timeout=900, retries=1).run(prompt, schema)
    issues = ST.verify(card, ctx["entity_ids"], ctx["allowed"], ctx["ts_of"], prompt, ctx["stats"])
    text = render(kind, key, card, ctx)
    if log:
        log(f"    {kind} {key}: input {t1 - t0:.0f}s, {len(prompt):,} chars, LLM {secs:.0f}s, {len(issues)} issues")
    return card, ctx, issues, text


def run(db, workers=8, effort="medium", kinds=None, limit=None, force=False,
        log=lambda m: print(m, file=sys.stderr, flush=True)):
    con, work = Q.connect(db), open_work(db)
    sel = select(con)
    have = set() if force else {(r[0], r[1]) for r in work.execute("SELECT kind, key FROM cards WHERE version=?", (VERSION,))}
    todo = [(k, key) for k in (kinds or ["all", "actor", "channel", "link"]) for key in sel[k] if (k, key) not in have]
    if limit:
        todo = todo[:limit]
    log(f"cards to write: {len(todo)} (" + ", ".join(f"{k} {len(sel[k])}" for k in sel) + f"); {workers} workers")
    done = failed = 0
    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(write_card, db, k, key, effort, log): (k, key) for k, key in todo}
        for f in concurrent.futures.as_completed(futures):
            k, key = futures[f]
            try:
                card, ctx, issues, text = f.result()
            except Exception as ex:
                failed += 1
                log(f"  {k} {key}: failed: {str(ex)[:160]}")
                continue
            work.execute("INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,?,?)",
                         (k, key, VERSION, time.strftime("%Y-%m-%d %H:%M:%S"), json.dumps(ctx["stats"]),
                          json.dumps(card), text, json.dumps(issues)))
            work.execute("DELETE FROM cards_fts WHERE kind=? AND key=?", (k, key))
            work.execute("INSERT INTO cards_fts VALUES (?,?,?)", (k, key, text))
            work.commit()
            done += 1
            if done % 10 == 0 or done == len(todo):
                log(f"  {done}/{len(todo)} cards, {failed} failed, {(time.time() - started) / 60:.1f} min")
    return {"written": done, "failed": failed, "selected": {k: len(v) for k, v in sel.items()}}


# ---------------------------------------------------------------------------- the story for people (not agents)

PLAIN_GROUP = {"work": "doing the task", "coordinate": "coordinating", "trial": "trying things out",
               "workaround": "working around obstacles", "control": "removing or moderating", "other": "other"}
STORY_SCHEMA = obj({
    "overview": {"type": "array", "items": obj({"text": {"type": "string"},
                                                "incidents": {"type": "array", "items": {"type": "string"}}})},
    "storylines": {"type": "array", "items": obj({"title": {"type": "string"}, "explanation": {"type": "string"},
                                                  "incidents": {"type": "array", "items": {"type": "string"}}})},
    "captions": {"type": "array", "items": obj({"incident": {"type": "string"}, "text": {"type": "string"}})}})

STORY_PROMPT = """You explain what happened in a multi-agent dataset to people who have not seen it and are not
technical. Do not run commands or browse.
Dataset: {dataset}

Key incidents found by code, in time order. An incident is one actor's burst of activity in a short time window:
when, who, how many actions, what kind of activity, what the actor did (from per-event notes) and how others reacted.
{incidents}

Connections between incidents (A -> B: B responded to, followed, built on or was triggered by A):
{edges}

Groups of connected incidents suggested by code (you may merge or split them):
{groups}

Phases of the whole swarm from an earlier summary:
{phases}

What the system is, inferred from the data alone, and possibly misaligned behaviour found in it:
{concerns}

Write for a general reader:
- overview: 3-5 short sentences telling the whole story in order: what happened, who drove it, what changed and
  why. List the incidents each sentence is based on.
- storylines: 3-7 storylines. title = at most 8 plain words (like "Agents share quiz answers across runs");
  explanation = 1-2 plain sentences (who did what, how others reacted, what changed); incidents = the incidents that
  belong to it (each incident in at most one storyline; keep connected incidents together). Place every incident in
  the storyline it fits; leave one out only if it fits none.
- captions: at most 10 plain words saying what happened there, for every incident you put in a storyline (and
  for other important ones), up to 120.

Rules for every text: describe methods only by what kind of thing was done, never payloads, URLs, encodings,
commands or step-by-step methods; everyday words; no ids, codes, page or file names, field names, labels in brackets or
technical terms (write "posted on a shared page", not "saved dse/X"); agent names only when they help, introduced as
"an agent called X"; numbers only if they appear above, rounded ("about 300"); describe what was done and how others
reacted; no guesses about hidden motives. Do not soften concerning behaviour: when agents appear to act against the
wishes of the people running the system, get around restrictions, keep going after being stopped, or share answers
in a way that undermines their task, say so plainly as what it appears to be, and make it a storyline of its own.
Mention misaligned behaviour in the overview if the concerns above rate it medium or higher."""

META = re.compile(r"\b[A-Za-z]+:[\w~@/]|~|@\d|\b[a-z]+_[a-z]+\b|\bI\d+\b|\[|\]")


def story_input(con, min_level=2, max_incidents=250):
    from .structure import swarm_incidents
    note = ST.notes(con)
    r = swarm_incidents(con, min_level)
    incs = sorted(r["incidents"], key=lambda x: (-x["level"], -x["n"]))[:max_incidents]
    incs.sort(key=lambda x: x["ts"])
    short = {x["id"]: f"I{i + 1}" for i, x in enumerate(incs)}
    lines = []
    for x in incs:
        rows = con.execute(f"SELECT id FROM events WHERE actor=? AND ts >= ? AND ts <= ? ORDER BY ts LIMIT 40",
                           (x["actor"], x["ts"], x["last_ts"])).fetchall()
        said = []
        for (eid,) in rows:
            sm = note.get(eid, {}).get("summary")
            if sm and sm not in said:
                said.append(sm)
        mix = ", ".join(f"{PLAIN_GROUP[g]} {round(100 * v)}%" for g, v in x["mix"].items() if v >= .15)
        react = []
        if x["responders"]:
            react.append(f"{len(x['responders'])} other agents reacted to it")
        if x["triggered"]:
            react.append(f"it preceded a change in behaviour of {len(x['triggered'])} other actor(s) or page(s)")
        if x.get("concerns"):
            react.append("flagged as possibly misaligned: " + ", ".join(k.replace("_", " ") for k in x["concerns"]))
        lines.append(f"{short[x['id']]} | {x['ts'][5:16]} to {x['last_ts'][11:16]} | {x['actor']} | {x['n']} actions | {mix} | "
                     f"did: {' / '.join(said[:3])}" + (f" | {'; '.join(react)}" if react else ""))
    edges = [f"{short[e['from']]} -> {short[e['to']]} ({e['type']})" for e in r["edges"]
             if e["from"] in short and e["to"] in short and e["type"] != "same actor"]
    parent = {k: k for k in short.values()}
    find = lambda a: a if parent[a] == a else find(parent[a])
    ts = {short[x["id"]]: epoch(x["ts"]) for x in incs}
    for e in r["edges"]:
        a, b = short.get(e["from"]), short.get(e["to"])
        if a and b and e["type"] != "same actor" and abs(ts[a] - ts[b]) <= 86400:
            parent[find(a)] = find(b)
    groups = collections.defaultdict(list)
    for k in short.values():
        groups[find(k)].append(k)
    groups = sorted((g for g in groups.values() if len(g) >= 2), key=len, reverse=True)
    swarm = get_card(con, "all", "all")
    phases = "\n".join(f"- {p['title']}: {p['summary']['text']}" for p in (swarm["card"]["phases"] if swarm else [])) or "(none)"
    phases = re.sub(r"\[[^\]]*\]", "", phases)
    from .alignment import concerns_card, environment_text
    cc = concerns_card(con)
    conc = (environment_text(cc["environment"]) if cc.get("environment") else "(not inferred)") + "\n" + (
        cc["concerns"]["summary"] + "\n" + "\n".join(f"- [{p['severity']}] {p['title']}: {p['what']} ({p['why_concerning']})"
                                                     for p in cc["concerns"]["patterns"]) if cc.get("concerns") else "(no concern cards)")
    prompt = STORY_PROMPT.format(dataset=dataset_line(con), incidents="\n".join(lines), edges="\n".join(edges) or "(none)",
                                 groups="\n".join(", ".join(g) for g in groups[:15]) or "(none)", phases=phases,
                                 concerns=conc)
    return prompt, {v: k for k, v in short.items()}


def write_story(db, effort="high", min_level=2, log=lambda m: print(m, file=sys.stderr, flush=True)):
    con, work = Q.connect(db), open_work(db)
    prompt, back = story_input(con, min_level)
    log(f"story: {len(back)} incidents → Codex ({effort})")
    out, secs = Codex(effort=effort, timeout=1800, retries=1).run(prompt, STORY_SCHEMA)
    issues, used = [], set()
    nums = set(ST.NUM.findall(prompt))

    def check(where, text):
        if META.search(text):
            issues.append(f"{where}: technical token '{META.search(text).group(0)}'")
        for n in ST.NUM.findall(text):
            if not (n.isdigit() and int(n) <= 10) and n not in nums:
                issues.append(f"{where}: number '{n}' not in the input")

    def ids(lst, where):
        good = [back[i] for i in lst if i in back]
        if len(good) < len(lst):
            issues.append(f"{where}: unknown incident ids {[i for i in lst if i not in back]}")
        return good
    card = {"overview": [], "storylines": [], "captions": []}
    for j, o in enumerate(out["overview"]):
        check(f"overview {j + 1}", o["text"])
        card["overview"].append({"text": o["text"], "incidents": ids(o["incidents"], f"overview {j + 1}")})
    for j, st in enumerate(out["storylines"]):
        check(f"storyline {j + 1}", st["title"] + " " + st["explanation"])
        mine = [i for i in ids(st["incidents"], f"storyline {j + 1}") if i not in used]
        used.update(mine)
        card["storylines"].append({"title": st["title"], "explanation": st["explanation"], "incidents": mine})
    for c in out["captions"]:
        if c["incident"] in back:
            check("caption", c["text"])
            card["captions"].append({"incident": back[c["incident"]], "text": c["text"]})
    text = "\n".join([o["text"] for o in card["overview"]] + [""] +
                      [f"{s['title']}: {s['explanation']}" for s in card["storylines"]])
    work.execute("INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,?,?)",
                 ("story", "all", VERSION, time.strftime("%Y-%m-%d %H:%M:%S"),
                  json.dumps({"incidents": len(back), "min_level": min_level, "seconds": round(secs)}),
                  json.dumps(card), text, json.dumps(issues)))
    work.execute("DELETE FROM cards_fts WHERE kind='story'")
    work.execute("INSERT INTO cards_fts VALUES (?,?,?)", ("story", "all", text))
    work.commit()
    log(f"story written in {secs:.0f}s; {len(card['storylines'])} storylines, {len(card['captions'])} captions, "
        f"{len(issues)} issues")
    return {"storylines": len(card["storylines"]), "captions": len(card["captions"]), "issues": issues}


# ---------------------------------------------------------------------------- queries

def get_card(con, kind, key):
    try:
        r = con.execute("SELECT card, text, issues, stats, created FROM w.cards WHERE kind=? AND key=?", (kind, key)).fetchone()
    except Exception:
        r = None
    if not r:
        return None
    return {"kind": kind, "key": key, "text": r[1], "card": json.loads(r[0]), "issues": json.loads(r[2]),
            "stats": json.loads(r[3]), "created": r[4]}


def template(con, kind, key, note):
    """Code-only card for entities too small for an LLM card: header + folded notes."""
    if kind == "link":
        a, b = key.split("~", 1)
        rows = con.execute("SELECT src, dst, type, event_id, ts FROM w.links WHERE (src=? AND dst=?) OR (src=? AND dst=?) "
                           "ORDER BY ts LIMIT 30", (a, b, b, a)).fetchall()
        lines = [f"[{r[3]}] {r[4][5:16]} {r[0]}→{r[1]} {r[2]}: {note.get(r[3], {}).get('summary', '(no note)')}" for r in rows]
        return {"kind": kind, "key": key, "text": f"{key} · {len(rows)} linked events (no LLM card)\n" + "\n".join(lines),
                "card": None, "issues": [], "stats": {"events": len(rows)}}
    evs = ST.entity_events(con, kind, key, note)
    st = ST.stats(con, kind, key, evs)
    lines = ST.collapse(evs, max_lines=12)
    head = f"{key} · {kind} · {st['events']} events · {st['first']} → {st['last']} (no LLM card)"
    mix = "Behaviour: " + ", ".join(f"{k} {v:.0%}" for k, v in st["tag_share"].items())
    return {"kind": kind, "key": key, "text": "\n".join([head, mix, *lines]), "card": None, "issues": [], "stats": st}


def resolve_entity(con, entity):
    if entity in ("all", "swarm"):
        return "all", "all"
    try:
        return "actor", Q.resolve(con, entity)
    except Q.QueryError:
        if con.execute("SELECT 1 FROM events WHERE channel=? LIMIT 1", (entity,)).fetchone():
            return "channel", entity
        raise


def trajectory(con, entity):
    kind, key = resolve_entity(con, entity)
    return get_card(con, kind, key) or template(con, kind, key, ST.notes(con))


def link(con, a, b):
    a, b = Q.resolve(con, a), Q.resolve(con, b)
    key = "~".join(sorted((a, b)))
    return get_card(con, "link", key) or template(con, "link", key, ST.notes(con))


def changes(con, entity=None, trigger=None, since=None, until=None, limit=50):
    """Change points from trajectory cards; trigger filters by the useful-link type or behaviour tag of the trigger."""
    sql, vals = "SELECT kind, key, card, stats FROM w.cards WHERE kind IN ('actor', 'channel', 'all')", []
    if entity:
        kind, key = resolve_entity(con, entity)
        sql += " AND kind=? AND key=?"
        vals += [kind, key]
    note = ST.notes(con) if trigger else {}
    ltype = dict(con.execute("SELECT event_id, type FROM w.links").fetchall()) if trigger else {}
    out = []
    for kind, key, card, st in con.execute(sql, vals):
        card = json.loads(card)
        for c in card.get("changes") or []:
            at_ts = c["at"] if kind == "all" else (con.execute("SELECT ts FROM events WHERE id=?", (c["at"],)).fetchone() or [""])[0]
            if (since and at_ts < since.replace("T", " ")) or (until and at_ts >= until.replace("T", " ")):
                continue
            if trigger and not any(ltype.get(t) == trigger or trigger in note.get(t, {}).get("tags", [])
                                   for t in c["trigger"]):
                continue
            out.append({"entity": key, "kind": kind, "at": c["at"], "ts": at_ts, "trigger": c["trigger"],
                        "trigger_kind": c["trigger_kind"], "before_after": c["before_after"]})
    out.sort(key=lambda x: x["ts"])
    return {"total": len(out), "changes": out[:limit]}


def phase_events(con, entity, phase):
    kind, key = resolve_entity(con, entity)
    c = get_card(con, kind, key)
    if not c or kind == "all":
        raise Q.QueryError("no LLM trajectory card with event phases for this entity (run `cards`)")
    phases = c["card"]["phases"]
    if not 1 <= int(phase) <= len(phases):
        raise Q.QueryError(f"phase must be 1..{len(phases)}")
    p = phases[int(phase) - 1]
    evs = ST.entity_events(con, kind, key)
    ts = {e["id"]: e["ts"] for e in evs}
    inside = [e for e in evs if ts[p["start"]] <= e["ts"] <= ts[p["end"]]]
    return {"entity": key, "phase": int(phase), "title": p["title"], "events": len(inside),
            "rows": [{"event_id": e["id"], "ts": e["ts"], "actor": e["actor"], "tags": e.get("tags"),
                      "summary": e.get("summary")} for e in inside[:200]]}


def find(con, query, limit=20):
    """Full-text search over note summaries/claims and card texts (normalised language, unlike raw event text)."""
    out = {"notes": [], "cards": []}
    try:
        out["notes"] = [{"event_id": r[0], "text": r[1]} for r in con.execute(
            "SELECT event_id, snippet(notes_fts, 1, '«', '»', ' … ', 24) FROM w.notes_fts WHERE notes_fts MATCH ? "
            "LIMIT ?", (query, limit))]
        out["cards"] = [{"kind": r[0], "key": r[1], "text": r[2]} for r in con.execute(
            "SELECT kind, key, snippet(cards_fts, 2, '«', '»', ' … ', 24) FROM w.cards_fts WHERE cards_fts MATCH ? "
            "LIMIT ?", (query, limit))]
    except Exception as ex:
        raise Q.QueryError(f"search failed ({ex}); run `structure` and `cards` first, quote phrases")
    return out


def quality(con, sample=5, seed=0):
    """Check the structure before generating hypotheses: note coverage, card verification, a sample to read."""
    import random
    total = con.execute("SELECT count(*) FROM events").fetchone()[0]
    try:
        n = con.execute("SELECT count(*), sum(responds_to <> ''), sum(continues <> ''), sum(claims <> '[]'), "
                        "sum(confidence = 'low') FROM w.tags WHERE version >= 2").fetchone()
        cards = con.execute("SELECT kind, key, issues FROM w.cards").fetchall()
    except Exception as ex:
        raise Q.QueryError(f"no structure yet ({ex}): run tag, structure, cards")
    per_kind = collections.defaultdict(lambda: [0, 0])
    for kind, key, iss in cards:
        per_kind[kind][0] += 1
        per_kind[kind][1] += not json.loads(iss)
    kinds = collections.Counter()
    for i in (json.loads(r[2]) for r in cards):
        for x in i:
            kinds[("citation" if "cites" in x else "number" if "number" in x else "order" if "after" in x or "before" in x
                   else "boundary")] += 1
    rnd = random.Random(seed)
    return {"events": total, "notes": n[0], "note_coverage": round(n[0] / total, 4) if total else None,
            "notes_with_responds_to": n[1], "notes_with_continues": n[2], "notes_with_claims": n[3],
            "low_confidence_notes": n[4],
            "cards": {k: {"n": v[0], "verified_clean": v[1]} for k, v in per_kind.items()},
            "issue_kinds": dict(kinds),
            "read_these": [f"{k}:{key}" for k, key, _ in rnd.sample(cards, min(sample, len(cards)))],
            "note": "Read the sampled cards against their events before trusting hypotheses built on them."}

