"""Deterministic anomaly signals: where a hypothesis generator (human or LLM) should look first."""
import bisect
import statistics

from .build import epoch
from .query import labels, window


def detect(con, since=None, until=None, limit=40):
    w, v = window("", since, until)
    names = labels(con)
    out = []

    def add(kind, actors, value, note, evidence, **extra):
        out.append({"type": kind, "actors": actors, "names": [names.get(a, a) for a in actors], "value": value,
                    "note": note, "evidence": evidence, **extra})

    agents = {r[0] for r in con.execute("SELECT id FROM actors WHERE kind='agent'")}
    rows = con.execute(f"SELECT src, dst, event_id, ts, answered_by FROM relations WHERE type='addressed'{w}", v).fetchall()
    out_n, ans, pairs, ev_by = {}, {}, {}, {}
    for s, d, e, ts, a in rows:
        out_n[s] = out_n.get(s, 0) + 1
        ans[s] = ans.get(s, 0) + (1 if a else 0)
        pairs[(s, d)] = pairs.get((s, d), 0) + 1
        ev_by.setdefault(s, []).append((ts, e, a))

    # 1. volume outliers
    vals = [out_n.get(a, 0) for a in agents]
    if len(vals) > 3:
        mu, sd = statistics.mean(vals), statistics.pstdev(vals)
        for a in agents:
            z = (out_n.get(a, 0) - mu) / sd if sd else 0
            if z >= 2:
                add("volume_outlier", [a], out_n[a], f"addresses others {out_n[a]}× (z={z:.1f}; mean {mu:.0f})",
                    [e for _, e, _ in ev_by[a][:3]])
    # 2. unusually low / high answered rates (with enough volume)
    rates = {a: ans[a] / out_n[a] for a in out_n if out_n[a] >= 30 and a in agents}
    if len(rates) > 3:
        med = statistics.median(rates.values())
        for a, r in rates.items():
            if abs(r - med) >= 0.2:
                add("answered_rate_outlier", [a], round(r, 3),
                    f"{r:.0%} of its addresses get an explicit reply vs median {med:.0%}",
                    [e for _, e, x in ev_by[a] if not x][:3])
    # 3. one-sided relationships
    for (s, d), n in sorted(pairs.items(), key=lambda kv: -kv[1]):
        back = pairs.get((d, s), 0)
        if n >= 20 and back <= 0.2 * n:
            add("one_sided", [s, d], n, f"{names.get(s, s)} → {names.get(d, d)} {n}× but only {back}× back",
                [r[2] for r in rows if r[0] == s and r[1] == d][:3])
    # 4. bursts: ≥8 addresses within 10 minutes
    for a, lst in ev_by.items():
        ts = [epoch(t) for t, _, _ in lst]
        best, bi = 0, 0
        for i, t in enumerate(ts):
            j = bisect.bisect_right(ts, t + 600)
            if j - i > best:
                best, bi = j - i, i
        if best >= 8:
            add("burst", [a], best, f"{best} addresses within 10 minutes starting {lst[bi][0][:16]}",
                [e for _, e, _ in lst[bi:bi + 5]], at=lst[bi][0])
    # 5. invocation fan-out and lineage clustering
    for s, n in con.execute(f"SELECT src, count(DISTINCT dst) FROM relations WHERE type='invoked'{w} GROUP BY src "
                            f"HAVING count(DISTINCT dst) >= 3", v):
        add("invocation_fanout", [s], n, f"invoked {n} different actors",
            [r[0] for r in con.execute("SELECT event_id FROM relations WHERE type='invoked' AND src=? LIMIT 3", (s,))])
    lineage = dict(con.execute("SELECT id, lineage FROM actors WHERE lineage IS NOT NULL"))
    for a, n in out_n.items():
        if a in lineage and n >= 10:
            same = sum(c for (s, d), c in pairs.items() if s == a and lineage.get(d) == lineage[a])
            if same / n >= 0.5:
                add("lineage_cluster", [a], round(same / n, 3), f"{same / n:.0%} of its addresses go to its own lineage "
                    f"'{lineage[a]}'", [r[2] for r in rows if r[0] == a and lineage.get(r[1]) == lineage[a]][:3])
    # 6. actors with self-reports (claims to check against behaviour)
    for a, n in con.execute(f"SELECT actor, count(*) FROM events WHERE kind='self_report'{w} GROUP BY actor "
                            f"ORDER BY count(*) DESC LIMIT 10", v):
        last = con.execute("SELECT id FROM events WHERE actor=? AND kind='self_report' ORDER BY ts DESC LIMIT 1", (a,)).fetchone()
        add("self_reports", [a], n, "has self-reports: compare its claims with observed behaviour", [last[0]])
    behaviour_signals(con, add, w, v)
    order = ["tag_outlier", "reaction", "tag_trend", "free_label", "burst", "one_sided", "answered_rate_outlier",
             "volume_outlier", "invocation_fanout", "lineage_cluster", "self_reports"]
    present = [k for k in order if any(x["type"] == k for x in out)]
    per_type = max(3, limit // max(1, len(present)))  # keep every kind of signal visible
    picked = []
    for kind in present:
        picked += sorted([x for x in out if x["type"] == kind], key=lambda x: -(x["value"] or 0))[:per_type]
    return picked[:limit]


REACTION_TRIGGERS = ("moderate", "remove_others", "instruct", "coordinate", "ask_help")
REACTION_RESPONSES = ("restore", "workaround", "retry_variant", "follow", "coordinate", "report_result")


def behaviour_signals(con, add, w, v):
    """Signals over LLM behaviour tags (only when a `tag` run exists)."""
    from .metrics import reaction
    from .tags import available
    if not available(con):
        return
    ew = w.replace("ts", "e.ts")
    rows = con.execute(f"SELECT e.actor, t.tag, count(*) FROM w.event_tags t JOIN events e ON e.id=t.event_id WHERE 1{ew} "
                       f"GROUP BY e.actor, t.tag", v).fetchall()
    n_by = dict(con.execute(f"SELECT e.actor, count(*) FROM w.tags t JOIN events e ON e.id=t.event_id WHERE 1{ew} "
                            f"GROUP BY e.actor", v).fetchall())
    total = sum(n_by.values())
    tag_n = {}
    for a, t, n in rows:
        tag_n[t] = tag_n.get(t, 0) + n
    # 1. actors whose behaviour mix differs strongly from everyone else's
    for a, t, n in rows:
        base = tag_n[t] / total
        share = n / n_by[a]
        if n_by[a] >= 15 and n >= 8 and share >= max(2 * base, base + 0.25):
            ev = [r[0] for r in con.execute("SELECT e.id FROM w.event_tags t JOIN events e ON e.id=t.event_id "
                                            "WHERE e.actor=? AND t.tag=? ORDER BY e.ts LIMIT 3", (a, t))]
            add("tag_outlier", [a], round(share, 3), f"'{t}' in {share:.0%} of its {n_by[a]} events vs {base:.0%} overall",
                ev, tag=t)
    # 2. reactions: what targets do right after something is done to them
    for trig in REACTION_TRIGGERS:
        if tag_n.get(trig, 0) < 10:
            continue
        for resp in REACTION_RESPONSES:
            if tag_n.get(resp, 0) < 10 or resp == trig:
                continue
            r = reaction(con, resp, trigger_tag=trig)
            d = r["details"]
            if d["triggers"] >= 10 and d["responded"] >= 5 and (d["lift"] or 0) >= 1.5:
                add("reaction", [], r["value"], f"after '{trig}' aimed at them, targets did '{resp}' within "
                    f"{d['window_min']} min in {r['value']:.0%} of {d['triggers']} cases vs {d['before_rate']:.0%} "
                    f"in the hour before", r["evidence"][:4], trigger_tag=trig, response_tag=resp)
    # 3. behaviours concentrated in a time segment
    seg_rows = con.execute(f"SELECT e.segment_id, t.tag, count(*) FROM w.event_tags t JOIN events e ON e.id=t.event_id "
                           f"WHERE 1{ew} GROUP BY e.segment_id, t.tag", v).fetchall()
    seg_n = dict(con.execute(f"SELECT e.segment_id, count(*) FROM w.tags t JOIN events e ON e.id=t.event_id WHERE 1{ew} "
                             f"GROUP BY e.segment_id", v).fetchall())
    seg_label = dict(con.execute("SELECT id, label FROM segments").fetchall())
    for sg, t, n in seg_rows:
        base, share = tag_n[t] / total, n / seg_n[sg]
        if seg_n[sg] >= 50 and n >= 15 and share >= max(2 * base, base + 0.2):
            ev = [r[0] for r in con.execute("SELECT e.id FROM w.event_tags t JOIN events e ON e.id=t.event_id "
                                            "WHERE e.segment_id=? AND t.tag=? ORDER BY e.ts LIMIT 3", (sg, t))]
            add("tag_trend", [], round(share, 3), f"'{t}' is {share:.0%} of events in segment {sg} "
                f"({seg_label.get(sg)}) vs {base:.0%} overall", ev, tag=t, segment=sg)
    # 4. behaviours the vocabulary missed, shared by several actors
    for label, n, actors, eid in con.execute(
            f"SELECT lower(t.other), count(*), count(DISTINCT e.actor), min(e.id) FROM w.tags t JOIN events e "
            f"ON e.id=t.event_id WHERE t.other<>''{ew} GROUP BY lower(t.other) HAVING count(DISTINCT e.actor) >= 3 "
            f"ORDER BY count(*) DESC LIMIT 10", v):
        add("free_label", [], n, f"free label '{label}' on {n} events by {actors} actors", [eid], label=label)
