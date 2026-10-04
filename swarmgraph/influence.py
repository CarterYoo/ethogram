"""Influence: who changes whom, by how much, and whether it comes back (docs/FLOW.md section 8).

The spread measures of flow.py compare connected stretches with connected stretches; reversed in time they give
nearly the same ratios, so they mostly show that stretches in contact are alike. This model asks the question the
other way round: does B do f more than B usually does, after B could see A do f?

For each behaviour f, over the target stretches u of the agents (actors with enough judged stretches):

  logit P(f in u) = beta_B            B's own level (B's usual behaviour, whoever it meets)
                  + pi_place          the page's or room's own level
                  + gamma_day         what changed for everyone that day
                  + rho * prev        B showed f in its previous stretch that day (persistence)
                  + kappa * nearby    how many stretches by others showed f in the two hours before u WITHOUT a
                                      contact to u (a common cause at a finer grain than the week)
                  + sum over A of  e_A(u) * alpha_AB,   alpha_AB = a0 + d_AB
                                      e_A(u) = 1 when a stretch by A that u could see (reuse, reply, mention, the
                                      same place in the hour before) showed f; d_AB = the pair's own part, shrunk to
                                      0 by an L2 penalty, so a pair with little contact stays near 0 (shrinking to a
                                      shared mean, or fitting how much A moves others and B is moved as terms of their
                                      own, let one pair's effect spill onto pairs without influence in synthetic
                                      tests; a0 is therefore held at 0). Each alpha has a standard error from its
                                      information (1 / sqrt(sum of p(1 - p) over its exposures + penalty)); a pair
                                      is clear influence when alpha / se >= 3, which keeps chance pairs out when about
                                      forty pairs are tested. Only clear pairs enter G

  from alpha to the system:
    dp_AB   = mean change in B's probability of f when exposed to A (marginal effect of alpha_AB)
    c_AB    = how many of B's stretches (all, judged or not) see one f-stretch of A, on average
    G_AB    = dp_AB * c_AB: further f-stretches in B per f-stretch of A (the branching matrix of a Hawkes process)
    R       = spectral radius of G: further f-stretches per f-stretch through everyone, in one generation (R >= 1:
              it feeds itself; R near 0: it dies out)
    T       = (I - G)^-1 - I: through every path, A -> B -> C -> ... ; T_AA = what returns to A through others

  checks:
    held out  fitted on the stretches before a date, both with and without the alpha terms, scored on the
              stretches after it (log loss; days unseen there, so both models drop the day terms): do the alphas
              predict what follows?
    contact   alpha is net of `nearby`: activity at the same time without a contact does not count as influence

Associations net of each agent's own level, the week and same-time activity; not proof of influence (unseen causes
that reach A and B through the same contact remain). Needs numpy and scipy (the maps environment); the result is
stored in the work store and read by the flow_influence tool without them.
"""
import collections
import json
import math
import time

WINDOW_S, NEARBY_S, MIN_UNITS = 86400, 7200, 30
LAM = {"fe": 0.05, "a0": 1e6, "d": 2.0, "x": 0.05}  # L2 strength by kind of parameter; a0 held at 0 (see above)
EXPO_LAM = 1.0  # L2 strength of the behaviour-to-behaviour terms a(g -> f)
CLEAR_Z = 3.0  # a pair counts as influence when alpha / its standard error reaches this (about 40 pairs tested)
MIN_POS, MIN_EXPOSED = 40, 25


def _fit(rows, cols, n, y, lam, theta0=None):
    """L2-penalised logistic regression on a sparse design given as (row, col) pairs with value 1 or a float"""
    import numpy as np
    from scipy import sparse
    from scipy.optimize import minimize
    r, c, v = zip(*rows)
    X = sparse.csr_matrix((v, (r, c)), shape=(len(y), n))
    y = np.asarray(y, float)
    lam = np.asarray(lam, float)

    def f(th):
        eta = X @ th
        loss = np.logaddexp(0, eta).sum() - y @ eta + 0.5 * (lam * th * th).sum()
        p = 1 / (1 + np.exp(-eta))
        return loss, X.T @ (p - y) + lam * th
    res = minimize(f, np.zeros(n) if theta0 is None else theta0, jac=True, method="L-BFGS-B",
                   options={"maxiter": 3000})
    return res.x, X


