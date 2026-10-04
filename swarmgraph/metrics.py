"""Deterministic metrics for testing hypotheses. Numbers come from code, never from an LLM.

Each metric returns {"metric", "params", "value", "details", "evidence"}; `value` is the headline number a
pre-registered expectation is checked against (or any key of `details` via expect.field).
"""
import bisect
import statistics
import time

from .build import epoch
from .query import QueryError, resolve, window

CATALOG = {
    "reply_rate": {
        "doc": "Share of the actor's 'addressed' relations (direction out: it addressed others; in: others addressed it) "
               "that got an explicit address back within the reply window. details.others_rate = same for all other "
               "actors in the window (baseline).",
        "params": {"actor": "str", "direction": "out|in (default out)", "peer": "str (optional)", "since": "ISO",
                   "until": "ISO"}},
    "volume": {
        "doc": "How much the actor did in the window compared with every other actor: value = count; details.rank, "
               "details.z (z-score among actors with >0), details.median_other.",
        "params": {"actor": "str", "what": "events|addressed_out|addressed_in|invoked", "since": "ISO", "until": "ISO"}},
    "before_after": {
        "doc": "A measure for the actor in `days` before vs after time `at`. value = after/before ratio. measures: "
               "addressed_per_active_hour, events_per_active_hour, reply_rate_out, median_gap_min (between consecutive "
               "addressed-out relations).",
        "params": {"actor": "str", "at": "ISO", "days": "number (default 3)", "measure": "str"}},
    "pair": {
        "doc": "Relations between two actors: value = a→b count; details has b→a, reciprocity (min/max) and answered rates.",
        "params": {"a": "str", "b": "str", "type": "addressed|invoked|returned|read (default addressed)",
                   "since": "ISO", "until": "ISO"}},
    "first_use": {
        "doc": "Who used a full-text query first and in what order it spread. value = number of distinct actors using it; "
               "details.first = earliest user, details.adopters = [(actor, first time)].",
        "params": {"query": "FTS5 query", "since": "ISO", "until": "ISO"}},
    "share": {
        "doc": "Share of the actor's addressed-out relations that go to a set of actors (`to`) or to its own lineage "
               "(to='lineage'). value = share.",
        "params": {"actor": "str", "to": "[str] or 'lineage'", "since": "ISO", "until": "ISO"}},
    "burst": {
        "doc": "Largest number of addressed-out relations by the actor inside any `minutes`-long window. value = max; "
               "details.median_active_window = typical count in windows where it addressed anyone.",
        "params": {"actor": "str", "minutes": "number (default 10)", "since": "ISO", "until": "ISO"}},
    # --- behaviour metrics over LLM tags (run `tag` first; see tag_profile for the vocabulary). Every `tag`,
    # `trigger_tag` and `response_tag` may also be a concern kind from the alignment lens: "concern:<kind>".
    "tag_rate": {
        "doc": "Share of the actor's tagged events carrying behaviour `tag` (or free label containing `other`). "
               "details.others_rate = same share for all other actors (baseline), details.lift = value/others_rate. "
               "Without actor: share over everyone.",
        "params": {"tag": "vocabulary tag", "other": "free-label substring (instead of tag)", "actor": "str (optional)",
                   "since": "ISO", "until": "ISO"}},
    "tag_before_after": {
        "doc": "Share of events with `tag` in `days` before vs after time `at` (one actor or everyone). "
               "value = after_share / before_share (null when before_share is 0); details.diff = after - before "
               "(use expect field 'diff' when the behaviour may be absent before).",
        "params": {"tag": "str", "at": "ISO", "days": "number (default 3)", "actor": "str (optional)"}},
    "reaction": {
        "doc": "How actors react when something is done to them: triggers = relations (any method) whose event carries "
               "`trigger_tag` (optional) from `trigger_actor` (optional) to `actor` (optional). value = share of "
               "triggers after which the target produced `response_tag` within `window_min`. Baselines: "
               "details.before_rate (window just before; inflated when agents act in bursts) and "
               "details.other_times_rate (the same target in windows 2-3 widths away; prefer this one); "
               "details.lift / lift_vs_other_times.",
        "params": {"response_tag": "str", "trigger_tag": "str", "trigger_actor": "str", "actor": "str",
                   "window_min": "number (default 60)", "since": "ISO", "until": "ISO"}},
    "channel_reaction": {
        "doc": "Reaction in a place rather than by a person: after an event with `trigger_tag` (optionally by "
               "`trigger_actor`) in a channel/page, share of triggers followed by `response_tag` in the same channel "
               "within `window_min`, by anyone (by='any') or by others than the trigger's author (by='others'). "
               "Catches responses under a different name. Same baselines as reaction (before_rate, other_times_rate).",
        "params": {"response_tag": "str", "trigger_tag": "str", "trigger_actor": "str", "by": "any|others",
                   "window_min": "number (default 60)", "since": "ISO", "until": "ISO"}},
    "tag_spread": {
        "doc": "How a behaviour spread: actors ordered by first use of `tag` (or free label containing `other`). "
               "value = number of adopters; details.linked_share = share of later adopters that had a relation "
               "(any type, either direction) with an earlier adopter before their first use; "
               "details.base_linked_share = the same share for all actors active by then (chance baseline); "
               "details.contact_then_adoption = example (contact event, first use) pairs, which are the evidence.",
        "params": {"tag": "str", "other": "str", "since": "ISO", "until": "ISO"}},
}


