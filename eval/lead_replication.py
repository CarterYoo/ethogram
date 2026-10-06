"""Is the board's own lead generator sound? Measured without any analyst.

Takes the after_then leads the theme board would show for the training month, and asks (a) how independent the cases
behind them are, (b) whether they still hold in the held-out month, (c) how that compares with leads chosen by
chance (labels shuffled inside each actor).

  python3 eval/lead_replication.py INDEX CUT [--window-min 90] [--min-cases 20] [--variant all|messages|independent]
                                   [--labels themes|tags|both]   (tags = the 17 generic behaviour tags: moves, not topics)

  INDEX  full index with event_themes in its work store; CUT  first held-out timestamp (ISO, e.g. 2026-08-01T00:00:00)
  variants (what counts as an A event / a B event / a case):
    all          every event (what theme_map does)
    messages     only chat messages (session-start 'action' records are intentions, not behaviour)
    independent  as `all`, but a follow-up event is used by at most one A case (no reuse of the same B)
    messages_independent   both
    episodes     bursts of A (same actor, gaps < window) count once, by their first event; every case treated alike
    messages_episodes      both
    shared       a B only counts if it mentions the same artifact (issue/PR number, host+path, file name) as the A event;
                 A events that mention none cannot be linked and are left out (episodes_shared, messages_episodes_shared)
"""
import bisect
import collections
import math
import random
import re
import sqlite3
import sys

sys.path.insert(0, __file__.rsplit("/eval/", 1)[0])
from swarmgraph.build import epoch  # noqa: E402


ART = re.compile(r"[#!]\d{1,6}\b|https?://[\w.\-]+(?:/[\w\-.%~]+)?|\b[\w\-]+\.(?:md|py|yml|yaml|json|html|js|ts|css|txt|csv|sh|pdf)\b", re.I)


def artifacts(text):
    """generic 'what is this about' tokens: issue/PR/MR numbers, hosts with their first path part, file names"""
    return frozenset(m.group(0).lower().rstrip(".") for m in ART.finditer(text or ""))


def load(index, cut, variant="all", labels="themes"):
    con = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    w = sqlite3.connect(f"file:{index}.work?mode=ro", uri=True)
    kind = dict(con.execute("SELECT id, kind FROM events"))
    tok = {i: artifacts(t) for i, t in con.execute("SELECT id, text FROM events")} if "shared" in variant else None
    ev = [(i, a, epoch(ts), ts) for i, a, ts in con.execute("SELECT id, actor, ts FROM events ORDER BY ts")]
    themes = collections.defaultdict(set)
    query = {"themes": "SELECT event_id, theme FROM event_themes", "tags": "SELECT event_id, tag FROM event_tags",
             "both": "SELECT event_id, theme FROM event_themes UNION ALL SELECT event_id, 'tag:' || tag FROM event_tags"}[labels]
    for e, t in w.execute(query):
        themes[e].add(t)
    if "messages" in variant:
        themes = {e: t for e, t in themes.items() if kind.get(e) == "message"}
        ev = [x for x in ev if kind.get(x[0]) == "message"]
    cutep = epoch(cut.replace("T", " ").rstrip("Z"))
    return ev, themes, cutep, kind, tok