def design(m, f, agents, targets, enc=None, with_alpha=True, with_day=True, placebo=False, place=True):
    """design rows for behaviour f: (rows [(i, col, val)], columns {name: index}, y, meta per row). placebo: what B
    sees only AFTER u (the sources of B's next stretch that came after u and were not answers to B): influence cannot
    act before it is seen, so an alpha here measures a shared topic or conversation, not influence. (Reversing the
    edges is no placebo: under real influence A -> B, B's later f predicts A's earlier f.)"""
    cols, rows, y, meta = {}, [], [], []

    def col(name):
        if name not in cols:
            cols[name] = len(cols)
        return cols[name]
    shows = lambda v: f in m.has[v] if v in m.has else (enc is not None and f in enc.get(v, ()))  # noqa: E731
    seen = lambda v: v in m.has or (enc is not None and v in enc)  # noqa: E731
    by_actor = collections.defaultdict(list)
    for i in m.has:
        by_actor[m.u[i]["actor"]].append(i)
    for lst in by_actor.values():
        lst.sort(key=lambda i: m.u[i]["t"])
    order = {i: k for lst in by_actor.values() for k, i in enumerate(lst)}
    f_times = sorted((m.u[v]["t"], m.u[v]["actor"], v) for v in m.u if seen(v) and shows(v))
    import bisect
    keys = [x[0] for x in f_times]
    col("intercept")
    for u in targets:
        x = m.u[u]
        B = x["actor"]
        if B not in agents:
            continue
        k = len(y)
        rows.append((k, cols["intercept"], 1.0))
        rows.append((k, col(f"beta:{B}"), 1.0))
        if place and x.get("channel"):
            rows.append((k, col(f"place:{x['channel']}"), 1.0))
        if with_day:
            rows.append((k, col(f"day:{x['day']}"), 1.0))
        prev = by_actor[B][order[u] - 1] if order[u] > 0 else None
        if prev is not None and x["t"] - m.u[prev]["t"] <= WINDOW_S:
            rows.append((k, col("prev"), 1.0 if f in m.has[prev] else 0.0))
        else:
            rows.append((k, col("prev_missing"), 1.0))
        srcs = set()
        if placebo:
            nxt = m.next_any(u)
            if nxt is not None:
                for t_ in ("reuse", "reply", "address", "channel"):
                    srcs |= {w for w in m.src[t_].get(nxt, ()) if x["t"] < m.u[w]["t"] <= m.u[nxt]["t"] and seen(w)
                             and not any(m.u[v]["actor"] == B for k_ in ("reuse", "reply", "address", "channel")
                                         for v in m.src[k_].get(w, ()))}
        else:
            for t_ in ("reuse", "reply", "address", "channel"):
                srcs |= {v for v in m.src[t_].get(u, ()) if m.u[v]["t"] <= x["t"] and seen(v)}
        lo = bisect.bisect_left(keys, x["t"] - NEARBY_S)
        hi = bisect.bisect_left(keys, x["t"])
        near = sum(1 for _, a, v in f_times[lo:hi] if a != B and v not in srcs)
        rows.append((k, col("nearby"), math.log1p(near)))
        exp_from = sorted({m.u[v]["actor"] for v in srcs if shows(v) and m.u[v]["actor"] in agents and
                           m.u[v]["actor"] != B})
        if with_alpha:
            for A in exp_from:
                rows.append((k, col("a0"), 1.0))
                rows.append((k, col(f"d:{A}>{B}"), 1.0))
        y.append(1 if f in m.has[u] else 0)
        meta.append({"u": u, "B": B, "from": exp_from})
    return rows, cols, y, meta


def _lam(cols, d=None):
    out = [0.0] * len(cols)
    for name, j in cols.items():
        kind = name.split(":", 1)[0]
        out[j] = {"intercept": 0.0, "beta": LAM["fe"], "place": LAM["fe"], "day": LAM["fe"], "prev": LAM["x"],
                  "prev_missing": LAM["x"], "nearby": LAM["x"], "a0": LAM["a0"],
                  "d": LAM["d"] if d is None else d}[kind]
    return out


def _side(alpha, support, k, lab, n=6):
    """per actor, the contact-weighted mean alpha of the pairs where it is the source (k=0) or the target (k=1)"""
    tot, w = collections.Counter(), collections.Counter()
    for pair, a in alpha.items():
        tot[pair[k]] += a * support[pair]
        w[pair[k]] += support[pair]  # all pairs, so an actor with many contacts and no influence scores near 0
    rows = [{"actor": lab(x), "alpha": round(tot[x] / w[x], 3), "exposed": w[x]} for x in w if w[x] >= 10]
    return sorted(rows, key=lambda r: -r["alpha"])[:n]


def _logloss(theta, X, y):
    import numpy as np
    eta = X @ theta
    y = np.asarray(y, float)
    return float((np.logaddexp(0, eta) - y * eta).mean())


