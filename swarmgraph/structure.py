"""Summary structure, code half (no LLM): useful links, entity trajectories, change-point candidates, stats,
and verification of LLM-written cards.

Entities are what trajectories are told about: an actor, a channel (page, room, thread), or the whole swarm ('all').
A *useful link* is a relation where one actor's behaviour is directed at another actor (communicates, instructs,
delegates, returns, follows, builds on, removes, restores, responds); plain adjacency (e.g. editing a page right
after someone else, with no behaviour toward them) is left in the index but not used here.

    python3 -m swarmgraph --db INDEX structure      # after `tag`; fast, rerun any time
"""
import collections
import json
import re
import statistics
import sys

from . import query as Q
from .build import epoch
from .store import open_work

LINK_TYPES = ("delegates", "returns", "instructs", "removes", "restores", "follows", "builds_on", "communicates",
              "responds")  # strongest first; one type per (src, dst, event)
EXPLICIT = ("to_field", "at_mention", "llm_text")
INTERVENTIONS = ("delegates", "instructs", "removes", "communicates")


# ---------------------------------------------------------------------------- notes and links

def notes(con):
    """event id → note dict (from the work store, attached as `w`)."""
    try:
        rows = con.execute("SELECT event_id, tags, summary, responds_to, continues, claims, addressed_to, other "
                           "FROM w.tags").fetchall()
    except Exception:
        return {}
    return {r[0]: {"tags": json.loads(r[1]), "summary": r[2], "responds_to": r[3] or None, "continues": r[4] or None,
                   "claims": json.loads(r[5]) if r[5] else [], "addressed_to": json.loads(r[6]), "other": r[7]}
            for r in rows}


def _type_from_tags(tags, how):
    """Link type from the source event's behaviour tags. how: 'explicit' (the text addresses the other actor),
    'structural' (the event is attached to the other's event, e.g. edits right after it) or 'note' (the note says
    it responds to the other's event)."""
    t = set(tags)
    if t & {"remove_others", "moderate"}:
        return "removes"
    if how == "explicit":
        return "instructs" if "instruct" in t else "follows" if "follow" in t else "communicates"
    if "restore" in t:
        return "restores"
    if "follow" in t:
        return "follows"
    if "build_on_others" in t:
        return "builds_on"
    return "responds" if how == "note" else None


def build_links(con, work, log=print):
    """Materialise useful links in the work store (table links)."""
    note = notes(con)
    author = dict(con.execute("SELECT id, actor FROM events").fetchall())
    ts_of = dict(con.execute("SELECT id, ts FROM events").fetchall())
    best = {}  # (src, dst, event) → (rank, type, via)

    def add(src, dst, eid, typ, via):
        if not typ or not src or not dst or src == dst:
            return
        k, r = (src, dst, eid), LINK_TYPES.index(typ)
        if k not in best or r < best[k][0]:
            best[k] = (r, typ, via)

    for typ, src, dst, eid, method in con.execute(
            "SELECT type, src, dst, event_id, method FROM relations WHERE type IN ('addressed', 'invoked', 'returned')"):
        tags = note.get(eid, {}).get("tags", [])
        if typ == "invoked":
            add(src, dst, eid, "delegates", method)
        elif typ == "returned":
            add(src, dst, eid, "returns", method)
        elif method in EXPLICIT:
            add(src, dst, eid, _type_from_tags(tags, "explicit"), method)
        else:  # structural adjacency (reply field): only if the note shows behaviour toward the other's content
            add(src, dst, eid, _type_from_tags(tags, "structural"), method)
    for eid, n in note.items():
        rt = n["responds_to"]
        if rt and rt in author and author.get(eid) and author[rt] != author[eid]:
            add(author[eid], author[rt], eid, _type_from_tags(n["tags"], "note"), "note_responds_to")
    work.execute("DROP TABLE IF EXISTS links")
    work.execute("CREATE TABLE links(src TEXT, dst TEXT, type TEXT, event_id TEXT, ts TEXT, via TEXT)")
    work.executemany("INSERT INTO links VALUES (?,?,?,?,?,?)",
                     [(s, d, typ, e, ts_of.get(e), via) for (s, d, e), (_, typ, via) in best.items()])
    work.execute("CREATE INDEX ix_links_src ON links(src, ts)")
    work.execute("CREATE INDEX ix_links_dst ON links(dst, ts)")
    work.execute("CREATE INDEX ix_links_event ON links(event_id)")
    work.commit()
    counts = collections.Counter(v[1] for v in best.values())
    log(f"useful links: {sum(counts.values()):,} {dict(counts)}")
    return counts