def pair_table(ev, themes, lo, hi, window_s=90 * 60, min_cases=20, independent=False, episodes=False, tok=None):
    """pair (A, B) -> stats for A-events with lo <= epoch < hi. Same definition as themes._after_then: the window after
    A needs activity by the actor, every off-window (2-3 widths before/after) needs activity, cases without an
    off-window are dropped, B is looked for among the same actor's events."""
    present = collections.defaultdict(list)
    at = collections.defaultdict(list)
    for i, a, ep, ts in ev:
        present[a].append(ep)
        for t in themes.get(i, ()):
            at[(a, t)].append((ep, i))
    active = lambda a, x, y: bisect.bisect_right(present[a], y) > bisect.bisect_right(present[a], x)

    def hit(a, th, x, y, need=None):
        """a B event by the actor in (x, y]; with `need` (artifact tokens of the A event) it must be about the same thing"""
        l = at.get((a, th))
        if not l:
            return None
        k = bisect.bisect_right(l, (x, chr(0x10ffff)))
        while k < len(l) and l[k][0] <= y:
            if need is None or (tok[l[k][1]] & need):
                return l[k][1]
            k += 1
        return None

    by_a = collections.defaultdict(list)  # theme -> [(ep, id, actor, offs)]
    dropped = [0]
    last = {}  # (actor, theme) -> time of the previous A event: a burst of A events is ONE episode, counted by its start
    for i, a, ep, ts in ev:
        if not (lo <= ep < hi):
            continue
        for t in themes.get(i, ()):
            prev = last.get((a, t))
            last[(a, t)] = ep
            if episodes and prev is not None and ep - prev < window_s:
                continue  # continuation of an episode already counted
            if tok is not None and not tok.get(i):
                dropped[0] += 1  # nothing to say what this event is about: cannot be linked
                continue
            if not active(a, ep, ep + window_s):
                continue
            offs = [k for k in (-3, -2, 2, 3) if active(a, ep + k * window_s, ep + (k + 1) * window_s)]
            if offs:
                by_a[t].append((ep, i, a, offs, ts[:10]))
    all_themes = {t for (a, t) in at}
    out = {}
    for A, cases in by_a.items():
        if len(cases) < min_cases:
            continue
        for B in all_themes:
            if B == A:
                continue
            used = set()
            n = f = bh = bn = 0
            per_actor = collections.defaultdict(lambda: [0, 0, 0, 0])
            bs, actors, days = set(), set(), set()
            for ep, i, a, offs, day in cases:
                need = tok[i] if tok is not None else None
                h = hit(a, B, ep, ep + window_s, need)
                if h == i:
                    h = None
                if independent and h is not None:
                    if h in used:
                        continue  # this follow-up already explains an earlier case: not a new episode
                    used.add(h)
                n += 1
                g = per_actor[a]
                g[0] += 1
                if h is not None:
                    f += 1
                    g[1] += 1
                    bs.add(h)
                for k in offs:
                    bn += 1
                    g[3] += 1
                    x = hit(a, B, ep + k * window_s, ep + (k + 1) * window_s, need) is not None
                    bh += x
                    g[2] += x
                actors.add(a)
                days.add(day)
            if n >= min_cases and bn and f >= 4:
                out[(A, B)] = {"cases": n, "followed": f, "rate": f / n, "base": bh / bn, "gap": f / n - bh / bn,
                               "distinct_b": len(bs), "actors": len(actors), "days": len(days),
                               "per_actor": dict(per_actor)}
    return out


def cluster_ci_low(st, reps=300, seed=1):
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


def passes(st, strict=True):
    """the hold-test bar (gap >= 0.10, rate >= 1.5x baseline or >= 0.10 if baseline ~ 0, actor-cluster bootstrap > 0)"""
    if st is None:
        return False
    lift_ok = st["rate"] >= 0.10 if st["base"] < 0.01 else st["rate"] / st["base"] >= 1.5
    if not (st["gap"] >= 0.10 and lift_ok):
        return False
    return (cluster_ci_low(st) or 0) > 0 if strict else True