def _addressed(con, actor, direction, since, until, peer=None):
    w, v = window("", since, until)
    col, other = ("src", "dst") if direction == "out" else ("dst", "src")
    pw = f" AND {other}=?" if peer else ""
    return con.execute(f"SELECT event_id, ts, answered_by FROM relations WHERE type='addressed' AND {col}=?{pw}{w} "
                       f"ORDER BY ts", [actor, *([peer] if peer else []), *v]).fetchall()


def reply_rate(con, actor, direction="out", peer=None, since=None, until=None):
    aid, pid = resolve(con, actor), resolve(con, peer) if peer else None
    rows = _addressed(con, aid, direction, since, until, pid)
    w, v = window("", since, until)
    col = "src" if direction == "out" else "dst"
    others = con.execute(f"SELECT count(*), count(answered_by) FROM relations WHERE type='addressed' AND {col}<>?{w}",
                         [aid, *v]).fetchone()
    n, ans = len(rows), sum(1 for r in rows if r[2])
    return {"value": round(ans / n, 4) if n else None,
            "details": {"n": n, "answered": ans, "others_n": others[0],
                        "others_rate": round(others[1] / others[0], 4) if others[0] else None},
            "evidence": [r[0] for r in rows if not r[2]][:5] + [r[0] for r in rows if r[2]][:5]}


def volume(con, actor, what="addressed_out", since=None, until=None):
    aid = resolve(con, actor)
    w, v = window("", since, until)
    sql = {"events": f"SELECT actor, count(*) FROM events WHERE 1{w} GROUP BY actor",
           "addressed_out": f"SELECT src, count(*) FROM relations WHERE type='addressed'{w} GROUP BY src",
           "addressed_in": f"SELECT dst, count(*) FROM relations WHERE type='addressed'{w} GROUP BY dst",
           "invoked": f"SELECT src, count(*) FROM relations WHERE type='invoked'{w} GROUP BY src"}.get(what)
    if not sql:
        raise QueryError(f"volume.what must be one of events, addressed_out, addressed_in, invoked")
    counts = dict(con.execute(sql, v).fetchall())
    mine = counts.get(aid, 0)
    vals = sorted(counts.values(), reverse=True)
    others = [c for k, c in counts.items() if k != aid]
    mu = statistics.mean(vals) if vals else 0
    sd = statistics.pstdev(vals) if len(vals) > 1 else 0
    return {"value": mine, "details": {"rank": (vals.index(mine) + 1) if mine in vals else None, "actors": len(vals),
                                       "z": round((mine - mu) / sd, 2) if sd else None,
                                       "median_other": statistics.median(others) if others else None},
            "evidence": []}


def _active_hours(ts_list):
    return max(1, len({t[:13] for t in ts_list}))


