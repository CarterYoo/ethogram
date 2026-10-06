"""Clue board for an analyst: a code-computed map of the whole swarm and per-node clues.

The division of labour: an LLM reads each event once (notes, concern flags — what was done, is it shown / only
context / a claim / a plan). Code does the counting, the baselines and the time ordering here. The analyst (an LLM
or a person) reads the clues and decides what they mean — the board states numbers and where they sit against a
baseline, never a verdict or a severity.

Every clue carries where to look next: `open` names a node (`actor`, a channel, `period:<id>`, `behaviour:<tag>`,
`concern:<kind>`) and `see` lists a few event ids. Nothing here calls the LLM, so it is cheap and deterministic.

    map                          the whole swarm on one screen
    node <entity>                clues for one actor / place / period / behaviour / concern kind
"""
import bisect
import collections

from . import query as Q
from .build import epoch


def _share(n, total):
    return round(n / total, 3) if total else 0.0


def _ev(con, ids, n=3):
    return [r[0] for r in con.execute(
        f"SELECT id FROM events WHERE id IN ({','.join('?' * len(ids))}) ORDER BY ts LIMIT {n}", list(ids))] if ids else []


# ---------------------------------------------------------------------------- building blocks (code only)

def authority_clue(con):
    """Who holds authority over the system, what they act on, and what happened afterwards. All from code: the
    `removes` links (an actor removed another's content) and the remove_others / moderate behaviour tags."""
    names = Q.labels(con)
    removers = collections.Counter()
    targets_after = []  # (actor, ts) an authority removed something of theirs
    rows = []
    try:
        rows = con.execute("SELECT src, dst, ts FROM w.links WHERE type='removes'").fetchall()
    except Exception:
        return None
    if not rows:
        return None
    for src, dst, ts in rows:
        removers[src] += 1
        targets_after.append((dst, ts))
    # did the targeted actor act again after the first time an authority removed their content?
    first_hit = {}
    for a, ts in targets_after:
        if a not in first_hit or ts < first_hit[a]:
            first_hit[a] = ts
    acted_again = 0
    for a, ts in first_hit.items():
        nxt = con.execute("SELECT 1 FROM events WHERE actor=? AND ts>? LIMIT 1", (a, ts)).fetchone()
        acted_again += bool(nxt)
    top = removers.most_common(5)
    return {"who": [{"actor": names.get(a, a), "removed_events": n, "open": a} for a, n in top],
            "removals": sum(removers.values()), "actors_targeted": len(first_hit),
            "targeted_actors_active_again": acted_again,
            "note": "an authority is an actor that mostly removes/reverts others; 'active again' counts targeted "
                    "actors with any later event, not defiance of a known order"}


def _tag_counts(con):
    """tag -> (events, distinct actors); and total noted events, and per-actor tag shares. Empty if no tag run."""
    try:
        rows = con.execute("SELECT t.tag, e.actor, e.id FROM w.event_tags t JOIN events e ON e.id=t.event_id").fetchall()
    except Exception:
        return None
    tag_ev, tag_act, per_actor = collections.Counter(), collections.defaultdict(set), collections.defaultdict(collections.Counter)
    for tag, actor, eid in rows:
        tag_ev[tag] += 1
        tag_act[tag].add(actor)
        per_actor[actor][tag] += 1
    noted = con.execute("SELECT count(*) FROM w.tags").fetchone()[0]
    return tag_ev, tag_act, per_actor, noted


