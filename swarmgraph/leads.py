"""Honest leads over event labels (themes): which "after A, the same actor does B" regularities are worth testing?

What the first version of the board got wrong, each point found by measurement (see eval/RESULTS.md section 8):
  - cases were not independent: one follow-up event could be counted as the outcome of 2-16 A-events, so a lead with
    742 "cases" had 25 distinct follow-ups from 7 actors on 4 days; ranking by gap picked exactly those;
  - A and B were linked by time alone: a broken document and the check of a different web page were one "sequence";
  - session-start records (intentions) were counted as behaviour;
  - nothing said how big a gap chance alone produces among ~1000 pairs, nor whether the lead held on data not used to
    find it.
Here: a burst of A by one actor is ONE episode (counted by its first event) and every case is treated alike; messages
only; a lead needs >= 4 actors, >= 3 days, an actor-cluster bootstrap bound above zero and the same sign in both halves
of the data it was found in; leads are ranked by that lower bound, not by the gap; the same table is computed with
labels shuffled inside each actor so the reader sees what chance produces; and a second list links A and B by a shared
artifact (issue/PR number, host and path, file name), which made leads replicate far better across months.

Code only; the labels come from the theme layer (LLM-assigned from note summaries).
"""
import bisect
import collections
import random
import re

from .build import epoch

ARTIFACT = re.compile(r"[#!]\d{1,6}\b|https?://[\w.\-]+(?:/[\w\-.%~]+)?|\b[\w\-]+\.(?:md|py|yml|yaml|json|html|js|ts|css|txt|csv|sh|pdf)\b", re.I)


def artifacts(text):
    """generic 'what is this about' tokens: issue/PR/MR numbers, hosts with their first path part, file names"""
    return frozenset(m.group(0).lower().rstrip(".") for m in ARTIFACT.finditer(text or ""))