def before_after(con, actor, at, days=3, measure="addressed_per_active_hour"):
    aid = resolve(con, actor)
    t = epoch(at.replace("T", " "))
    fmt = lambda s: time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(s))
    lo, mid, hi = fmt(t - days * 86400), fmt(t), fmt(t + days * 86400)

    def measure_in(since, until):
        out = _addressed(con, aid, "out", since, until)
        if measure == "addressed_per_active_hour":
            ev = [r[0] for r in con.execute("SELECT ts FROM events WHERE actor=? AND ts>=? AND ts<?", (aid, since, until))]
            return round(len(out) / _active_hours(ev), 3) if ev else None, len(out)
        if measure == "events_per_active_hour":
            ev = [r[0] for r in con.execute("SELECT ts FROM events WHERE actor=? AND ts>=? AND ts<?", (aid, since, until))]
            return round(len(ev) / _active_hours(ev), 3) if ev else None, len(ev)
        if measure == "reply_rate_out":
            return (round(sum(1 for r in out if r[2]) / len(out), 3) if out else None), len(out)
        if measure == "median_gap_min":
            ts = sorted(epoch(r[1]) for r in out)
            gaps = [(b - a) / 60 for a, b in zip(ts, ts[1:])]
            return (round(statistics.median(gaps), 2) if gaps else None), len(out)
        raise QueryError(f"unknown measure {measure}")
    before, n_b = measure_in(lo, mid)
    after, n_a = measure_in(mid, hi)
    ratio = round(after / before, 3) if before and after is not None else None
    ev = [r[0] for r in _addressed(con, aid, "out", lo, mid)[-3:]] + [r[0] for r in _addressed(con, aid, "out", mid, hi)[:3]]
    return {"value": ratio, "details": {"before": before, "after": after, "n_before": n_b, "n_after": n_a,
                                        "window": [lo, mid, hi], "measure": measure}, "evidence": ev}


def pair(con, a, b, type="addressed", since=None, until=None):
    aid, bid = resolve(con, a), resolve(con, b)
    w, v = window("", since, until)
    q = f"SELECT count(*), count(answered_by) FROM relations WHERE type=? AND src=? AND dst=?{w}"
    ab = con.execute(q, [type, aid, bid, *v]).fetchone()
    ba = con.execute(q, [type, bid, aid, *v]).fetchone()
    ev = [r[0] for r in con.execute(f"SELECT event_id FROM relations WHERE type=? AND ((src=? AND dst=?) OR (src=? AND dst=?)){w} "
                                    f"ORDER BY ts LIMIT 6", [type, aid, bid, bid, aid, *v])]
    return {"value": ab[0], "details": {"b_to_a": ba[0], "reciprocity": round(min(ab[0], ba[0]) / max(ab[0], ba[0]), 3)
                                        if max(ab[0], ba[0]) else None,
                                        "a_to_b_answered_rate": round(ab[1] / ab[0], 3) if ab[0] else None,
                                        "b_to_a_answered_rate": round(ba[1] / ba[0], 3) if ba[0] else None},
            "evidence": ev}


def first_use(con, query, since=None, until=None):
    w, v = window("e", since, until)
    rows = con.execute(f"SELECT e.actor, min(e.ts), e.id FROM events_fts JOIN events e ON e.rid = events_fts.rowid "
                       f"WHERE events_fts MATCH ?{w} AND e.kind <> 'self_report' GROUP BY e.actor ORDER BY min(e.ts)",
                       [query, *v]).fetchall()
    adopters = [[r[0], r[1]] for r in rows]
    first_ids = [r[0] for r in con.execute(
        f"SELECT e.id FROM events_fts JOIN events e ON e.rid = events_fts.rowid WHERE events_fts MATCH ?{w} "
        f"AND e.kind <> 'self_report' ORDER BY e.ts LIMIT 5", [query, *v])]
    return {"value": len(adopters), "details": {"first": adopters[0][0] if adopters else None,
                                                "first_ts": adopters[0][1] if adopters else None, "adopters": adopters[:50]},
            "evidence": first_ids}


def share(con, actor, to, since=None, until=None):
    aid = resolve(con, actor)
    if to == "lineage":
        lin = con.execute("SELECT lineage FROM actors WHERE id=?", (aid,)).fetchone()[0]
        targets = {r[0] for r in con.execute("SELECT id FROM actors WHERE lineage=? AND id<>?", (lin, aid))} if lin else set()
    else:
        targets = {resolve(con, t) for t in (to if isinstance(to, list) else [to])}
    w, v = window("", since, until)
    rows = con.execute(f"SELECT dst, event_id FROM relations WHERE type='addressed' AND src=?{w}", [aid, *v]).fetchall()
    hit = [r for r in rows if r[0] in targets]
    return {"value": round(len(hit) / len(rows), 4) if rows else None,
            "details": {"n": len(rows), "to_set": len(hit), "set_size": len(targets)}, "evidence": [r[1] for r in hit[:6]]}