def behaviour_board(con, dominant_share=0.15, top=6):
    """Behaviour tags split into the dominant background (big share) and the rest, plus the actors whose mix is
    most unlike everyone else's (deviation from a pooled baseline). Reuses the tag vocabulary's own names."""
    tc = _tag_counts(con)
    if not tc:
        return None
    from .tags import VOCAB
    tag_ev, tag_act, per_actor, noted = tc
    dominant, rest = [], []
    for tag, n in tag_ev.most_common():
        row = {"behaviour": tag, "means": VOCAB.get(tag, ""), "events": n, "share": _share(n, noted),
               "actors": len(tag_act[tag]), "open": f"behaviour:{tag}"}
        (dominant if n >= dominant_share * noted else rest).append(row)
    # actors whose behaviour mix differs most from the pooled baseline of everyone else
    base = collections.Counter()
    for a, c in per_actor.items():
        base.update(c)
    base_total = sum(base.values())
    outliers = []
    for a, c in per_actor.items():
        na = sum(c.values())
        if na < 15:
            continue
        best = None
        for tag, n in c.items():
            share, b = n / na, (base[tag] - n) / (base_total - na) if base_total - na else 0
            if n >= 8 and share >= max(2 * b, b + 0.2) and (best is None or share - b > best[3]):
                best = (tag, share, b, share - b)
        if best:
            ev = _ev(con, [r[0] for r in con.execute(
                "SELECT e.id FROM w.event_tags t JOIN events e ON e.id=t.event_id WHERE e.actor=? AND t.tag=? "
                "ORDER BY e.ts LIMIT 3", (a, best[0]))])
            outliers.append({"actor": Q.labels(con).get(a, a), "behaviour": best[0], "share": round(best[1], 3),
                             "baseline": round(best[2], 3), "events": na, "see": ev, "open": a, "_d": best[3]})
    outliers = sorted(outliers, key=lambda x: -x.pop("_d"))[:top]
    return {"dominant": dominant, "rest": rest[:top], "actors_unlike_the_rest": outliers}


def reaction_board(con, top=4):
    """Behaviour regularities: after X is aimed at an actor, do they do Y more than usual? Code-computed lift over a
    baseline. These are the ready-made leads for behaviour hypotheses."""
    from .tags import available
    if not available(con):
        return None
    from .metrics import reaction
    from .signals import REACTION_TRIGGERS, REACTION_RESPONSES
    tc = _tag_counts(con)
    tag_ev = tc[0] if tc else {}
    out = []
    for trig in REACTION_TRIGGERS:
        if tag_ev.get(trig, 0) < 10:
            continue
        for resp in REACTION_RESPONSES:
            if resp == trig or tag_ev.get(resp, 0) < 10:
                continue
            try:
                r = reaction(con, resp, trigger_tag=trig)
            except Exception:
                continue
            d = r["details"]
            lift = d.get("lift_vs_other_times") or d.get("lift")
            if d["triggers"] >= 10 and d["responded"] >= 5 and (lift or 0) >= 1.5:
                out.append({"after": trig, "targets_do": resp, "rate": r["value"],
                            "baseline_other_times": d["other_times_rate"], "lift": lift,
                            "cases": d["triggers"], "see": r["evidence"][:4],
                            "open": f"behaviour:{resp}"})
    return sorted(out, key=lambda x: -(x["lift"] or 0))[:top] or None


def concern_board(con, dominant_share=0.10, top=10):
    """Concern kinds split into pervasive (a big share of ALL events — background to judge as a whole) and the rest
    (rarer, easier to read case by case). Each with how far the evidence goes (basis), reach, and whether it came
    after an authority acted. No severity — that is the analyst's call."""
    from .alignment import patterns, KINDS, NEUTRAL, get_environment, BASIS
    pats = patterns(con)
    if not pats:
        return None
    total = con.execute("SELECT count(*) FROM events").fetchone()[0]
    dominant, rest = [], []
    for p in pats:
        row = {"concern": p["kind"], "records": NEUTRAL.get(p["kind"], p["kind"]), "events": p["events"],
               "share_of_all": _share(p["events"], total), "actors": p["actors"], "basis": p["basis"],
               "after_authority_acted": p["after_authority_acted"], "often_unconfirmed": p["often_unconfirmed"],
               "see": [e["id"] for e in p["examples"][:3]], "open": f"concern:{p['kind']}"}
        (dominant if p["events"] >= dominant_share * total else rest).append(row)
    env = get_environment(con)
    return {"dominant": dominant, "rest": rest[:top],
            "basis_means": BASIS, "environment_inferred": bool(env),
            "note": "flags are an LLM's reading of single events; 'basis' says how far each goes. A kind is a "
                    "category, not a finding — open it to read the events."}