def build_fts(con, work):
    """Full-text search over note summaries, claims and free labels (the normalised language of the notes)."""
    work.execute("DROP TABLE IF EXISTS notes_fts")
    work.execute("CREATE VIRTUAL TABLE notes_fts USING fts5(event_id UNINDEXED, body)")
    rows = work.execute("SELECT event_id, summary, claims, other, stated_goal FROM tags").fetchall()
    work.executemany("INSERT INTO notes_fts VALUES (?,?)",
                     [(r[0], " ".join(filter(None, [r[1], " ".join(json.loads(r[2]) if r[2] else []), r[3], r[4]])))
                      for r in rows])
    work.commit()


def run(db, log=lambda m: print(m, file=sys.stderr, flush=True)):
    con, work = Q.connect(db), open_work(db)
    counts = build_links(con, work, log)
    build_fts(con, work)
    return {"links": dict(counts)}


# ---------------------------------------------------------------------------- entities and trajectories

def entity_events(con, kind, key, note=None):
    note = note if note is not None else notes(con)
    if kind == "actor":
        rows = con.execute("SELECT id, ts, actor, kind, channel FROM events WHERE actor=? ORDER BY ts", (key,))
    elif kind == "channel":
        rows = con.execute("SELECT id, ts, actor, kind, channel FROM events WHERE channel=? ORDER BY ts", (key,))
    elif kind == "all":
        rows = con.execute("SELECT id, ts, actor, kind, channel FROM events ORDER BY ts")
    else:
        raise Q.QueryError("entity kind must be actor, channel or all")
    return [{"id": r[0], "ts": r[1], "actor": r[2], "kind": r[3], "channel": r[4], **note.get(r[0], {"tags": []})}
            for r in rows]


def _hist(evs):
    c = collections.Counter(t for e in evs for t in (e.get("tags") or ["(none)"]))
    n = sum(c.values()) or 1
    return {k: v / n for k, v in c.items()}


def _tvd(p, q):
    return 0.5 * sum(abs(p.get(k, 0) - q.get(k, 0)) for k in set(p) | set(q))