def one(m, f, agents, enc=None, cut=None, placebo=False, until=None, d_lam=None):
    """the influence model for one behaviour: alphas, the branching matrix and its consequences, the held-out check"""
    import numpy as np
    targets = [u for u in m.tgt if m.u[u]["actor"] in agents and (not until or m.u[u]["start"] < until)]
    if placebo:
        _next_any(m)
    rows, cols, y, meta = design(m, f, agents, targets, enc, placebo=placebo)
    if sum(y) < MIN_POS or sum(1 for x in meta if x["from"]) < MIN_EXPOSED:
        return None
    theta, X = _fit(rows, cols, len(cols), y, _lam(cols, d_lam))
    eta = X @ theta
    pr = 1 / (1 + np.exp(-eta))
    info = X.T.multiply(1).power(2) @ (pr * (1 - pr)) + np.asarray(_lam(cols, d_lam))  # diagonal of the penalised Hessian
    se = {name: float(1 / math.sqrt(info[j])) for name, j in cols.items()}
    get = lambda name: theta[cols[name]] if name in cols else 0.0  # noqa: E731
    alpha, support = {}, collections.Counter()
    for x in meta:
        for A in x["from"]:
            support[(A, x["B"])] += 1
    dp = collections.defaultdict(list)
    for x, e in zip(meta, eta):
        for A in x["from"]:
            a = get("a0") + get(f"d:{A}>{x['B']}")
            alpha[(A, x["B"])] = a
            dp[(A, x["B"])].append(1 / (1 + math.exp(-e)) - 1 / (1 + math.exp(-(e - a))))
    # contacts: how many of B's stretches (all of them) see one f-stretch of A
    out_edges = collections.defaultdict(set)
    for t_ in ("reuse", "reply", "address", "channel"):
        for w, vs in m.src[t_].items():
            for v in vs:
                out_edges[v].add(w)
    ag = sorted(agents)
    ix = {a: k for k, a in enumerate(ag)}
    fst = collections.Counter(m.u[v]["actor"] for v in m.has if f in m.has[v])
    C = np.zeros((len(ag), len(ag)))
    for v in m.has:
        if f in m.has[v] and m.u[v]["actor"] in ix:
            for w in out_edges.get(v, ()):
                b = m.u[w]["actor"]
                if b in ix and b != m.u[v]["actor"]:
                    C[ix[m.u[v]["actor"]], ix[b]] += 1
    for a in ag:
        if fst[a]:
            C[ix[a]] /= fst[a]
    z = {k: alpha[k] / se.get(f"d:{k[0]}>{k[1]}", 1e9) for k in alpha}
    D = np.zeros_like(C)
    for (A, B), v in dp.items():
        if z[(A, B)] >= CLEAR_Z:
            D[ix[A], ix[B]] = sum(v) / len(v)
    G = D * C
    R = float(max(abs(np.linalg.eigvals(G)))) if len(ag) else 0.0
    T = np.linalg.inv(np.eye(len(ag)) - G) - np.eye(len(ag)) if R < 0.99 else None
    held = None
    if cut:  # the alphas fitted before the cut, scored after it, against the same model without them
        tr = [u for u in targets if m.u[u]["start"] < cut]
        te = [u for u in targets if m.u[u]["start"] >= cut]
        if len(tr) > 200 and len(te) > 200:
            res = {}
            for name, wa in (("with", True), ("without", False)):
                r1, c1, y1, _ = design(m, f, agents, tr, enc, wa, with_day=False, placebo=placebo)
                th, _ = _fit(r1, c1, len(c1), y1, _lam(c1, d_lam))
                r2, c2, y2, _ = design(m, f, agents, te, enc, wa, with_day=False, placebo=placebo)
                # columns met only after the cut (an alpha never fitted) count as 0
                name_of = {j: k for k, j in c2.items()}
                rr = [(i, c1[name_of[j]], v) for (i, j, v) in r2 if name_of[j] in c1]
                from scipy import sparse
                Xt = sparse.csr_matrix(([v for _, _, v in rr], ([i for i, _, _ in rr], [j for _, j, _ in rr])),
                                       shape=(len(y2), len(c1)))
                res[name] = _logloss(th, Xt, y2)
            held = {"cut": cut, "logloss_with": round(res["with"], 5), "logloss_without": round(res["without"], 5),
                    "gain": round(res["without"] - res["with"], 5), "train": len(tr), "test": len(te)}
    pairs = sorted(((z[k], k) for k in alpha if support[k] >= 3), reverse=True)  # strongest evidence first
    clear = [k for k in alpha if z[k] >= CLEAR_Z]
    lab = m.who
    top = lambda M, n=8: sorted(((float(M[i, j]), ag[i], ag[j]) for i in range(len(ag)) for j in range(len(ag))  # noqa
                                 if i != j and M[i, j] > 0), reverse=True)[:n]
    loops = sorted(((float(G[i, j] * G[j, i]), ag[i], ag[j]) for i in range(len(ag)) for j in range(i + 1, len(ag))
                    if G[i, j] > 0 and G[j, i] > 0), reverse=True)[:5]
    return {"behaviour": m.name(f), "targets": len(y), "with_f": int(sum(y)),
            "exposed_targets": sum(1 for x in meta if x["from"]),
            "persistence": round(get("prev"), 3), "same_time_without_contact": round(get("nearby"), 3),
            "mean_alpha": round(float(np.mean([alpha[k] for k in alpha])) if alpha else 0.0, 3),
            "mean_alpha_clear": round(float(np.mean([alpha[k] for k in clear])), 3) if clear else None,
            "pairs_tested": len(alpha), "clear_pairs": len(clear),
            "pairs": [{"from": lab(A), "to": lab(B), "alpha": round(float(alpha[(A, B)]), 3), "z": round(float(zz), 2),
                       "clear": bool(zz >= CLEAR_Z), "dp": round(sum(dp[(A, B)]) / len(dp[(A, B)]), 4),
                       "exposed": support[(A, B)]} for zz, (A, B) in pairs[:max(10, len(clear))]],
            "moves_others": _side(alpha, support, 0, lab), "is_moved": _side(alpha, support, 1, lab),
            "R": round(R, 3), "branching_top": [{"from": lab(a), "to": lab(b), "g": round(g, 4)} for g, a, b in top(G)],
            "total_top": [{"from": lab(a), "to": lab(b), "t": round(t, 4)} for t, a, b in top(T)] if T is not None
            else "R >= 1: no finite total",
            "returns_to_self": sorted(({"actor": lab(a), "t": round(float(T[ix[a], ix[a]]), 4)} for a in ag),
                                      key=lambda x: -x["t"])[:5] if T is not None else None,
            "two_way": [{"a": lab(a), "b": lab(b), "g_ab_x_g_ba": round(g, 5)} for g, a, b in loops],
            "heldout": held}