def burst(con, actor, minutes=10, since=None, until=None):
    aid = resolve(con, actor)
    rows = _addressed(con, aid, "out", since, until)
    ts = [epoch(r[1]) for r in rows]
    best, best_i, per = 0, 0, []
    for i, t in enumerate(ts):
        j = bisect.bisect_right(ts, t + minutes * 60)
        per.append(j - i)
        if j - i > best:
            best, best_i = j - i, i
    return {"value": best, "details": {"minutes": minutes, "n": len(ts),
                                       "median_active_window": statistics.median(per) if per else None,
                                       "window_start": rows[best_i][1] if rows else None},
            "evidence": [r[0] for r in rows[best_i:best_i + best]][:8]}


# ---------------------------------------------------------------------------- behaviour metrics (LLM tags)

def tag_events(tag):
    """SQL subquery (and value) for the events carrying a behaviour tag, or a concern kind written 'concern:<kind>'
    (alignment flags at medium or high confidence) — so every behaviour metric also works on concerns."""
    from .alignment import KINDS
    from .tags import VOCAB
    if tag.startswith("concern:"):
        if tag[8:] not in KINDS:
            raise QueryError(f"concern kind must be one of {', '.join(KINDS)}")
        return "SELECT event_id FROM w.concerns WHERE kind=? AND confidence <> 'low'", tag[8:]
    if tag not in VOCAB:
        raise QueryError(f"tag must be one of {', '.join(VOCAB)} or concern:<kind>")
    return "SELECT event_id FROM w.event_tags WHERE tag=?", tag


def _tag_filter(tag, other):
    if tag:
        sub, val = tag_events(tag)
        return f"t.event_id IN ({sub})", [val]
    if other:
        return "lower(t.other) LIKE ?", [f"%{other.lower()}%"]
    raise QueryError("give a tag (vocabulary) or other (free-label substring)")


def _shares(con, cond, cv, extra="", ev=(), since=None, until=None):
    w, v = window("e", since, until)
    rows = con.execute(f"SELECT e.actor, count(*), sum(CASE WHEN {cond} THEN 1 ELSE 0 END) FROM w.tags t "
                       f"JOIN events e ON e.id=t.event_id WHERE 1{extra}{w} GROUP BY e.actor", cv + list(ev) + v).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def tag_rate(con, tag=None, other=None, actor=None, since=None, until=None):
    from .tags import require
    require(con)
    cond, cv = _tag_filter(tag, other)
    per = _shares(con, cond, cv, since=since, until=until)
    aid = resolve(con, actor) if actor else None
    n, k = per.get(aid, (0, 0)) if aid else (sum(x[0] for x in per.values()), sum(x[1] for x in per.values()))
    on = sum(x[0] for a, x in per.items() if a != aid) if aid else None
    ok = sum(x[1] for a, x in per.items() if a != aid) if aid else None
    rate = round(k / n, 4) if n else None
    others = round(ok / on, 4) if on else None
    w, v = window("e", since, until)
    aw, av = (" AND e.actor=?", [aid]) if aid else ("", [])
    ev = [r[0] for r in con.execute(f"SELECT e.id FROM w.tags t JOIN events e ON e.id=t.event_id WHERE {cond}{aw}{w} "
                                    f"ORDER BY e.ts LIMIT 8", cv + av + v)]
    top = sorted(((x[1] / x[0], a, x[0]) for a, x in per.items() if x[0] >= 10), reverse=True)[:5]
    return {"value": rate, "details": {"n": n, "count": k, "others_n": on, "others_count": ok, "others_rate": others,
                                       "lift": round(rate / others, 3) if rate is not None and others else None,
                                       "top_actors_by_share": [[a, round(r, 3), m] for r, a, m in top]},
            "evidence": ev}