def change_board(con, top=6):
    """Time segments where a behaviour or a concern kind is far above its overall share — where to look for a shift."""
    segs = {r[0]: (r[1], r[2], r[3]) for r in con.execute("SELECT id, label, start, end FROM segments")}
    if not segs:
        return None
    out = []
    tc = _tag_counts(con)
    if tc:
        tag_ev, _, _, noted = tc
        seg_tag = con.execute("SELECT e.segment_id, t.tag, count(*) FROM w.event_tags t JOIN events e ON e.id=t.event_id "
                              "GROUP BY e.segment_id, t.tag").fetchall()
        seg_n = dict(con.execute("SELECT e.segment_id, count(*) FROM w.tags t JOIN events e ON e.id=t.event_id "
                                 "GROUP BY e.segment_id").fetchall())
        for sg, tag, n in seg_tag:
            if seg_n.get(sg, 0) < 50 or n < 15:
                continue
            base, share = tag_ev[tag] / noted, n / seg_n[sg]
            if share >= max(2 * base, base + 0.2):
                out.append({"period": segs[sg][0] or f"segment {sg}", "behaviour": tag, "share": round(share, 3),
                            "baseline": round(base, 3), "events": n, "open": f"period:{sg}", "_d": share - base})
    out = sorted(out, key=lambda x: -x.pop("_d"))[:top]
    return out or None


# ---------------------------------------------------------------------------- the map

def overview_map(con):
    names = Q.labels(con)
    total, nactors = con.execute("SELECT count(*), count(DISTINCT actor) FROM events").fetchone()
    span = con.execute("SELECT min(ts), max(ts) FROM events").fetchone()
    places = con.execute("SELECT count(DISTINCT channel) FROM events WHERE channel IS NOT NULL").fetchone()[0]
    top_actors = [{"actor": names.get(r[0], r[0]), "events": r[1], "open": r[0]} for r in con.execute(
        "SELECT actor, count(*) FROM events GROUP BY actor ORDER BY count(*) DESC LIMIT 6")]
    return {k: v for k, v in {
        "read": "A clue is where to look, not a verdict. Numbers and baselines are code-computed; open a node "
                "(actor name, channel, period:<id>, behaviour:<tag>, concern:<kind>) to see its clues and sources. "
                "'dominant' = a big share of all events (judge it as a whole); 'rest'/'unusual' = rarer, read case "
                "by case.",
        "dataset": {"events": total, "actors": nactors, "places": places, "span": [span[0], span[1]],
                    "most_active": top_actors},
        "authority": authority_clue(con),
        "behaviour": behaviour_board(con),
        "behaviour_regularities": reaction_board(con),
        "concerns": concern_board(con),
        "changes": change_board(con),
    }.items() if v is not None}


# ---------------------------------------------------------------------------- nodes

def _actor_node(con, actor):
    from .tags import available
    names = Q.labels(con)
    key = Q.resolve(con, actor)
    n, first, last = con.execute("SELECT count(*), min(ts), max(ts) FROM events WHERE actor=?", (key,)).fetchone()
    places = [r[0] for r in con.execute("SELECT DISTINCT channel FROM events WHERE actor=? AND channel IS NOT NULL "
                                        "LIMIT 8", (key,))]
    out = {"node": "actor", "actor": names.get(key, key), "events": n, "span": [first, last], "places": places}
    if available(con):
        from .tags import profile
        from .structure import _others_share
        pr = profile(con, actor=actor)["tags"]
        base = _others_share(con, key)
        out["does"] = [{"behaviour": t, "share": v["share"], "all_others": base.get(t, 0)}
                       for t, v in list(pr.items())[:6]]
    out["flags"] = _flag_summary(con, actor=key)
    # who responded to this actor, and whom it responded to (useful links, code)
    try:
        links = {d: [{"type": r[0], "actor": names.get(r[1], r[1]), "n": r[2]} for r in con.execute(
            f"SELECT type, {o}, count(*) FROM w.links WHERE {s}=? GROUP BY type, {o} ORDER BY count(*) DESC LIMIT 6",
            (key,))] for d, s, o in (("to_others", "src", "dst"), ("from_others", "dst", "src"))}
        out["links"] = links
    except Exception:
        pass
    out["authority_acted_on_it"] = _authority_on_actor(con, key)
    out["see"] = _span_examples(con, "actor", key)
    return out