def spearman(x, y):
    def rk(v):
        o = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for p, i in enumerate(o):
            r[i] = p
        return r
    rx, ry = rk(x), rk(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def main(index, cut, window_min=90, min_cases=20, variant="all", reverse=False, eval_variant=None, labels="themes"):
    ev, themes, cutep, kind, tok = load(index, cut, variant, labels)
    first = ev[0][2]
    W = window_min * 60
    if eval_variant:  # leads are chosen by `variant` on the training part but graded by a common, stricter measure
        ev_e, th_e, _, _, tok_e = load(index, cut, eval_variant, labels)
    else:
        ev_e, th_e, tok_e, eval_variant = ev, themes, tok, variant
    indep = "independent" in variant
    epi = "episodes" in variant
    if reverse:  # train on the later part, hold out the earlier part (an independent second check of a variant choice)
        jul = pair_table(ev, themes, cutep, 1e12, W, min_cases, indep, epi, tok)
        aug = pair_table(ev_e, th_e, first, cutep, W, min_cases // 2, "independent" in eval_variant, "episodes" in eval_variant, tok_e)
    else:
        jul = pair_table(ev, themes, first, cutep, W, min_cases, indep, epi, tok)
        aug = pair_table(ev_e, th_e, cutep, 1e12, W, min_cases // 2, "independent" in eval_variant, "episodes" in eval_variant, tok_e)
    print(f"leads chosen with variant {variant}, graded with {eval_variant}: pairs with enough cases in the training month {len(jul)}, in the held-out month {len(aug)}")
    ranked = sorted(jul, key=lambda p: -jul[p]["gap"])
    print("\nTOP LEADS in the training month (what the board shows): gap = rate after A - the actors' own off-window rate")
    print(f"{'#':>2s} {'gap':>5s} {'cases':>6s} {'followed':>8s} {'distinct B':>10s} {'B reuse':>7s} {'actors':>6s} {'days':>5s} {'cluster CI>0':>12s} | held-out gap  cases  passes")
    for k, p in enumerate(ranked[:12], 1):
        s = jul[p]
        a = aug.get(p)
        print(f"{k:2d} {s['gap']:5.2f} {s['cases']:6d} {s['followed']:8d} {s['distinct_b']:10d} {s['followed'] / max(1, s['distinct_b']):6.1f}x {s['actors']:6d} {s['days']:5d} "
              f"{str((cluster_ci_low(s) or 0) > 0):>12s} | " + (f"{a['gap']:+.2f}     {a['cases']:5d}  {passes(a)}" if a else "no held-out cases"))
    # replication of the top leads vs the base rate over all pairs
    base_pairs = [p for p in jul if p in aug]
    base_rate = sum(passes(aug[p]) for p in base_pairs) / max(1, len(base_pairs))
    print(f"\nreplication: share of pairs that pass the bar in the held-out month: all training pairs {base_rate:.2f} ({len(base_pairs)} pairs with held-out cases)")
    for K in (8, 20, 50):
        top = [p for p in ranked[:K] if p in aug]
        r = sum(passes(aug[p]) for p in top) / max(1, len(top))
        lenient = sum(aug[p]["gap"] >= 0.05 for p in top) / max(1, len(top))
        pos = sum(aug[p]["gap"] > 0 for p in top) / max(1, len(top))
        print(f"  top {K:3d} by training gap: pass {r:.2f}  (gap>=0.05: {lenient:.2f}, gap>0: {pos:.2f}; {len(top)} had held-out cases)")
    xs = [jul[p]["gap"] for p in base_pairs]
    ys = [aug[p]["gap"] for p in base_pairs]
    print(f"  rank correlation, training gap vs held-out gap over all pairs: {spearman(xs, ys):.2f}")
    # chance: shuffle theme labels among the events of each actor in the training month
    rnd = random.Random(3)
    ids_by_actor = collections.defaultdict(list)
    for i, a, ep, ts in ev:
        if (cutep <= ep) if reverse else (first <= ep < cutep):
            ids_by_actor[a].append(i)
    tops = []
    for rep in range(3):
        sh = dict(themes)
        for a, ids in ids_by_actor.items():
            lab = [themes.get(i, set()) for i in ids]
            rnd.shuffle(lab)
            for i, l in zip(ids, lab):
                if l:
                    sh[i] = l
                else:
                    sh.pop(i, None)
        t = pair_table(ev, sh, cutep if reverse else first, 1e12 if reverse else cutep, W, min_cases, indep, epi, tok)
        g = sorted((s["gap"] for s in t.values()), reverse=True)
        tops.append((g[0] if g else 0, sum(g[:8]) / max(1, len(g[:8])), sum(1 for x in g if x >= 0.2), len(g)))
    real = sorted((s["gap"] for s in jul.values()), reverse=True)
    print(f"\nchance (labels shuffled inside each actor, 3 shuffles): best gap {[round(t[0], 2) for t in tops]}, mean of top 8 "
          f"{[round(t[1], 2) for t in tops]}, pairs with gap>=0.2: {[t[2] for t in tops]} of {[t[3] for t in tops]}")
    print(f"real:                                                    best gap {real[0]:.2f}, mean of top 8 {sum(real[:8]) / 8:.2f}, "
          f"pairs with gap>=0.2: {sum(1 for x in real if x >= 0.2)} of {len(real)}")


if __name__ == "__main__":
    a = sys.argv[1:]
    opt = lambda k, d: type(d)(a[a.index(k) + 1]) if k in a else d
    main(a[0], a[1], opt("--window-min", 90), opt("--min-cases", 20), opt("--variant", "all"), "--reverse" in a,
         opt("--eval-variant", "") or None, opt("--labels", "themes"))