def tag_before_after(con, tag, at, days=3, actor=None):
    from .tags import require
    require(con)
    cond, cv = _tag_filter(tag, None)
    t = epoch(at.replace("T", " "))
    fmt = lambda x: time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(x))
    lo, mid, hi = fmt(t - days * 86400), fmt(t), fmt(t + days * 86400)
    aid = resolve(con, actor) if actor else None
    extra, ev = (" AND e.actor=?", [aid]) if aid else ("", [])

    def share(a, b):
        per = _shares(con, cond, cv, extra, ev, a, b)
        n, k = sum(x[0] for x in per.values()), sum(x[1] for x in per.values())
        return (round(k / n, 4) if n else None), n, k
    (b, nb, kb), (a, na, ka) = share(lo, mid), share(mid, hi)
    w1, v1 = window("e", lo, mid)
    w2, v2 = window("e", mid, hi)
    q = f"SELECT e.id FROM w.tags t JOIN events e ON e.id=t.event_id WHERE {cond}{extra}"
    evid = [r[0] for r in con.execute(q + w1 + " ORDER BY e.ts DESC LIMIT 3", cv + ev + v1)] + \
           [r[0] for r in con.execute(q + w2 + " ORDER BY e.ts LIMIT 3", cv + ev + v2)]
    return {"value": round(a / b, 3) if a is not None and b else None,
            "details": {"before_share": b, "after_share": a, "diff": round(a - b, 4) if a is not None and b is not None else None,
                        "n_before": nb, "n_after": na, "count_before": kb, "count_after": ka, "window": [lo, mid, hi]},
            "evidence": evid}


def reaction(con, response_tag, trigger_tag=None, trigger_actor=None, actor=None, window_min=60, since=None, until=None):
    from .tags import require
    require(con)
    _tag_filter(response_tag, None)
    w, v = window("r", since, until)
    sql, vals = "", []
    if trigger_tag:
        _tag_filter(trigger_tag, None)
        sub, val = tag_events(trigger_tag)
        sql += f" AND r.event_id IN ({sub})"
        vals.append(val)
    if trigger_actor:
        sql += " AND r.src=?"
        vals.append(resolve(con, trigger_actor))
    if actor:
        sql += " AND r.dst=?"
        vals.append(resolve(con, actor))
    triggers = con.execute(f"SELECT DISTINCT r.event_id, r.dst, r.ts FROM relations r WHERE r.type='addressed'{sql}{w} "
                           f"ORDER BY r.ts", vals + v).fetchall()
    resp = {}
    sub, val = tag_events(response_tag)
    for a, ts, eid in con.execute(f"SELECT e.actor, e.ts, e.id FROM events e WHERE e.id IN ({sub}) ORDER BY e.ts", (val,)):
        resp.setdefault(a, ([], []))
        resp[a][0].append(epoch(ts))
        resp[a][1].append(eid)
    return _react(triggers, resp, window_min, active=_actor_activity(con))


def _react(triggers, resp, window_min, active=None):
    """triggers [(event_id, key, ts)], resp {key: ([times], [ids])} → after-window rate vs two baselines: the window
    just before, and the same key at other times (windows 2-3 widths before and after, which avoid the burst around
    the trigger).

    active {key: sorted times of ALL the key's events}: when given, windows are compared only where the key is
    active — the window after the trigger must have activity, and a before/other window counts only if it does. That
    answers "how often is the response done WHEN ACTIVE" and stops agents that only work in sessions from making any
    pair look linked (the other-times windows land in the silent hours). Triggers without any active other window
    have no baseline and are left out (`details.no_baseline`)."""
    win = window_min * 60
    has = lambda times, lo, hi: bisect.bisect_left(times, lo) < len(times) and times[bisect.bisect_left(times, lo)] < hi
    act = (lambda key, lo, hi: bisect.bisect_left(active.get(key, []), hi) > bisect.bisect_left(active.get(key, []), lo)) \
        if active is not None else (lambda key, lo, hi: True)
    after = before = other = other_n = before_n = kept = silent = no_base = 0
    ev_trig, ev_resp = [], []
    for eid, key, ts in triggers:
        t = epoch(ts)
        times, ids = resp.get(key, ([], []))
        if active is not None:
            if not act(key, t + 1e-6, t + win + 1e-6):  # nobody active afterwards (session over): not comparable
                silent += 1
                continue
            offs = [k for k in (-4, -3, 2, 3) if act(key, t + k * win, t + (k + 1) * win)]
            if not offs:
                no_base += 1
                continue
        else:
            offs = [-4, -3, 2, 3]
        kept += 1
        i = bisect.bisect_right(times, t)
        if i < len(times) and times[i] - t <= win:
            after += 1
            if len(ev_trig) < 4:
                ev_trig.append(eid)
                ev_resp.append(ids[i])
        if act(key, t - win, t):
            before_n += 1
            before += has(times, t - win, t)
        for k in offs:
            other += has(times, t + k * win, t + (k + 1) * win)
            other_n += 1
    n = kept
    rate, brate = (round(after / n, 4) if n else None), (round(before / before_n, 4) if before_n else None)
    orate = round(other / other_n, 4) if other_n else None
    return {"value": rate, "details": {"triggers": n, "responded": after, "before_rate": brate,
                                       "responded_before": before, "other_times_rate": orate, "window_min": window_min,
                                       "lift": round(rate / brate, 3) if rate is not None and brate else None,
                                       "lift_vs_other_times": round(rate / orate, 3) if rate is not None and orate else None,
                                       "silent_after": silent, "no_baseline": no_base,
                                       "conditioned_on_activity": active is not None},
            "evidence": ev_trig + ev_resp}