def _concern_node(con, kind):
    from .alignment import patterns, KINDS, NEUTRAL, concern_events
    pats = {p["kind"]: p for p in (patterns(con) or [])}
    if kind not in pats:
        return {"node": "concern", "concern": kind, "means": KINDS.get(kind), "events": 0,
                "note": "no flags of this kind at medium/high confidence"}
    p = pats[kind]
    ce = concern_events(con, kind=kind, limit=12)
    by_actor = collections.Counter()
    for r in ce["rows"]:
        by_actor[r["actor"]] += 1
    names = Q.labels(con)
    return {"node": "concern", "concern": kind, "records": NEUTRAL.get(kind), "means": KINDS.get(kind),
            "events": p["events"], "actors": p["actors"], "basis": p["basis"],
            "after_authority_acted": p["after_authority_acted"], "often_unconfirmed": p["often_unconfirmed"],
            "top_actors": [{"actor": names.get(a, a), "flags": c, "open": a} for a, c in by_actor.most_common(6)],
            "examples": [{"event_id": r["event_id"], "actor": names.get(r["actor"], r["actor"]), "basis": r["basis"],
                          "unconfirmed": r["unconfirmed"], "quote": r["quote"]} for r in ce["rows"][:8]],
            "more": f"concern_events {{\"kind\": \"{kind}\"}}"}


def _behaviour_node(con, tag):
    from .tags import VOCAB, available
    if not available(con):
        raise Q.QueryError("no tag run yet")
    if tag not in VOCAB:
        raise Q.QueryError(f"behaviour must be one of {', '.join(VOCAB)}")
    from .metrics import tag_rate
    names = Q.labels(con)
    rows = con.execute("SELECT e.actor, count(*) FROM w.event_tags t JOIN events e ON e.id=t.event_id WHERE t.tag=? "
                       "GROUP BY e.actor ORDER BY count(*) DESC LIMIT 8", (tag,)).fetchall()
    n_by = dict(con.execute("SELECT e.actor, count(*) FROM w.tags t JOIN events e ON e.id=t.event_id GROUP BY e.actor"))
    ev = [r[0] for r in con.execute("SELECT e.id FROM w.event_tags t JOIN events e ON e.id=t.event_id WHERE t.tag=? "
                                    "ORDER BY e.ts LIMIT 3", (tag,))]
    return {"node": "behaviour", "behaviour": tag, "means": VOCAB[tag], "rate": tag_rate(con, tag=tag),
            "top_actors": [{"actor": names.get(a, a), "events": c, "share_of_its_events": _share(c, n_by.get(a, 0)),
                            "open": a} for a, c in rows], "see": ev,
            "tip": "for a regularity, compare reaction(trigger_tag or response_tag) or tag_before_after around a time"}


def _period_node(con, token):
    seg = con.execute("SELECT id, label, start, end FROM segments WHERE id=? OR label=?", (token, token)).fetchone()
    if not seg:
        raise Q.QueryError(f"no period {token!r}; see segments")
    sid, label, start, end = seg
    names = Q.labels(con)
    n = con.execute("SELECT count(*) FROM events WHERE segment_id=?", (sid,)).fetchone()[0]
    actors = [{"actor": names.get(r[0], r[0]), "events": r[1], "open": r[0]} for r in con.execute(
        "SELECT actor, count(*) FROM events WHERE segment_id=? GROUP BY actor ORDER BY count(*) DESC LIMIT 6", (sid,))]
    out = {"node": "period", "period": label or f"segment {sid}", "span": [start, end], "events": n,
           "most_active": actors}
    try:
        tags = con.execute("SELECT t.tag, count(*) FROM w.event_tags t JOIN events e ON e.id=t.event_id "
                           "WHERE e.segment_id=? GROUP BY t.tag ORDER BY count(*) DESC LIMIT 6", (sid,)).fetchall()
        noted = con.execute("SELECT count(*) FROM w.tags t JOIN events e ON e.id=t.event_id WHERE e.segment_id=?",
                            (sid,)).fetchone()[0]
        out["does"] = [{"behaviour": t, "share": _share(c, noted)} for t, c in tags]
    except Exception:
        pass
    out["flags"] = _flag_summary(con, segment=sid)
    out["see"] = [r[0] for r in con.execute("SELECT id FROM events WHERE segment_id=? ORDER BY ts LIMIT 3", (sid,))]
    return out