PENALTIES = (1.0, 5.0, 20.0, 80.0, 320.0)


def choose_penalty(m, agents, enc, cut, fe, log=print):
    """the shrinkage of the a(g -> f) terms, chosen by prediction INSIDE the part before the cut: fitted on its first
    three quarters, scored on its last quarter. The part after the cut is never looked at, so the held-out check that
    follows stays held out. Weak shrinkage overfits: on AI Village the terms fitted with 1 predicted August worse than
    no terms at all, with 20-80 better"""
    from datetime import datetime
    times = sorted(m.u[u]["start"] for u in m.tgt if not cut or m.u[u]["start"] < cut)
    if not cut or len(times) < 400:
        return EXPO_LAM, {}
    inner = times[int(len(times) * 0.75)][:10]
    tried = {}
    for lam in PENALTIES:
        r = behaviours(m, agents, enc, inner, fe=fe, until=cut, lam=lam)
        tried[lam] = r["heldout"]["total_gain"] if r["heldout"] else None
        log(f"influence: penalty {lam}: gain {tried[lam]} on {inner} to {cut}")
    best = max((g, -lam, lam) for lam, g in tried.items() if g is not None)[2]
    return best, {str(k): v for k, v in tried.items()}


def run(db, cut=None, log=print):
    """the influence model for every behaviour with enough cases; stored as feature_atlas['influence']"""
    from . import features as FE, flow as FL, query as Q
    from .store import open_work
    con = Q.connect(db)
    m = FL.load(con)
    if m is None:
        raise RuntimeError("no behaviour atlas yet: run `features` first")
    per = collections.Counter(m.u[i]["actor"] for i in m.has)
    agents = {a for a, n in per.items() if n >= MIN_UNITS}
    enc = (FE.get(con) or {}).get("encoded", {}).get("units")
    enc = {int(k): set(v) for k, v in enc.items()} if enc else None
    out, t0 = {}, time.time()
    # agent to agent needs actors that recur (AI Village: 27 agents); on a wiki, where author labels are mostly used
    # once, only behaviour to behaviour is measured, over every label, with each label's and each page's own level
    # shrunk towards the mean
    level = "agents" if len(agents) >= 5 else "labels"
    d_lam, d_tried = LAM["d"], {}
    if level == "agents" and cut:
        times = sorted(m.u[u]["start"] for u in m.tgt if m.u[u]["start"] < cut)
        inner = times[int(len(times) * 0.75)][:10]
        for lam in (2.0, 8.0, 32.0, 128.0):  # chosen inside the part before the cut, as for the behaviour terms
            gains = [r["heldout"]["gain"] for f in m.fids
                     for r in [one(m, f, agents, enc, inner, until=cut, d_lam=lam)] if r and r["heldout"]]
            d_tried[str(lam)] = round(sum(gains), 4)
            log(f"influence: pair penalty {lam}: gain {d_tried[str(lam)]} on {inner} to {cut}")
        d_lam = float(max((g, -float(k), k) for k, g in d_tried.items())[2])
    if level == "agents":
        for f in m.fids:
            r = one(m, f, agents, enc, cut, d_lam=d_lam)
            if r:
                out[f] = r
                log(f"influence: {r['behaviour'][:40]} R={r['R']} heldout={r['heldout'] and r['heldout']['gain']}")
    who, fe = (agents, LAM["fe"]) if level == "agents" else (None, 1.0)
    lam, tried = choose_penalty(m, who, enc, cut, fe, log)
    between = behaviours(m, who, enc, cut, log=log, fe=fe, lam=lam)
    pl = behaviours(m, who, enc, cut, placebo=True, fe=fe, lam=lam)
    between["level"], between["penalty"], between["penalty_tried"] = level, lam, tried
    between.pop("tested", None)
    pl.pop("tested", None)
    between["placebo"] = {"significant": pl["significant"], "R": pl["R"], "heldout": pl["heldout"]}
    summary = {"level": level, "agents": len(agents), "behaviours": len(out), "cut": cut, "encoded": bool(enc),
               "made": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(time.time() - t0),
               "behaviour_links": between["significant"], "behaviour_links_placebo": pl["significant"],
               "R": between["R"], "R_placebo": pl["R"], "penalty": lam, "pair_penalty": d_lam,
               "pair_penalty_tried": d_tried}
    FE.save(open_work(db), "influence", {"summary": summary, "behaviours": out, "between": between})
    return summary