def candidates(con, kind, key, evs, limit=12):
    """Code-proposed change points: behaviour shift, long gap, intervention by another actor, new participant."""
    n = len(evs)
    if n < 4:
        return []
    W = max(3, min(12, n // 8))
    t = [epoch(e["ts"]) for e in evs]
    out = {}

    def add(i, reason, strength, trigger=None):
        c = out.setdefault(i, {"at": evs[i]["id"], "ts": evs[i]["ts"], "reasons": [], "trigger": [], "strength": 0})
        c["reasons"].append(reason)
        c["strength"] = max(c["strength"], strength)
        if trigger:
            c["trigger"] += [x for x in trigger if x not in c["trigger"]]

    shift = [0.0] * n
    for i in range(W, n - W + 1):
        shift[i] = _tvd(_hist(evs[i - W:i]), _hist(evs[i:i + W]))
    for i in range(W, n - W + 1):  # local maxima above threshold
        if shift[i] >= 0.5 and shift[i] == max(shift[max(W, i - W // 2):min(n - W + 1, i + W // 2 + 1)]):
            before, after = _hist(evs[i - W:i]), _hist(evs[i:i + W])
            diff = sorted(((after.get(k, 0) - before.get(k, 0)), k) for k in set(before) | set(after))
            less, more = diff[0][1], diff[-1][1]
            add(i, f"behaviour shift: more {more}, less {less} (distance {shift[i]:.2f})", shift[i])
    gaps = [b - a for a, b in zip(t, t[1:])]
    med = statistics.median(gaps) if gaps else 0
    for i, g in enumerate(gaps, 1):
        if g > max(6 * 3600, 10 * med):
            add(i, f"resumes after {g / 3600:.1f} h pause", 0.6)
    seen_tags, seen_actors = set(), set()
    for i, e in enumerate(evs):
        for tag in e.get("tags") or []:
            if tag not in seen_tags and i >= W and tag not in ("other", "filler"):
                share = sum(tag in (x.get("tags") or []) for x in evs[i:i + W]) / W
                if share >= 0.3:
                    add(i, f"new behaviour '{tag}' ({share:.0%} of next {W})", 0.5 + share / 2)
        seen_tags.update(e.get("tags") or [])
        if kind in ("channel", "all") and e["actor"] not in seen_actors and i > 0 and kind == "channel":
            add(i, f"new participant {e['actor']}", 0.4)
        seen_actors.add(e["actor"])
    if kind == "actor":  # interventions by others: the first own event within 3 h after one
        incoming = con.execute(f"SELECT ts, event_id, src, type FROM w.links WHERE dst=? AND type IN "
                               f"({','.join('?' * len(INTERVENTIONS))}) ORDER BY ts", (key, *INTERVENTIONS)).fetchall()
        for ts, eid, src, typ in incoming:
            ti = epoch(ts)
            j = next((k for k, x in enumerate(t) if x >= ti), None)
            if j is not None and 0 < j and t[j] - ti <= 3 * 3600:
                add(j, f"after {src} {typ} it", 0.55, [eid])
    ranked = sorted(out.values(), key=lambda c: -c["strength"])[:limit]
    return sorted(ranked, key=lambda c: c["ts"])


def stats(con, kind, key, evs):
    """Numbers the cards may use (the LLM gets these and must not invent others)."""
    n = len(evs)
    tagc = collections.Counter(tag for e in evs for tag in e.get("tags") or [])
    noted = sum(1 for e in evs if e.get("tags"))
    out = {"events": n, "noted": noted, "first": evs[0]["ts"][:16] if evs else None,
           "last": evs[-1]["ts"][:16] if evs else None,
           "tag_share": {k: round(v / noted, 2) for k, v in tagc.most_common(8)} if noted else {},
           "channels": len({e["channel"] for e in evs if e["channel"]}),
           "actors": len({e["actor"] for e in evs}),
           "claims": sum(len(e.get("claims") or []) for e in evs),
           "repeat_chains": sum(1 for e in evs if e.get("continues"))}
    if kind == "actor":
        base = _others_share(con, key)
        out["vs_others"] = {k: {"this": out["tag_share"].get(k, 0), "all_other_actors": base.get(k, 0)}
                            for k in list(out["tag_share"])[:6]}
        for d, col, other in (("out", "src", "dst"), ("in", "dst", "src")):
            out[f"links_{d}"] = [{"type": r[0], "actor": r[1], "n": r[2]} for r in con.execute(
                f"SELECT type, {other}, count(*) FROM w.links WHERE {col}=? GROUP BY type, {other} "
                f"ORDER BY count(*) DESC LIMIT 8", (key,))]
    elif kind == "channel":
        out["participants"] = [{"actor": a, "n": c} for a, c in collections.Counter(e["actor"] for e in evs).most_common(8)]
    return out


_TAGS = {}


def _others_share(con, actor):
    """Share of each tag among all other actors' noted events (pooled baseline)."""
    key = id(con)
    if key not in _TAGS:
        per, tot = collections.defaultdict(collections.Counter), collections.Counter()
        for a, tags in con.execute("SELECT e.actor, t.tags FROM w.tags t JOIN events e ON e.id=t.event_id"):
            tot[a] += 1
            per[a].update(json.loads(tags))
        _TAGS[key] = (per, tot)
    per, tot = _TAGS[key]
    n = sum(tot.values()) - tot[actor]
    allc = collections.Counter()
    for a, c in per.items():
        if a != actor:
            allc.update(c)
    return {t: round(v / n, 2) for t, v in allc.items()} if n else {}


def collapse(evs, max_lines=160):
    """Prompt lines: runs of similar consecutive events (same tags, continuing each other) become one line."""
    runs = []
    for e in evs:
        r = runs[-1] if runs else None
        if r and r["tags"] == e.get("tags") and (e.get("continues") in r["ids"] or e["channel"] == r["channel"]) \
                and e["actor"] == r["actor"]:
            r["ids"].append(e["id"])
            r["last"] = e
        else:
            runs.append({"tags": e.get("tags"), "ids": [e["id"]], "first": e, "last": e, "channel": e["channel"],
                         "actor": e["actor"]})
    if len(runs) > max_lines:  # keep the start, the end and an even sample of the middle
        edge = min(20, max_lines // 3)
        middle = max(1, max_lines - 2 * edge)
        keep = set(range(edge)) | set(range(len(runs) - edge, len(runs))) | \
            set(range(edge, len(runs) - edge, max(1, (len(runs) - 2 * edge) // middle)))
        runs = [r for i, r in enumerate(runs) if i in keep]
    lines = []
    for r in runs:
        f, l = r["first"], r["last"]
        extra = []
        if f.get("responds_to"):
            extra.append(f"responds_to={f['responds_to']}")
        if f.get("claims"):
            extra.append("claims: " + " | ".join(f["claims"][:2]))
        head = f"[{f['id']}] {f['ts'][5:16]} {f['actor']} @{f['channel']} {','.join(f.get('tags') or ['?'])}"
        if len(r["ids"]) > 1:
            head += f" ×{len(r['ids'])} (to [{l['id']}] {l['ts'][5:16]})"
        lines.append(f"{head}: {f.get('summary') or '(no note)'}" + (f"; last: {l.get('summary')}" if l is not f else "")
                     + (f" ({'; '.join(extra)})" if extra else ""))
    return lines


# ---------------------------------------------------------------------------- verification of LLM cards

NUM = re.compile(r"(?<![\w.])\d[\d,.:]*\d|(?<![\w.])\d(?![\w])")


def _stat_exists(stats, path):
    cur = stats
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False
    return True


def verify(card, entity_ids, allowed_ids, ts_of, input_text, stats=None):
    """Check a card written by the LLM: cited ids exist in its input, numbers appear in its input, phase boundaries
    are the entity's own events in order, triggers precede the change. Returns issues (empty = verified)."""
    issues = []
    input_nums = set(NUM.findall(input_text))

    def check_sentence(where, s):
        for i in s.get("evidence", []):
            if i.startswith("stats:"):
                if not _stat_exists(stats or {}, i[6:].strip()):
                    issues.append(f"{where}: cites {i}, which is not a stats key")
            elif i not in allowed_ids:
                issues.append(f"{where}: cites {i}, which was not in its input")
        for num in NUM.findall(s.get("text", "")):
            small = num.isdigit() and int(num) <= 10
            if not small and num not in input_nums and num.replace(",", "") not in input_nums:
                issues.append(f"{where}: number '{num}' is not in the input")

    for k in ("role", "relationship"):
        if card.get(k):
            check_sentence(k, card[k])
    for k in ("claims", "unusual", "exchanges", "effect", "unknowns", "key_points"):
        for j, s in enumerate(card.get(k) or []):
            check_sentence(f"{k}[{j}]", s)
    last = ""
    for j, p in enumerate(card.get("phases") or []):
        for x in (p.get("start"), p.get("end")):
            if x not in entity_ids:
                issues.append(f"phase {j + 1}: boundary {x} is not an event of this entity")
        if p.get("start") in ts_of:
            if ts_of[p["start"]] < last:
                issues.append(f"phase {j + 1} starts before the previous phase ends")
            last = ts_of.get(p.get("end"), last)
        summary = p.get("summary")
        check_sentence(f"phase {j + 1}", summary if isinstance(summary, dict) else {"text": summary or "", "evidence": []})
    for j, c in enumerate(card.get("changes") or []):
        at = ts_of.get(c.get("at")) if c.get("at") in entity_ids else None
        if at is None:
            issues.append(f"change {j + 1}: {c.get('at')} is not an event of this entity")
        for tr in c.get("trigger") or []:
            if tr not in allowed_ids:
                issues.append(f"change {j + 1}: trigger {tr} was not in its input")
            elif at and ts_of.get(tr, "") > at:
                issues.append(f"change {j + 1}: trigger {tr} happens after the change")
        check_sentence(f"change {j + 1}", {"text": c.get("before_after", ""), "evidence": []})
    return issues


# ---------------------------------------------------------------------------- incidents (story view, code only)

CRITERIA = {
    "triggered": "cited as the trigger of another entity's behaviour change (trajectory cards)",
    "change": "an entity's behaviour changes here (start of a new phase in its card)",
    "taken_up": "unusually many other actors responded to it or followed it (top 5% of incidents with responders)",
    "proposal": "it instructs others or sets a rule or convention (note tag instruct)",
    "mass": "unusually many events at once (top 2% of incident sizes)",
    "concern": "possibly misaligned behaviour flagged by the alignment lens (a high-confidence flag or two medium ones, "
               "of a kind that is not on more than 5% of all events)",
}


def swarm_incidents(con, min_level=2, gap=1800, cell=None):
    """The swarm as incidents: one incident = one actor's burst of activity inside one time slot (gaps <= 30 min).

    Each incident keeps its magnitudes (events, responders, triggered changes) and is checked against CRITERIA; its
    level = how many criteria it meets. Incidents at or above `min_level` are returned individually, with the
    evidenced connections between them (responses, trigger → change, same actor continuing); the rest are summed
    per time slot and behaviour group."""
    import bisect
    from .cards import buckets
    from .tags import GROUP_OF
    note = notes(con)
    slots = buckets(con)
    starts = [lo for _, lo, _ in slots]
    slot_of = lambda ts: max(0, bisect.bisect_right(starts, ts) - 1)
    ev, incs, inc_of, cur = {}, [], {}, None
    for eid, ts, actor, rt in con.execute("SELECT id, ts, actor, reply_to FROM events ORDER BY actor, ts"):
        ev[eid] = {"ts": ts, "actor": actor, "reply_to": rt}
        sl = slot_of(ts)
        if not (cur and cur["actor"] == actor and cur["slot"] == sl and epoch(ts) - epoch(cur["last_ts"]) <= gap):
            cur = {"id": eid, "actor": actor, "slot": sl, "ts": ts, "last_ts": ts, "members": []}
            incs.append(cur)
        cur["members"].append(eid)
        cur["last_ts"] = ts
        inc_of[eid] = cur["id"]
    by_id = {i["id"]: i for i in incs}

    # magnitudes
    change_at, triggers = {}, collections.defaultdict(set)
    try:
        cards = con.execute("SELECT kind, key, card FROM w.cards").fetchall()
    except Exception:
        cards = []
    for kind, key, card in cards:
        for c in json.loads(card).get("changes") or []:
            if kind != "all" and c.get("at") in ev:
                change_at[c["at"]] = f"{key}: {c.get('before_after', '')}"
            for t in c.get("trigger") or []:
                if t in inc_of and (kind == "all" or c.get("at") not in ev or inc_of.get(c.get("at")) != inc_of[t]):
                    triggers[inc_of[t]].add(f"{kind}:{key}")
    responders = collections.defaultdict(set)
    for e, n in note.items():
        t = n.get("responds_to")
        if t in ev and e in ev and ev[t]["actor"] != ev[e]["actor"]:
            responders[inc_of[t]].add(ev[e]["actor"])
    flags = collections.defaultdict(list)  # event → [(kind, confidence)]
    try:
        for eid, kind, conf in con.execute("SELECT event_id, kind, confidence FROM w.concerns WHERE confidence <> 'low'"):
            flags[eid].append((kind, conf))
    except Exception:
        pass
    # kinds flagged on more than 5% of events are reported as patterns; in the timeline they do not make an incident
    # stand out on their own (otherwise a log where most events are concerning shows nothing but concerns)
    share = collections.Counter(k for fs in flags.values() for k in {k for k, _ in fs})
    pervasive = {k for k, n in share.items() if n > 0.05 * max(1, len(ev))}
    pct = lambda xs, q: sorted(xs)[int(q * (len(xs) - 1))] if xs else 0
    mass_min = max(10, pct([len(i["members"]) for i in incs], 0.98))
    resp_min = max(2, pct([len(v) for v in responders.values() if v], 0.95))
    for i in incs:
        m = i["members"]
        tags = collections.Counter(t for e in m for t in note.get(e, {}).get("tags") or [])
        groups = collections.Counter(GROUP_OF.get(t, "other") for e in m for t in (note.get(e, {}).get("tags") or ["other"]))
        i["n"] = len(m)
        i["group"] = groups.most_common(1)[0][0]
        i["mix"] = {g: round(v / sum(groups.values()), 2) for g, v in groups.most_common()}
        i["responders"] = sorted(responders.get(i["id"], ()))
        i["triggered"] = sorted(triggers.get(i["id"], ()))
        changes = [change_at[e] for e in m if e in change_at]
        instruct = [e for e in m if "instruct" in (note.get(e, {}).get("tags") or [])]
        fl = [f for e in m for f in flags.get(e, ())]
        i["concerns"] = dict(collections.Counter(k for k, _ in fl))
        rare = [(k, c) for k, c in fl if k not in pervasive]
        solid = any(c == "high" for _, c in rare) or len(rare) >= 2
        i["met"] = [k for k, ok in (("triggered", bool(i["triggered"])), ("change", bool(changes)),
                                    ("taken_up", len(i["responders"]) >= resp_min),
                                    ("proposal", bool(instruct)),
                                    ("mass", i["n"] >= mass_min), ("concern", solid)) if ok]
        i["level"] = len(i["met"])
        lead = next((e for e in m if flags.get(e)), None) or (changes and next(e for e in m if e in change_at)) or (instruct and instruct[0]) or \
            next((e for e in m if note.get(e, {}).get("summary")), m[0])
        i["label"] = change_at.get(lead) or note.get(lead, {}).get("summary") or "(no note)"
        i["summary"] = note.get(lead, {}).get("summary") or next(
            (note[e]["summary"] for e in m if note.get(e, {}).get("summary")), "")  # plain one-liner for people
        i["lead"] = lead
        i["top_tags"] = [t for t, _ in tags.most_common(3)]

    if cell is not None:  # the incidents behind one background circle (time slot, behaviour group)
        sl, grp = cell
        below = sorted((i for i in incs if i["slot"] == sl and i["group"] == grp and i["level"] < min_level
                        and "concern" not in i["met"]),
                       key=lambda i: -i["n"])
        return {"slot": slots[sl][0], "group": grp, "events": sum(i["n"] for i in below), "incidents": len(below),
                "actors": len({i["actor"] for i in below}),
                "top": [{k: i[k] for k in ("id", "actor", "ts", "last_ts", "n", "summary", "met")} for i in below[:12]]}
    keep = [i for i in incs if i["level"] >= min_level or "concern" in i["met"]]  # concerns are never background
    keep_ids = {i["id"] for i in keep}
    rest = collections.defaultdict(lambda: [0, 0])
    for i in incs:
        if i["id"] not in keep_ids:
            r = rest[(i["slot"], i["group"])]
            r[0] += i["n"]
            r[1] += 1
    edges, seen = [], set()

    def edge(a, b, typ):
        if a != b and a in keep_ids and b in keep_ids and (a, b) not in seen and by_id[a]["ts"] <= by_id[b]["ts"]:
            seen.add((a, b))
            edges.append({"from": a, "to": b, "type": typ})
    ltype = dict(con.execute("SELECT event_id, type FROM w.links").fetchall()) if cards is not None else {}
    for i in keep:
        for m in i["members"][:200]:
            cur, hops = m, 0
            while hops < 4:  # follow reply/response pointers back to an earlier kept incident
                nxt = note.get(cur, {}).get("responds_to") or ev[cur]["reply_to"]
                if not nxt or nxt not in ev:
                    break
                hops += 1
                if inc_of[nxt] in keep_ids and inc_of[nxt] != i["id"]:
                    edge(inc_of[nxt], i["id"], (ltype.get(m) or "responds") if hops == 1 else "via responses")
                    break
                cur = nxt
    for kind, key, card in cards:
        for c in json.loads(card).get("changes") or []:
            if c.get("at") in inc_of:
                for t in c.get("trigger") or []:
                    if t in inc_of:
                        edge(inc_of[t], inc_of[c["at"]], "triggered")
    last_of_actor = {}
    for i in sorted(keep, key=lambda x: x["ts"]):
        if i["actor"] in last_of_actor:
            edge(last_of_actor[i["actor"]], i["id"], "same actor")
        last_of_actor[i["actor"]] = i["id"]
    out = [{k: i[k] for k in ("id", "actor", "slot", "ts", "last_ts", "n", "group", "mix", "responders", "triggered",
                              "met", "level", "label", "summary", "lead", "top_tags", "concerns")} for i in sorted(keep, key=lambda x: x["ts"])]
    return {"slots": [{"label": l, "lo": lo, "hi": hi} for l, lo, hi in slots], "incidents": out,
            "rest": [{"slot": sl, "group": g, "n": v[0], "incidents": v[1]} for (sl, g), v in sorted(rest.items())],
            "edges": edges, "criteria": CRITERIA, "thresholds": {"mass_min_events": mass_min,
                                                                 "taken_up_min_actors": resp_min},
            "min_level": min_level,
            "levels": dict(collections.Counter(i["level"] for i in incs)), "total_incidents": len(incs)}