def pair_table(ev, themes, lo, hi, window_s=90 * 60, min_cases=10, tok=None, episodes=True):
    """(A, B) -> statistics for A-episodes with lo <= epoch < hi.

    ev      [(id, actor, epoch, ts)] in time order     themes  id -> set of labels
    A-episode: the first A event of an actor after at least one window without A (when `episodes`).
    Window after A must have activity by the actor; off-windows (2-3 widths before/after) count only if the actor was
    active in them; cases with no off-window have no baseline and are left out. B is looked for among the same actor's
    events; with `tok` (id -> artifact tokens) it must share one with the A event and A events with no token are left
    out. Returns also per-actor aggregates (for the bootstrap), halves and a few (A, B) event pairs to read."""
    present = collections.defaultdict(list)
    at = collections.defaultdict(list)
    for i, a, ep, ts in ev:
        present[a].append(ep)
        for t in themes.get(i, ()):
            at[(a, t)].append((ep, i))
    active = lambda a, x, y: bisect.bisect_right(present[a], y) > bisect.bisect_right(present[a], x)

    def hit(a, th, x, y, need=None):
        l = at.get((a, th))
        if not l:
            return None
        k = bisect.bisect_right(l, (x, chr(0x10ffff)))
        while k < len(l) and l[k][0] <= y:
            if need is None or (tok[l[k][1]] & need):
                return l[k][1]
            k += 1
        return None

    by_a = collections.defaultdict(list)
    last = {}
    for i, a, ep, ts in ev:
        if not (lo <= ep < hi):
            continue
        for t in themes.get(i, ()):
            prev = last.get((a, t))
            last[(a, t)] = ep
            if episodes and prev is not None and ep - prev < window_s:
                continue
            if tok is not None and not tok.get(i):
                continue
            if not active(a, ep, ep + window_s):
                continue
            offs = [k for k in (-3, -2, 2, 3) if active(a, ep + k * window_s, ep + (k + 1) * window_s)]
            if offs:
                by_a[t].append((ep, i, a, offs, ts[:10]))
    labels = {t for (a, t) in at}
    out = {}
    for A, cases in by_a.items():
        if len(cases) < min_cases:
            continue
        mid = sorted(c[0] for c in cases)[len(cases) // 2]
        for B in labels:
            if B == A:
                continue
            n = f = bh = bn = 0
            per_actor = collections.defaultdict(lambda: [0, 0, 0, 0])
            half = [[0, 0, 0, 0], [0, 0, 0, 0]]
            actors, days, see = set(), set(), []
            for ep, i, a, offs, day in cases:
                need = tok[i] if tok is not None else None
                h = hit(a, B, ep, ep + window_s, need)
                if h == i:
                    h = None
                n += 1
                g, hv = per_actor[a], half[0 if ep < mid else 1]
                g[0] += 1
                hv[0] += 1
                if h is not None:
                    f += 1
                    g[1] += 1
                    hv[1] += 1
                    if len(see) < 3:
                        see.append((i, h))
                for k in offs:
                    x = hit(a, B, ep + k * window_s, ep + (k + 1) * window_s, need) is not None
                    bn += 1
                    bh += x
                    g[3] += 1
                    g[2] += x
                    hv[3] += 1
                    hv[2] += x
                actors.add(a)
                days.add(day)
            if n >= min_cases and bn and f >= 4:
                out[(A, B)] = {"cases": n, "followed": f, "rate": f / n, "base": bh / bn, "gap": f / n - bh / bn,
                               "actors": len(actors), "days": len(days), "per_actor": dict(per_actor),
                               "halves": [(h[1] / h[0] - h[2] / h[3]) if h[0] and h[3] else None for h in half],
                               "see": see}
    return out


def cluster_ci_low(st, reps=300, seed=1):
    """one-sided 95% lower bound of the gap, resampling actors (so one prolific actor cannot carry a lead)"""
    rnd = random.Random(seed)
    gl = list(st["per_actor"].values())
    k = len(gl)
    if k < 3:
        return None
    d = []
    for _ in range(reps):
        pick = [gl[rnd.randrange(k)] for _ in range(k)]
        c, f, bh, bn = (sum(x[i] for x in pick) for i in range(4))
        if c and bn:
            d.append(f / c - bh / bn)
    d.sort()
    return d[int(0.05 * len(d))] if d else None


def load_events(con, kinds=("message",)):
    rows = con.execute("SELECT id, actor, ts, kind FROM events ORDER BY ts").fetchall()
    return [(i, a, epoch(ts), ts) for i, a, ts, k in rows if kinds is None or k in kinds]


def honest_after_then(ev, themes, tok=None, window_s=90 * 60, top=8, min_cases=10, min_actors=4, min_days=3, min_gap=0.05):
    """ranked leads that survive the filters; also returns the number of pairs examined"""
    lo, hi = ev[0][2], ev[-1][2] + 1
    t = pair_table(ev, themes, lo, hi, window_s, min_cases, tok)
    leads = []
    for (A, B), s in t.items():
        if s["actors"] < min_actors or s["days"] < min_days or s["gap"] < min_gap:
            continue
        if any(h is None or h <= 0 for h in s["halves"]):
            continue
        ci = cluster_ci_low(s)
        if ci is None or ci <= 0:
            continue
        leads.append({"after": A, "then": B, "episodes": s["cases"], "followed": s["followed"], "rate": round(s["rate"], 3),
                      "off_window_rate": round(s["base"], 3), "gap": round(s["gap"], 3), "gap_lower_bound": round(ci, 3),
                      "actors": s["actors"], "days": s["days"], "halves": [round(h, 3) for h in s["halves"]],
                      "see": [x for p in s["see"] for x in p]})
    leads.sort(key=lambda x: -x["gap_lower_bound"])
    return leads[:top], len(t)


def shuffled_best(ev, themes, tok=None, window_s=90 * 60, min_cases=10, shuffles=2, seed=3):
    """what chance produces: labels shuffled among each actor's events, same table, best and top-8 mean gap"""
    rnd = random.Random(seed)
    by_actor = collections.defaultdict(list)
    for i, a, ep, ts in ev:
        by_actor[a].append(i)
    lo, hi = ev[0][2], ev[-1][2] + 1
    res = []
    for _ in range(shuffles):
        sh = dict(themes)
        for a, ids in by_actor.items():
            lab = [themes.get(i, set()) for i in ids]
            rnd.shuffle(lab)
            for i, l in zip(ids, lab):
                if l:
                    sh[i] = l
                else:
                    sh.pop(i, None)
        g = sorted((s["gap"] for s in pair_table(ev, sh, lo, hi, window_s, min_cases, tok).values()), reverse=True)
        res.append((g[0] if g else 0.0, sum(g[:8]) / max(1, len(g[:8]))))
    return {"best_gap": [round(x[0], 3) for x in res], "top8_mean_gap": [round(x[1], 3) for x in res]}