BETWEEN_HOW = ("Does seeing one behaviour change what others do? For each behaviour f, a(g -> f) = how much more an "
               "agent shows f than it usually does after it could see another agent show g (copied words, a reply, a "
               "mention, the same place in the hour before), net of the day, its previous stretch and same-time f "
               "without contact; odds = exp(a); dp = the change in probability; significant = false discovery rate "
               "5% over every pair tested; branch = further f-stretches in others per g-stretch; R = further "
               "behaviour per behaviour through everyone (near 1: it nearly sustains itself); total = through every "
               "chain. The placebo (what an agent sees only afterwards) is the bar to beat. Associations, not proof; "
               "behaviours found by the readers of whole chunks (R1-R4) can also co-occur because one reader marked "
               "several agents at once.")


def between_view(con, fid=None):
    """behaviour to behaviour: the stored links (no numpy needed)"""
    from . import features as FE, flow as FL
    d = (FE.get(con) or {}).get("influence") or {}
    b = d.get("between")
    if not b:
        return {"error": "no influence model yet: run `influence` (CLI) after the feature atlas"}
    m = FL.load(con)
    nm = (lambda f: m.name(f)) if m else (lambda f: f)
    row = lambda x: {"from": nm(x["g"]), "to": nm(x["f"]), "odds": x["odds"], "dp": x["dp"], "z": x["z"],  # noqa
                     "exposed": x["exposed"], "branch": x.get("branch")}
    if fid:
        return {"how": BETWEEN_HOW, "behaviour": nm(fid),
                "same_behaviour": [row(x) for x in b["links"] if x["g"] == fid and x["f"] == fid],
                "makes_others_do": [row(x) for x in b["links"] if x["g"] == fid and x["f"] != fid][:12],
                "brought_on_by": [row(x) for x in b["links"] if x["f"] == fid and x["g"] != fid][:12]}
    note = ("" if b.get("level", "agents") == "agents" else
            " Here actors are author labels that are mostly used once: an actor's own level can be measured only for "
            "labels that recur, so a page's own level is part of the model too, and the placebo is the bar to beat.")
    return {"how": BETWEEN_HOW + note, **{k: b[k] for k in ("behaviours", "pairs_tested", "significant", "R", "heldout",
                                                            "placebo", "two_way", "total_top") if k in b},
            "same_behaviour": b["same_behaviour"][:10], "other_behaviour": b["other_behaviour"][:15]}