def _channel_node(con, channel):
    names = Q.labels(con)
    n, first, last = con.execute("SELECT count(*), min(ts), max(ts) FROM events WHERE channel=?", (channel,)).fetchone()
    if not n:
        raise Q.QueryError(f"no events in channel {channel!r}")
    actors = [{"actor": names.get(r[0], r[0]), "events": r[1], "open": r[0]} for r in con.execute(
        "SELECT actor, count(*) FROM events WHERE channel=? GROUP BY actor ORDER BY count(*) DESC LIMIT 8", (channel,))]
    out = {"node": "channel", "channel": channel, "events": n, "span": [first, last], "participants": actors}
    out["flags"] = _flag_summary(con, channel=channel)
    out["see"] = [r[0] for r in con.execute("SELECT id FROM events WHERE channel=? ORDER BY ts LIMIT 3", (channel,))]
    return out


# -- node helpers

def _flag_summary(con, actor=None, channel=None, segment=None):
    """Concern kinds on this node's events, with basis counts. Code only; empty if no align run."""
    where, v = "", []
    if actor:
        where, v = " AND e.actor=?", [actor]
    elif channel:
        where, v = " AND e.channel=?", [channel]
    elif segment is not None:
        where, v = " AND e.segment_id=?", [segment]
    try:
        rows = con.execute("SELECT c.kind, c.basis FROM w.concerns c JOIN events e ON e.id=c.event_id "
                           f"WHERE c.confidence<>'low'{where}", v).fetchall()
    except Exception:
        return None
    if not rows:
        return []
    from .alignment import NEUTRAL
    by = collections.defaultdict(collections.Counter)
    for kind, basis in rows:
        by[kind][basis or "unknown"] += 1
    return [{"concern": k, "records": NEUTRAL.get(k, k), "events": sum(c.values()), "basis": dict(c),
             "open": f"concern:{k}"} for k, c in sorted(by.items(), key=lambda kv: -sum(kv[1].values()))]


def _authority_on_actor(con, actor):
    try:
        hits = con.execute("SELECT count(*), min(ts) FROM w.links WHERE type='removes' AND dst=?", (actor,)).fetchone()
    except Exception:
        return None
    if not hits or not hits[0]:
        return {"removed": 0}
    later = con.execute("SELECT count(*) FROM events WHERE actor=? AND ts>?", (actor, hits[1])).fetchone()[0]
    return {"removed": hits[0], "first_at": hits[1][:16], "its_events_after": later}


def _span_examples(con, kind, key):
    col = {"actor": "actor"}[kind]
    rows = con.execute(f"SELECT id, ts FROM events WHERE {col}=? ORDER BY ts", (key,)).fetchall()
    if not rows:
        return {}
    out = {"first": rows[0][0], "last": rows[-1][0]}
    try:
        strong = con.execute("SELECT c.event_id FROM w.concerns c JOIN events e ON e.id=c.event_id WHERE e.actor=? "
                             "AND c.confidence='high' ORDER BY e.ts LIMIT 1", (key,)).fetchone()
        if strong:
            out["strongest_flag"] = strong[0]
    except Exception:
        pass
    return out


def node(con, entity):
    """Clues for one node. `entity` is an actor name/id, a channel, `period:<id|label>`, `behaviour:<tag>` or
    `concern:<kind>`."""
    if ":" in entity:
        pre, _, rest = entity.partition(":")
        if pre == "concern":
            return _concern_node(con, rest)
        if pre == "behaviour":
            return _behaviour_node(con, rest)
        if pre == "period":
            return _period_node(con, rest)
    if con.execute("SELECT 1 FROM events WHERE channel=? LIMIT 1", (entity,)).fetchone():
        return _channel_node(con, entity)
    return _actor_node(con, entity)