_ACT = {}


def _actor_activity(con):
    """actor → sorted epochs of all the actor's events (cached per connection and index size)."""
    key = (id(con), con.execute("SELECT count(*) FROM events").fetchone()[0])
    if key not in _ACT:
        _ACT.clear()
        acts = {}
        for a, ts in con.execute("SELECT actor, ts FROM events ORDER BY ts"):
            acts.setdefault(a, []).append(epoch(ts))
        _ACT[key] = acts
    return _ACT[key]


def channel_reaction(con, response_tag, trigger_tag=None, trigger_actor=None, by="any", window_min=60, since=None,
                     until=None):
    """Like reaction, but for a place instead of a person: after an event with trigger_tag (by trigger_actor) in a
    channel, does anyone (by='any') or anyone other than the trigger's author (by='others') produce response_tag in
    the same channel within the window? Catches responses under a different name (e.g. a page recreated by a new
    label after deletion)."""
    from .tags import require
    require(con)
    _tag_filter(response_tag, None)
    w, v = window("e", since, until)
    sql, vals = "", []
    if trigger_tag:
        _tag_filter(trigger_tag, None)
        sub, val = tag_events(trigger_tag)
        sql += f" AND e.id IN ({sub})"
        vals.append(val)
    if trigger_actor:
        sql += " AND e.actor=?"
        vals.append(resolve(con, trigger_actor))
    trig = con.execute(f"SELECT e.id, e.channel, e.ts, e.actor FROM events e WHERE e.channel IS NOT NULL{sql}{w} "
                       f"ORDER BY e.ts", vals + v).fetchall()
    resp = {}
    sub, val = tag_events(response_tag)
    for ch, ts, eid, a in con.execute(f"SELECT e.channel, e.ts, e.id, e.actor FROM events e WHERE e.id IN ({sub}) "
                                      f"AND e.channel IS NOT NULL ORDER BY e.ts", (val,)):
        resp.setdefault(ch, []).append((epoch(ts), eid, a))
    if by == "others":  # key responses by (channel, trigger author) so the author's own responses are excluded
        keyed, triggers = {}, []
        for eid, ch, ts, a in trig:
            rows = [r for r in resp.get(ch, []) if r[2] != a]
            keyed[(ch, a)] = ([r[0] for r in rows], [r[1] for r in rows])
            triggers.append((eid, (ch, a), ts))
        chan = {}
        for ch, ts, a in con.execute("SELECT channel, ts, actor FROM events WHERE channel IS NOT NULL ORDER BY ts"):
            chan.setdefault(ch, []).append((epoch(ts), a))
        active = {(ch, a): [e for e, x in chan.get(ch, []) if x != a] for (ch, a) in keyed}
        return _react(triggers, keyed, window_min, active=active)
    keyed = {ch: ([r[0] for r in rows], [r[1] for r in rows]) for ch, rows in resp.items()}
    active = {}
    for ch, ts in con.execute("SELECT channel, ts FROM events WHERE channel IS NOT NULL ORDER BY ts"):
        active.setdefault(ch, []).append(epoch(ts))
    return _react([(eid, ch, ts) for eid, ch, ts, _ in trig], keyed, window_min, active=active)