def view(con, fid=None, actor=None, by=None):
    """what the flow_influence tool returns (stored results; no numpy needed): behaviour to behaviour unless by is
    'agents' or an actor is given"""
    from . import features as FE
    if by != "agents" and not actor:
        return between_view(con, fid)
    d = (FE.get(con) or {}).get("influence")
    if not d:
        return {"error": "no influence model yet: run `influence` (CLI) after the feature atlas"}
    bs = d["behaviours"]
    if not bs:
        return {"error": "no agent-to-agent model here: actors are author labels that rarely recur (see the default, "
                         "behaviour to behaviour)"}
    how = ("alpha = how much more B shows a behaviour than B usually does (log-odds), after B could see A show it, net "
           "of the week, B's previous stretch and same-time activity without contact; dp = that change as a "
           "probability; R = further occurrences per occurrence through everyone (>= 1 feeds itself); total = through "
           "every path; heldout gain > 0 = the alphas fitted before the cut predict the stretches after it. "
           "Associations, not proof.")
    if fid:
        if fid not in bs:
            return {"error": f"no influence model for {fid} (too few cases or no such behaviour)"}
        return {"how": how, **bs[fid]}
    if actor:
        moves, moved = [], []
        for f, b in bs.items():
            for p in b["pairs"]:
                if p["from"] == actor:
                    moves.append({"behaviour": b["behaviour"], **p})
                if p["to"] == actor:
                    moved.append({"behaviour": b["behaviour"], **p})
        return {"how": how, "actor": actor, "moves": sorted(moves, key=lambda x: -x["alpha"])[:12],
                "moved_by": sorted(moved, key=lambda x: -x["alpha"])[:12]}
    rows = sorted(({"id": f, "behaviour": b["behaviour"], "clear_pairs": b.get("clear_pairs", 0), "R": b["R"],
                    "heldout_gain": (b["heldout"] or {}).get("gain"),
                    "top_pair": next((p for p in b["pairs"] if p.get("clear")), None)}
                   for f, b in bs.items()), key=lambda x: (-x["clear_pairs"], -(x["heldout_gain"] or 0)))
    movers = collections.Counter()
    for b in bs.values():
        for p in b["pairs"]:
            if p.get("clear"):
                movers[p["from"]] += 1
    return {"how": how, **d["summary"], "predictive": sum(1 for r in rows if (r["heldout_gain"] or 0) > 0),
            "moves_most_often": [{"actor": a, "clear_pairs": n} for a, n in movers.most_common(6)],
            "behaviours": [r for r in rows if r["clear_pairs"]] + [
                {"id": r["id"], "behaviour": r["behaviour"], "clear_pairs": 0, "heldout_gain": r["heldout_gain"]}
                for r in rows if not r["clear_pairs"]]}


# ---------------------------------------------------------------- behaviour to behaviour: does seeing g change f?

def _bh(ps, q=0.05):
    """Benjamini-Hochberg: the set of indices kept at false discovery rate q"""
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    keep, n = set(), len(ps)
    for rank, i in enumerate(order, 1):
        if ps[i] <= q * rank / n:
            keep = set(order[:rank])
    return keep


def _exposures(m, u, B, enc, placebo, fids):
    """the behaviours shown by what u could see (or, for the placebo, by what B sees only after u)"""
    shows = lambda v: m.has[v] if v in m.has else (enc.get(v, set()) if enc else set())  # noqa: E731
    seen = lambda v: v in m.has or (enc is not None and v in enc)  # noqa: E731
    x, out = m.u[u], set()
    if placebo:
        nxt = m.next_any(u)
        if nxt is None:
            return out
        for t_ in ("reuse", "reply", "address", "channel"):
            for w in m.src[t_].get(nxt, ()):
                if x["t"] < m.u[w]["t"] <= m.u[nxt]["t"] and seen(w) and m.u[w]["actor"] != B and not any(
                        m.u[v]["actor"] == B for k_ in ("reuse", "reply", "address", "channel")
                        for v in m.src[k_].get(w, ())):
                    out |= shows(w)
    else:
        for t_ in ("reuse", "reply", "address", "channel"):
            for v in m.src[t_].get(u, ()):
                if m.u[v]["t"] <= x["t"] and seen(v) and m.u[v]["actor"] != B:
                    out |= shows(v)
    return out & set(fids)


def _next_any(m):
    """each stretch's actor's next stretch within a day (any stretch, judged or not), for the seen-later placebo"""
    if not hasattr(m, "next_any"):
        seq = collections.defaultdict(list)
        for i, x in m.u.items():
            seq[x["actor"]].append((x["t"], i))
        nxt = {}
        for lst in seq.values():
            lst.sort()
            for (t0, a), (t1, b) in zip(lst, lst[1:]):
                if t1 - t0 <= WINDOW_S:
                    nxt[a] = b
        m.next_any = nxt.get
    return m.next_any


def behaviours(m, agents, enc=None, cut=None, placebo=False, fids=None, log=lambda s: None, fe=None, place=True,
               until=None, lam=None, since=None):
    """for every behaviour f: logit P(f in u) = B's level + day + B's previous f + same-time f without contact
    + sum over g of a(g -> f) * [what u could see showed g]; a(g -> f) with a z, the false discovery rate over all
    pairs, the change in probability, and the branching matrix over behaviours"""
    import bisect
    import numpy as np
    if placebo:
        _next_any(m)
    fids = fids or [f for f in m.fids if sum(1 for i in m.has if f in m.has[i]) >= MIN_POS]
    if agents is None:  # every actor (author labels that rarely recur: their own level is shrunk towards the mean)
        agents = {x["actor"] for x in m.u.values()}
    fe = LAM["fe"] if fe is None else fe
    lam_e = EXPO_LAM if lam is None else lam
    targets = [u for u in m.tgt if m.u[u]["actor"] in agents and (not until or m.u[u]["start"] < until)
               and (not since or m.u[u]["start"] >= since)]
    expo = {u: _exposures(m, u, m.u[u]["actor"], enc, placebo, fids) for u in targets}
    by_actor = collections.defaultdict(list)
    for i in m.has:
        by_actor[m.u[i]["actor"]].append(i)
    for lst in by_actor.values():
        lst.sort(key=lambda i: m.u[i]["t"])
    prev = {}
    for lst in by_actor.values():
        for a, b in zip(lst, lst[1:]):
            if m.u[b]["t"] - m.u[a]["t"] <= WINDOW_S:
                prev[b] = a
    links, gains, fitted = [], {}, {}
    for f in fids:
        def build(sel, with_g, with_day):
            cols, rows, y = {}, [], []
            col = lambda n: cols.setdefault(n, len(cols))  # noqa: E731
            col("intercept")
            f_t = sorted(m.u[v]["t"] for v in m.has if f in m.has[v])
            for k, u in enumerate(sel):
                x = m.u[u]
                rows += [(k, 0, 1.0), (k, col(f"beta:{x['actor']}"), 1.0)]
                if place and x.get("channel"):
                    rows.append((k, col(f"place:{x['channel']}"), 1.0))
                if with_day:
                    rows.append((k, col(f"day:{x['day']}"), 1.0))
                p = prev.get(u)
                rows.append((k, col("prev"), 1.0 if p is not None and f in m.has[p] else 0.0) if p is not None
                            else (k, col("prev_missing"), 1.0))
                near = bisect.bisect_left(f_t, x["t"]) - bisect.bisect_left(f_t, x["t"] - NEARBY_S)
                rows.append((k, col("nearby"), math.log1p(near)))
                if with_g:
                    for g in expo[u]:
                        rows.append((k, col(f"e:{g}"), 1.0))
                y.append(1 if f in m.has[u] else 0)
            lam = [0.0 if n == "intercept" else fe if n.split(":")[0] in ("beta", "place") else
                   LAM["fe"] if n.startswith("day:") else
                   lam_e if n.startswith("e:") else LAM["x"] for n in sorted(cols, key=cols.get)]
            return rows, cols, y, lam
        rows, cols, y, lam = build(targets, True, True)
        if sum(y) < MIN_POS:
            continue
        th, X = _fit(rows, cols, len(cols), y, lam)
        eta = X @ th
        pr = 1 / (1 + np.exp(-eta))
        info = X.T.power(2) @ (pr * (1 - pr)) + np.asarray(lam)
        for g in fids:
            n = f"e:{g}"
            if n not in cols:
                continue
            j = cols[n]
            ex = np.asarray(X[:, j].todense()).ravel() > 0
            if ex.sum() < MIN_EXPOSED:
                continue
            a, se = float(th[j]), float(1 / math.sqrt(info[j]))
            z = a / se
            dp = float(np.mean(pr[ex] - 1 / (1 + np.exp(-(eta[ex] - a)))))
            links.append({"g": g, "f": f, "a": a, "z": z, "p": math.erfc(abs(z) / math.sqrt(2)), "dp": dp,
                          "exposed": int(ex.sum()), "exposed_with": int(np.asarray(y)[ex].sum())})
        fitted[f] = True
        if cut:  # with and without the exposures, fitted before the cut and scored after it (no day terms)
            tr = [u for u in targets if m.u[u]["start"] < cut]
            te = [u for u in targets if m.u[u]["start"] >= cut]
            res = {}
            for name, wg in (("with", True), ("without", False)):
                r1, c1, y1, l1 = build(tr, wg, False)
                if sum(y1) < 10:
                    break
                t1, _ = _fit(r1, c1, len(c1), y1, l1)
                r2, c2, y2, _ = build(te, wg, False)
                name_of = {j: k for k, j in c2.items()}
                rr = [(i, c1[name_of[j]], v) for (i, j, v) in r2 if name_of[j] in c1]
                from scipy import sparse
                Xt = sparse.csr_matrix(([v for _, _, v in rr], ([i for i, _, _ in rr], [j for _, j, _ in rr])),
                                       shape=(len(y2), len(c1)))
                res[name] = _logloss(t1, Xt, y2)
            if len(res) == 2:
                gains[f] = res["without"] - res["with"]
        log(f"behaviours: {m.name(f)[:40]} done")
    keep = _bh([x["p"] for x in links])
    for k, x in enumerate(links):
        x["significant"] = k in keep and x["a"] > 0
    # the branching matrix over behaviours: further f-stretches in others per g-stretch. A stretch that saw g in k
    # sources is exposed once, not k times: each of those sources is credited with 1/k of it
    gsrc = collections.defaultdict(collections.Counter)  # g -> source -> credited exposures
    for w, x in m.u.items():
        if x["actor"] not in agents:
            continue
        srcs = {v for t_ in ("reuse", "reply", "address", "channel") for v in m.src[t_].get(w, ())
                if v in m.has and m.u[v]["actor"] != x["actor"] and m.u[v]["t"] <= x["t"]}
        for g in fids:
            hit = [v for v in srcs if g in m.has[v]]
            for v in hit:
                gsrc[g][v] += 1 / len(hit)
    c = {}
    for g in fids:
        gs = [v for v in m.has if g in m.has[v]]
        c[g] = sum(gsrc[g].get(v, 0) for v in gs) / len(gs) if gs else 0.0
    ix = {g: k for k, g in enumerate(fids)}
    G = np.zeros((len(fids), len(fids)))
    for x in links:
        if x["significant"]:
            G[ix[x["g"]], ix[x["f"]]] = max(0.0, x["dp"]) * c[x["g"]]
            x["g_branch"] = round(G[ix[x["g"]], ix[x["f"]]], 4)
    R = float(max(abs(np.linalg.eigvals(G)))) if len(fids) else 0.0
    T = np.linalg.inv(np.eye(len(fids)) - G) - np.eye(len(fids)) if R < 0.99 else None
    sig = [x for x in links if x["significant"]]
    nm = m.name
    fmt = lambda x: {"from": nm(x["g"]), "to": nm(x["f"]), "a": round(x["a"], 3), "odds_x": round(math.exp(x["a"]), 2),  # noqa
                     "dp": round(x["dp"], 4), "z": round(x["z"], 2), "exposed": x["exposed"],
                     "branch": x.get("g_branch")}
    totals = sorted(((float(T[i, j]), fids[i], fids[j]) for i in range(len(fids)) for j in range(len(fids))
                     if T is not None and T[i, j] > 1e-4), reverse=True)[:12] if T is not None else []
    loops = []
    for x in sig:
        back = next((y for y in sig if y["g"] == x["f"] and y["f"] == x["g"]), None)
        if back and x["g"] < x["f"]:
            loops.append({"a": nm(x["g"]), "b": nm(x["f"]), "ab": round(x["a"], 3), "ba": round(back["a"], 3)})
    return {"tested": {f"{x['g']}>{x['f']}": [round(x["a"], 4), round(x["z"], 3), x["exposed"]] for x in links},
            "links": [{"g": x["g"], "f": x["f"], "a": round(x["a"], 3), "odds": round(math.exp(x["a"]), 2),
                       "dp": round(x["dp"], 4), "z": round(x["z"], 2), "exposed": x["exposed"],
                       "branch": x.get("g_branch")} for x in sorted(sig, key=lambda r: -r["z"])],
            "behaviours": len(fitted), "pairs_tested": len(links), "significant": len(sig),
            "seen_per_stretch": {nm(g): round(v, 2) for g, v in sorted(c.items(), key=lambda kv: -kv[1])[:5]},
            "same_behaviour": sorted((fmt(x) for x in sig if x["g"] == x["f"]), key=lambda r: -r["z"]),
            "other_behaviour": sorted((fmt(x) for x in sig if x["g"] != x["f"]), key=lambda r: -r["z"]),
            "R": round(R, 4), "total_top": [{"from": nm(g), "to": nm(f), "t": round(t, 4)} for t, g, f in totals],
            "two_way": loops,
            "heldout": {"behaviours": len(gains), "better": sum(1 for v in gains.values() if v > 0),
                        "total_gain": round(sum(gains.values()), 4), "cut": cut} if cut else None}