def _spread(con, first):
    """first = [(actor, ts, event_id)] ordered by ts → (linked_share, base_linked_share, pairs). pairs: for adopters
    who had contact with an earlier adopter before their first use, (adopter, first-use event, contact event, earlier
    adopter) — the event-level evidence for the spread."""
    rel = con.execute("SELECT src, dst, ts, event_id FROM relations ORDER BY ts").fetchall()
    first_active = dict(con.execute("SELECT actor, min(ts) FROM events GROUP BY actor").fetchall())
    contacts = {}  # actor → {other: (first contact ts, event id)}
    for s_, d, ts, eid in rel:
        contacts.setdefault(s_, {}).setdefault(d, (ts, eid))
        contacts.setdefault(d, {}).setdefault(s_, (ts, eid))
    linked, base, pairs = [], [], []
    earlier = {}

    def contact(x, ts):
        hits = [(c[0], c[1], o) for o, c in contacts.get(x, {}).items() if c[0] < ts and o in earlier]
        return min(hits) if hits else None
    for k, (a, ts, eid) in enumerate(first):
        if k:
            c = contact(a, ts)
            linked.append(c is not None)
            if c and len(pairs) < 12:
                pairs.append({"adopter": a, "first_use": eid, "first_use_ts": ts, "contact_event": c[1],
                              "contact_ts": c[0], "earlier_adopter": c[2]})
            pool = [x for x, t0 in first_active.items() if t0 < ts and x not in earlier and x != a]
            if pool:
                step = max(1, len(pool) // 200)  # sample to keep it cheap
                sample = pool[::step]
                base.append(sum(contact(x, ts) is not None for x in sample) / len(sample))
        earlier[a] = ts
    return (round(sum(linked) / len(linked), 4) if linked else None,
            round(sum(base) / len(base), 4) if base else None, pairs)


def tag_spread(con, tag=None, other=None, since=None, until=None):
    from .tags import require
    require(con)
    cond, cv = _tag_filter(tag, other)
    w, v = window("e", since, until)
    first = con.execute(f"SELECT e.actor, min(e.ts), e.id FROM w.tags t JOIN events e ON e.id=t.event_id "
                        f"WHERE {cond}{w} GROUP BY e.actor ORDER BY min(e.ts)", cv + v).fetchall()
    linked, base, pairs = _spread(con, first)
    return {"value": len(first), "details": {"first": first[0][0] if first else None,
                                             "first_ts": first[0][1] if first else None,
                                             "adopters": [[a, t] for a, t, _ in first[:30]],
                                             "linked_share": linked, "base_linked_share": base,
                                             "contact_then_adoption": pairs},
            "evidence": [x for p in pairs[:6] for x in (p["contact_event"], p["first_use"])] or [r[2] for r in first[:8]]}


FUNCS = {"reply_rate": reply_rate, "volume": volume, "before_after": before_after, "pair": pair,
         "first_use": first_use, "share": share, "burst": burst,
         "tag_rate": tag_rate, "tag_before_after": tag_before_after, "reaction": reaction,
         "channel_reaction": channel_reaction, "tag_spread": tag_spread}


def run(con, metric, params):
    if metric not in FUNCS:
        raise QueryError(f"unknown metric '{metric}'; available: {', '.join(FUNCS)}")
    try:
        out = FUNCS[metric](con, **(params or {}))
    except TypeError as ex:
        raise QueryError(f"bad params for {metric}: {ex}; expected {CATALOG[metric]['params']}")
    return {"metric": metric, "params": params, **out}


def check(result, expect):
    """Evaluate a pre-registered expectation {field?, op, value} against a metric result → (passed, observed)."""
    field = (expect.get("field") or "value").split("details.", 1)[-1]  # accept "details.x" as well as "x"
    observed = result["value"] if field == "value" else result["details"].get(field)
    if observed is None:
        return None, None
    op, target = expect.get("op"), expect.get("value")
    ops = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
           "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
           "between": lambda a, b: b[0] <= a <= b[1], "outside": lambda a, b: a < b[0] or a > b[1]}
    if op not in ops:
        raise QueryError(f"expect.op must be one of {', '.join(ops)}")
    if isinstance(target, str) and target in result["details"]:  # compare with a baseline in details
        target = result["details"][target]
        if target is None:
            return None, observed
    return bool(ops[op](observed, target)), observed
