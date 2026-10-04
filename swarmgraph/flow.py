"""Flow: how behaviours move through a multi-agent record, as numbers an agent can query (docs/FLOW.md).

People watch the behaviour atlas play and see which behaviours are on, when the mix changes, and which behaviour
travels from whom to whom. This module defines the same things on the behaviour features, so an agent forming
hypotheses can ask for them instead of reading summaries that had to be cut to fit.

  unit u        a stretch of work: start t(u), actor a(u), place c(u). Judged units carry activations x[u][f] (share
                of their events the judges marked); f is present in u when x[u][f] > 0. `uniform` units were drawn at
                random from all stretches: every rate uses only these as targets.
  edge v -> u   v starts no later than u, different actors (except `next`):
                  reuse    u reuses word sequences that v wrote first (shared shingles, counted by code)
                  reply    an event in u answers an event in v (reply fields and reply windows of the index)
                  address  v mentions u's actor, and u is that actor's next stretch within a day
                  channel  v is one of the last 3 stretches by others in u's place in the hour before u (what u
                           could have seen there)
                  next     u is the same actor's next judged stretch within a day (persistence and sequence, not spread)
  state         p_f(t): share of uniform units in bin t that show f, with a Wilson 95% interval
  regimes       the split of time at day boundaries into K segments that minimises the within-segment squared error
                of the units' behaviour vectors (exact dynamic programming); K is chosen by split-half prediction:
                boundaries and means fitted on one random half of the units must lower the squared error on the other
                half. Each regime is described by the behaviours over-represented in it (lift, z)
  transmission  over target units with at least one judged source along the edges: risk ratio of f in the target
                when a source shows f versus when none does, Mantel-Haenszel over ISO weeks of the target (so a trend
                shared by the whole week cancels), with a Greenland-Robins 95% interval; and observed / expected
                against the week's base rate of f
  adoption      over units whose actor had shown f in no earlier judged unit: risk ratio of f when a source shows f
                versus not (no judged source counts as not), by weeks
  coupling      A -> B: risk ratio of B in the target when a source shows A versus not, by weeks
  cascades      groups of units showing f joined by edges: who passed it to whom, and when

What the numbers are: associations that fit a behaviour travelling along the edges, net of week-wide trends. They do
not prove influence (similar actors may connect and behave alike, and unseen causes may reach both), and they see only
judged units: every answer reports how many edges could be measured.
"""
import bisect
import collections
import hashlib
import math
from datetime import datetime

EXPOSE = ("reuse", "reply", "address", "channel")
EDGES = EXPOSE + ("next",)
HOUR, DAY = 3600, 86400
CHANNEL_K = 3
MIN_CASES = 5  # exposed targets showing the behaviour before a ratio is ranked
_CACHE = {}


def _ts(s):
    return datetime.fromisoformat(s[:19].replace("T", " ")).timestamp()


def _week(day):
    y, w, _ = datetime.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def _half(x):
    return hashlib.md5(str(x).encode()).digest()[0] % 2


def _r(x, n=3):
    return None if x is None else round(x, n)


# ---------------------------------------------------------------- statistics

def wilson(k, n, z=1.96):
    """95% interval for a share k/n"""
    if not n:
        return None, None
    p, d = k / n, 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)


def mh_rr(strata):
    """Mantel-Haenszel risk ratio over strata of (exposed with, exposed, unexposed with, unexposed), with the
    Greenland-Robins 95% interval; strata without both groups are left out. A stratum with an empty cell (no case,
    or every one a case, in a group) gets half a case and half a non-case added to each group (Haldane-Anscombe):
    without it a group where all 9 of 9 show the behaviour gives an interval far too narrow"""
    num = den = var = 0.0
    a_t = n1_t = c_t = n0_t = 0
    for a, n1, c, n0 in strata:
        if not n1 or not n0:
            continue
        a_t, n1_t, c_t, n0_t = a_t + a, n1_t + n1, c_t + c, n0_t + n0
        if 0 in (a, n1 - a, c, n0 - c):
            a, n1, c, n0 = a + 0.5, n1 + 1, c + 0.5, n0 + 1
        n = n1 + n0
        num += a * n0 / n
        den += c * n1 / n
        var += (n1 * n0 * (a + c) - a * c * n) / (n * n)
    out = {"exposed": n1_t, "exposed_with": a_t, "unexposed": n0_t, "unexposed_with": c_t,
           "rr": None, "lo": None, "hi": None}
    if num and den:
        rr = num / den
        se = math.sqrt(var / (num * den)) if var > 0 else 0.0
        out.update(rr=round(rr, 3), lo=round(rr * math.exp(-1.96 * se), 3), hi=round(rr * math.exp(1.96 * se), 3))
    return out


def two_prop_z(k1, n1, k0, n0):
    if not n1 or not n0:
        return None
    p = (k1 + k0) / (n1 + n0)
    s = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n0))
    return round((k1 / n1 - k0 / n0) / s, 2) if s else None


def _cos_dist(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return round(1 - sum(x * y for x, y in zip(a, b)) / (na * nb), 3) if na and nb else None


# ---------------------------------------------------------------- the model

class Flow:
    """units: [{id, start, actor, channel, ids, f: {feature: activation} or None (not judged), uniform}];
    edges: {type: iterable of (v, u)}; features: [{id, text, name}]"""

    def __init__(self, units, edges, features, notes=(), labels=None):
        self.labels = labels or {}
        self.u = {}
        for x in units:
            self.u[x["id"]] = dict(x, t=_ts(x["start"]), day=x["start"][:10], week=_week(x["start"][:10]))
        self.features = [f for f in features]
        self.fids = [f["id"] for f in self.features]
        self.label = {f["id"]: f.get("name") or f["text"] for f in self.features}
        self.text = {f["id"]: f["text"] for f in self.features}
        self.has = {i: {f for f, v in (x["f"] or {}).items() if v > 0 and f in self.label}
                    for i, x in self.u.items() if x["f"] is not None}
        self.uni = sorted((i for i in self.has if self.u[i]["uniform"]), key=lambda i: (self.u[i]["t"], i))
        # targets of spread measures: the uniform sample and the flow sample (chosen by edges, not by behaviour);
        # rates and regimes use the uniform sample only
        self.tgt = sorted((i for i in self.has if self.u[i].get("target", self.u[i]["uniform"])),
                          key=lambda i: (self.u[i]["t"], i))
        self.notes = list(notes)
        self.src = {k: collections.defaultdict(set) for k in EDGES}
        self.n_edges = {}
        for k in EDGES:
            n = 0
            for v, w in edges.get(k, ()):
                if v in self.u and w in self.u and v != w:
                    self.src[k][w].add(v)
                    n += 1
            self.n_edges[k] = n
        # base rate of each behaviour per week, from the uniform units
        self.week_n = collections.Counter(self.u[i]["week"] for i in self.uni)
        wk = collections.defaultdict(collections.Counter)
        for i in self.uni:
            wk[self.u[i]["week"]].update(self.has[i])
        self.week_rate = {w: {f: c / self.week_n[w] for f, c in cnt.items()} for w, cnt in wk.items()}
        self.overall = collections.Counter(f for i in self.uni for f in self.has[i])
        self._memo, self._srcs = {}, {}

    # -- helpers

    def _sources(self, u, edge):
        """judged sources of u along one edge type, or along every exposure type ('any')"""
        key = (u, edge)
        if key not in self._srcs:
            types = EXPOSE if edge == "any" else (edge,)
            out = set()
            for k in types:
                out |= {v for v in self.src[k].get(u, ()) if v in self.has and self.u[v]["t"] <= self.u[u]["t"]}
            self._srcs[key] = frozenset(out)
        return self._srcs[key]

    def _targets(self, edge, since=None, until=None, actors=None, connected=True):
        """(target, judged sources) for target units in the window"""
        for u in self.tgt:
            x = self.u[u]
            if since and x["start"] < since or until and x["start"] >= until:
                continue
            if actors is not None and x["actor"] not in actors:
                continue
            s = self._sources(u, edge)
            if s or not connected:
                yield u, s

    def ref(self, uid, n=3):
        x = self.u[uid]
        return {"actor": self.who(x["actor"]), "start": x["start"][:16], "events": x["ids"][:n]}

    def who(self, actor):
        return self.labels.get(actor) or actor

    def name(self, f):
        return f"{f} ({self.label.get(f, '?')})"

    def coverage(self):
        """how much of each edge type can be measured: a judged source and a uniform target"""
        out = {}
        tgt = set(self.tgt)
        for k in EDGES:
            usable = sum(1 for w, vs in self.src[k].items() if w in tgt for v in vs if v in self.has)
            targets = sum(1 for w, vs in self.src[k].items() if w in tgt and any(v in self.has for v in vs))
            out[k] = {"edges": self.n_edges[k], "measurable": usable, "targets": targets}
        return {"stretches": len(self.u), "judged": len(self.has), "uniform": len(self.uni),
                "flow_sample": len(self.tgt) - len(self.uni), "edges": out}

    # -- state

    def state(self, f, unit=None):
        """share of uniform units showing f per day (records up to 45 days) or week, with intervals; first seen,
        peak, last seen; actors showing it for the first time per bin"""
        days = [self.u[i]["day"] for i in self.uni]
        if not days:
            return {"bins": []}
        span = (_ts(days[-1]) - _ts(days[0])) / DAY
        unit = unit or ("day" if span <= 45 else "week")
        key = (lambda d: d) if unit == "day" else _week
        n, k = collections.Counter(), collections.Counter()
        for i in self.uni:
            b = key(self.u[i]["day"])
            n[b] += 1
            k[b] += f in self.has[i]
        first_by_actor = {}
        for i in sorted(self.has, key=lambda i: self.u[i]["t"]):
            if f in self.has[i]:
                first_by_actor.setdefault(self.u[i]["actor"], key(self.u[i]["day"]))
        new = collections.Counter(first_by_actor.values())
        bins = []
        for b in sorted(n):
            lo, hi = wilson(k[b], n[b])
            bins.append({"bin": b, "n": n[b], "with": k[b], "p": round(k[b] / n[b], 4), "lo": lo, "hi": hi,
                         "new_actors": new.get(b, 0)})
        on = [i for i in self.uni if f in self.has[i]]
        peak = max((b for b in bins if b["n"] >= 10), key=lambda b: b["p"], default=None)
        return {"unit": unit, "overall": round(self.overall[f] / max(1, len(self.uni)), 4),
                "first_seen": self.u[on[0]]["start"][:16] if on else None,
                "last_seen": self.u[on[-1]]["start"][:16] if on else None,
                "peak": peak and {"bin": peak["bin"], "p": peak["p"], "n": peak["n"]},
                "actors": len(first_by_actor), "bins": bins}

    # -- regimes

    def _prefix(self, ids, days):
        at = {d: k for k, d in enumerate(days)}
        F = len(self.fids)
        col = {f: j for j, f in enumerate(self.fids)}
        N = [0] * (len(days) + 1)
        C = [[0] * F for _ in range(len(days) + 1)]
        per_n = collections.Counter()
        per_c = collections.defaultdict(lambda: [0] * F)
        for i in ids:
            d = at[self.u[i]["day"]]
            per_n[d] += 1
            row = per_c[d]
            for f in self.has[i]:
                row[col[f]] += 1
        for d in range(len(days)):
            N[d + 1] = N[d] + per_n[d]
            prev, row, cur = C[d], per_c.get(d), C[d + 1]
            for j in range(F):
                cur[j] = prev[j] + (row[j] if row else 0)
        return N, C

    @staticmethod
    def _dp(N, C, kmax, nmin):
        """{K: (sse, cuts)} for K = 1..kmax: exact least squares segmentation at day boundaries"""
        D = len(N) - 1
        inf = float("inf")
        cost = [[inf] * (D + 1) for _ in range(D + 1)]
        for i in range(D):
            ci = C[i]
            for j in range(i + 1, D + 1):
                n = N[j] - N[i]
                if n < nmin:
                    continue
                cj = C[j]
                s = 0.0
                for a, b in zip(ci, cj):
                    c = b - a
                    if c:
                        s += c - c * c / n
                cost[i][j] = s
        best = [[inf] * (D + 1) for _ in range(kmax + 1)]
        back = [[0] * (D + 1) for _ in range(kmax + 1)]
        best[0][0] = 0.0
        for k in range(1, kmax + 1):
            for j in range(1, D + 1):
                bk, bi = inf, 0
                for i in range(k - 1, j):
                    v = best[k - 1][i] + cost[i][j]
                    if v < bk:
                        bk, bi = v, i
                best[k][j], back[k][j] = bk, bi
        out = {}
        for k in range(1, kmax + 1):
            if best[k][D] == inf:
                continue
            cuts, j = [D], D
            for kk in range(k, 0, -1):
                j = back[kk][j]
                cuts.append(j)
            out[k] = (best[k][D], cuts[::-1])
        return out

    @staticmethod
    def _heldout(fit, test, cuts):
        """squared error on `test` of the segment means fitted on `fit`"""
        (Nf, Cf), (Nt, Ct) = fit, test
        tot_n = Nf[-1] or 1
        sse = 0.0
        for i, j in zip(cuts, cuts[1:]):
            nf, nt = Nf[j] - Nf[i], Nt[j] - Nt[i]
            for a, b, c, d, g in zip(Cf[i], Cf[j], Ct[i], Ct[j], Cf[-1]):
                mu = (b - a) / nf if nf else g / tot_n
                ct = d - c
                sse += ct - 2 * mu * ct + nt * mu * mu
        return sse

    def regimes(self, kmax=8, min_units=25, min_share=0.03):
        if "regimes" in self._memo:
            return self._memo["regimes"]
        ids = self.uni
        days = sorted({self.u[i]["day"] for i in ids})

        def single(note):
            g = self._describe(ids, days[0], days[-1]) if days else {"n": 0}
            g.pop("_k", None)
            out = {"k": 1, "regimes": [g], "boundaries": [], "heldout_explained": 0.0, "note": note}
            self._memo["regimes"] = out
            return out
        if len(ids) < 2 * min_units or len(days) < 2:
            return single("too few uniform stretches to split time")
        nmin = max(min_units, int(min_share * len(ids)))
        full = self._prefix(ids, days)
        halves = [self._prefix([i for i in ids if _half(i) == h], days) for h in (0, 1)]
        fits = [self._dp(*p, kmax, max(1, nmin // 2)) for p in halves]
        sol = self._dp(*full, kmax, nmin)
        cv = {}
        for k in sol:
            if k in fits[0] and k in fits[1]:
                cv[k] = self._heldout(halves[0], halves[1], fits[0][k][1]) + \
                        self._heldout(halves[1], halves[0], fits[1][k][1])
        if 1 not in cv:
            return single("too few uniform stretches to split time")
        best = min(cv.values())
        k = min(kk for kk, v in cv.items() if v <= best + 0.001 * cv[1])  # the simplest within 0.1% of the best
        cuts = sol[k][1]

        def near(d, cs):
            return any(abs(_ts(days[d]) - _ts(days[c])) <= 3 * DAY for c in cs[1:-1])
        regs = []
        for i, j in zip(cuts, cuts[1:]):
            sel = [u for u in ids if days[i] <= self.u[u]["day"] <= days[j - 1]]
            regs.append(self._describe(sel, days[i], days[j - 1]))
        bounds = []
        for r, (a, b) in enumerate(zip(regs, regs[1:])):
            c = cuts[r + 1]
            pa = [a["_k"][f] / a["n"] for f in self.fids]
            pb = [b["_k"][f] / b["n"] for f in self.fids]
            moves = []
            for f, x, y in zip(self.fids, pa, pb):
                z = two_prop_z(b["_k"][f], b["n"], a["_k"][f], a["n"])
                if z is not None and abs(z) >= 2:
                    moves.append((y - x, f, x, y, z))
            moves.sort()
            fmt = lambda m: {"id": m[1], "behaviour": self.name(m[1]), "before": round(m[2], 3),
                             "after": round(m[3], 3), "z": m[4]}
            bounds.append({"date": days[c], "shift": _cos_dist(pa, pb),
                           "stable_in_both_halves": all(near(c, fits[h][k][1]) for h in (0, 1)) if k > 1 else None,
                           "rose": [fmt(m) for m in moves[::-1][:4] if m[0] > 0],
                           "fell": [fmt(m) for m in moves[:3] if m[0] < 0]})
        for g in regs:
            del g["_k"]
        explained = round(1 - cv[k] / cv[1], 4) if cv[1] else 0.0
        out = {"k": k, "heldout_explained": explained,
               "heldout_by_k": {kk: round(1 - v / cv[1], 4) for kk, v in sorted(cv.items())},
               "regimes": regs, "boundaries": bounds}
        if explained < 0.01:
            out["note"] = ("the regimes explain under 1% of the variation on held-out stretches: the overall mix of "
                           "behaviour changes little at this level. Look at single behaviours (flow_feature) and at "
                           "spread instead")
        self._memo["regimes"] = out
        return out

    def _describe(self, sel, start, end):
        n = len(sel)
        k = collections.Counter(f for i in sel for f in self.has[i])
        rest_n = len(self.uni) - n
        top = []
        for f in self.fids:
            if k[f] < 5:
                continue
            rk = self.overall[f] - k[f]
            z = two_prop_z(k[f], n, rk, rest_n)
            p, base = k[f] / n, self.overall[f] / len(self.uni)
            if z is not None and z >= 2:
                top.append((p / base, z, f, p, (rk / rest_n) if rest_n else None))
        top.sort(reverse=True)
        allu = [x for x in self.u.values() if start and start <= x["day"] <= end]
        return {"start": start, "end": end, "n": n, "stretches": len(allu), "actors": len({x["actor"] for x in allu}),
                "defining": [{"id": f, "behaviour": self.name(f), "share": round(p, 3), "elsewhere": _r(q),
                              "lift": round(l, 2), "z": z} for l, z, f, p, q in top[:5]], "_k": k}

    # -- shifts between windows

    def shift(self, start, end, base=None, limit=8):
        """behaviours over- and under-represented in [start, end) against a base window (start, end), or against
        the rest of the record"""
        inside = [i for i in self.uni if start <= self.u[i]["start"] < end]
        if base:
            rest = [i for i in self.uni if base[0] <= self.u[i]["start"] < base[1]]
        else:
            rest = [i for i in self.uni if not start <= self.u[i]["start"] < end]
        if not inside or not rest:
            return {"start": start, "end": end, "error": "no uniform stretches in one of the windows"}
        ki = collections.Counter(f for i in inside for f in self.has[i])
        kr = collections.Counter(f for i in rest for f in self.has[i])
        moves = []
        for f in self.fids:
            z = two_prop_z(ki[f], len(inside), kr[f], len(rest))
            if z is not None and (ki[f] or kr[f]):
                moves.append((z, f))
        moves.sort()
        fmt = lambda z, f: {"behaviour": self.name(f), "inside": round(ki[f] / len(inside), 3),
                            "elsewhere": round(kr[f] / len(rest), 3), "z": z}
        allu = [x for x in self.u.values() if start <= x["start"] < end]
        before = {x["actor"] for x in self.u.values() if x["start"] < start}
        return {"start": start, "end": end, "against": list(base) if base else "the rest of the record",
                "uniform_inside": len(inside), "uniform_against": len(rest), "stretches": len(allu),
                "actors": len({x["actor"] for x in allu}), "new_actors": len({x["actor"] for x in allu} - before),
                "higher": [fmt(z, f) for z, f in moves[::-1][:limit] if z >= 2],
                "lower": [fmt(z, f) for z, f in moves[:limit] if z <= -2]}

    # -- spread

    def transmission(self, f, edge="any", since=None, until=None, actors=None):
        strata = collections.defaultdict(lambda: [0, 0, 0, 0])
        obs = exp = 0.0
        examples = []
        for u, srcs in self._targets(edge, since, until, actors):
            s = strata[self.u[u]["week"]]
            hit = f in self.has[u]
            if any(f in self.has[v] for v in srcs):
                s[0] += hit
                s[1] += 1
                obs += hit
                exp += self.week_rate.get(self.u[u]["week"], {}).get(f, 0.0)
                if hit and len(examples) < 3:
                    v = min((v for v in srcs if f in self.has[v]), key=lambda v: self.u[v]["t"])
                    examples.append({"from": self.ref(v), "to": self.ref(u)})
            else:
                s[2] += hit
                s[3] += 1
        out = mh_rr(strata.values())
        out.update(observed=int(obs), expected=round(exp, 1), o_e=_r(obs / exp if exp else None, 2), examples=examples)
        return out

    def adoption(self, f, since=None, until=None, actors=None):
        first = {}
        for i in sorted(self.has, key=lambda i: self.u[i]["t"]):
            if f in self.has[i]:
                first.setdefault(self.u[i]["actor"], self.u[i]["t"])
        strata = collections.defaultdict(lambda: [0, 0, 0, 0])
        for u, srcs in self._targets("any", since, until, actors, connected=False):
            x = self.u[u]
            if x["actor"] in first and x["t"] > first[x["actor"]]:
                continue  # already shown earlier: not at risk
            s = strata[x["week"]]
            hit = f in self.has[u]
            if any(f in self.has[v] for v in srcs):
                s[0] += hit
                s[1] += 1
            else:
                s[2] += hit
                s[3] += 1
        return mh_rr(strata.values())

    def spreaders(self, f, edge="any", limit=5):
        cnt, ex = collections.Counter(), {}
        for u, srcs in self._targets(edge):
            if f not in self.has[u]:
                continue
            for v in srcs:
                if f in self.has[v]:
                    a = self.u[v]["actor"]
                    cnt[a] += 1
                    ex.setdefault(a, {"from": self.ref(v), "to": self.ref(u)})
        return [{"actor": self.who(a), "passed_on": c, "example": ex[a]} for a, c in cnt.most_common(limit)]

    def coupling(self, a=None, b=None, edge="any", since=None, until=None, actors=None, limit=10, min_cases=MIN_CASES):
        """A -> B over the edges; one pair when a and b are given, else the strongest pairs (lower bound first)"""
        if a and b:
            strata = collections.defaultdict(lambda: [0, 0, 0, 0])
            for u, srcs in self._targets(edge, since, until, actors):
                s = strata[self.u[u]["week"]]
                hit = b in self.has[u]
                if any(a in self.has[v] for v in srcs):
                    s[0] += hit
                    s[1] += 1
                else:
                    s[2] += hit
                    s[3] += 1
            return {"a": a, "b": b, "from": self.name(a), "to": self.name(b), "edge": edge} | mh_rr(strata.values())
        key = ("pairs", edge, since, until)
        if key not in self._memo:
            ex = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))  # week -> A -> B
            n1 = collections.defaultdict(collections.Counter)  # week -> A
            cases = collections.defaultdict(collections.Counter)  # week -> B
            total = collections.Counter()
            for u, srcs in self._targets(edge, since, until, actors):
                w = self.u[u]["week"]
                total[w] += 1
                cases[w].update(self.has[u])
                seen = set().union(*(self.has[v] for v in srcs))
                n1[w].update(seen)
                for x in seen:
                    ex[w][x].update(self.has[u])
            res = []
            for x in self.fids:
                for y in self.fids:
                    if x == y:
                        continue
                    st = [(ex[w][x][y], n1[w][x], cases[w][y] - ex[w][x][y], total[w] - n1[w][x]) for w in total]
                    if sum(s[0] for s in st) < min_cases:
                        continue
                    r = mh_rr(st)
                    if r["exposed_with"] >= min_cases and r["lo"] is not None and r["lo"] > 1:
                        res.append((r["lo"], x, y, r))
            res.sort(key=lambda t: (-t[0], t[1], t[2]))
            self._memo[key] = res
        return [{"a": x, "b": y, "from": self.name(x), "to": self.name(y)} | r
                for _, x, y, r in self._memo[key][:limit]]

    def cascades(self, f, edge="any", limit=5):
        on = {i for i in self.has if f in self.has[i]}
        parent = {i: i for i in on}

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        kids = collections.defaultdict(set)
        for u in on:
            for v in self._sources(u, edge):
                if v in on:
                    kids[v].add(u)
                    parent[find(v)] = find(u)
        groups = collections.defaultdict(list)
        for i in on:
            groups[find(i)].append(i)
        out = []
        for g in groups.values():
            if len(g) < 2:
                continue
            g.sort(key=lambda i: (self.u[i]["t"], i))
            depth = {}
            for i in reversed(g):
                depth[i] = 1 + max((depth[k] for k in kids[i] if k in depth), default=0)
            actors = []
            for i in g:
                if self.u[i]["actor"] not in actors:
                    actors.append(self.u[i]["actor"])
            out.append({"units": len(g), "actors": len(actors), "depth": max(depth.values()),
                        "from": self.u[g[0]]["start"][:16], "to": self.u[g[-1]]["start"][:16],
                        "order": [self.who(a) for a in actors[:10]], "root": self.ref(g[0]),
                        "members": [self.ref(i, 1) for i in g[:10]]})
        out.sort(key=lambda c: (-c["actors"], -c["units"], c["from"]))
        return {"cascades": len(out), "multi_actor": sum(1 for c in out if c["actors"] > 1),
                "largest": out[:limit]}

    # -- one behaviour, the whole record

    def feature(self, f):
        if f not in self.label:
            return {"error": f"no behaviour {f}"}
        tr = {k: {x: v for x, v in self.transmission(f, k).items() if x != "examples"} for k in EXPOSE + ("next",)}
        anyt = self.transmission(f, "any")
        return {"behaviour": self.name(f), "text": self.text[f], "state": self.state(f),
                "transmission": {"any": anyt} | tr, "adoption": self.adoption(f), "spreaders": self.spreaders(f),
                "cascades": {k: v for k, v in self.cascades(f, limit=2).items()}}

    def spread_ranking(self, edge="any", limit=8, min_cases=MIN_CASES):
        key = ("rank", edge)
        if key not in self._memo:
            res = []
            for f in self.fids:
                r = self.transmission(f, edge)
                if r["exposed_with"] >= min_cases and r["lo"] is not None and r["lo"] > 1:
                    res.append((r["lo"], f, r))
            res.sort(key=lambda t: (-t[0], t[1]))
            self._memo[key] = res
        return [{"id": f, "behaviour": self.name(f), "rr": r["rr"], "lo": r["lo"], "hi": r["hi"], "o_e": r["o_e"],
                 "exposed": r["exposed"], "exposed_with": r["exposed_with"]} for _, f, r in self._memo[key][:limit]]

    def overview(self):
        reg = self.regimes()
        return {"coverage": self.coverage(), "regimes": reg, "spreads_most": self.spread_ranking(),
                "spreads_by_edge": {k: self.spread_ranking(k, limit=3) for k in EXPOSE},
                "persists_within_actor": self.spread_ranking("next", limit=3),
                "couplings": self.coupling(limit=8), "notes": self.notes}

    # -- held-out tests

    def test(self, claim, split):
        """measure a claim on a discovery part and a held-out part of the record; the verdict uses the held-out part"""
        kind = claim.get("kind")
        by = (split or {}).get("by", "time")
        if by == "time":
            at = split.get("at")
            if not at:
                return {"error": "split by time needs `at` (a date): discovery before it, test from it on"}
            if kind == "shift":
                return {"error": "a shift is a claim about a time window: split it by actors"}
            parts = {"discovery": {"until": at}, "test": {"since": at}}
        elif by == "actors":
            names = {x["actor"] for x in self.u.values()}
            parts = {"discovery": {"actors": {a for a in names if _half(a) == 0}},
                     "test": {"actors": {a for a in names if _half(a) == 1}}}
        else:
            return {"error": "split.by is time or actors"}
        out = {"claim": claim, "split": {k: v for k, v in (split or {}).items()}}
        for part, kw in parts.items():
            if kind == "transmission":
                r = self.transmission(claim["feature"], claim.get("edge", "any"), **kw)
                r.pop("examples", None)
            elif kind == "adoption":
                r = self.adoption(claim["feature"], **kw)
            elif kind == "coupling":
                r = self.coupling(claim["a"], claim["b"], claim.get("edge", "any"), **kw)
            elif kind == "shift":
                r = self._shift_one(claim["feature"], claim["start"], claim["end"], kw.get("actors"))
            else:
                return {"error": "claim.kind is transmission, adoption, coupling or shift"}
            out[part] = r
        out["verdict"] = self._verdict(kind, out["test"], claim.get("direction", "up"))
        return out

    def _shift_one(self, f, start, end, actors=None):
        sel = [i for i in self.uni if actors is None or self.u[i]["actor"] in actors]
        inside = [i for i in sel if start <= self.u[i]["start"] < end]
        rest = [i for i in sel if not start <= self.u[i]["start"] < end]
        ki, kr = sum(f in self.has[i] for i in inside), sum(f in self.has[i] for i in rest)
        return {"inside": len(inside), "inside_with": ki, "elsewhere": len(rest), "elsewhere_with": kr,
                "z": two_prop_z(ki, len(inside), kr, len(rest))}

    @staticmethod
    def _verdict(kind, r, direction="up"):
        if kind == "shift":
            z = r.get("z")
            if z is None or min(r["inside_with"] + r["elsewhere_with"], r["inside"]) < MIN_CASES:
                return "too few cases"
            z = z if direction == "up" else -z
            return "holds" if z >= 1.96 else "contradicted" if z <= -1.96 else "undecided"
        if r.get("exposed_with", 0) < MIN_CASES or r.get("lo") is None:
            return "too few cases"
        return "holds" if r["lo"] > 1 else "contradicted" if (r["hi"] is not None and r["hi"] < 1) else "undecided"


# ---------------------------------------------------------------- from an index

def edges_from(con, rows, us, judged):
    """the edge sets of the module docstring, as {type: set((source stretch, target stretch))}"""
    from . import features as FE
    start = {u["id"]: _ts(u["start"]) for u in us}
    actor = {u["id"]: u["actor"] for u in us}
    ev_unit = {e: u["id"] for u in us for e in u["ids"]}
    ev_ts = {e["id"]: e["ts"] for e in rows}

    def ok(v, w):
        return v != w and actor[v] != actor[w] and start[v] <= start[w]
    out = {k: set() for k in EDGES}
    out["reuse"] = {(v, w) for v, w in FE.reuse_edges(rows, us) if ok(v, w)}
    pairs = [(e["reply_to"], e["id"]) for e in rows if e.get("reply_to")]
    pairs += con.execute("SELECT answers, event_id FROM relations "
                         "WHERE type='replied' AND answers IS NOT NULL").fetchall()
    for a, b in pairs:
        v, w = ev_unit.get(a), ev_unit.get(b)
        if v is not None and w is not None and ok(v, w):
            out["reply"].add((v, w))
    by_actor = collections.defaultdict(list)
    for u in us:
        by_actor[u["actor"]].append((start[u["id"]], u["id"]))
    for lst in by_actor.values():
        lst.sort()
    for ev, dst, ts in con.execute("SELECT event_id, dst, ts FROM relations WHERE type='addressed' "
                                   "AND COALESCE(method, '') != 'reply_field'"):
        v = ev_unit.get(ev)
        lst = by_actor.get(dst)
        if v is None or not lst:
            continue
        t = _ts(ev_ts.get(ev, ts))
        k = bisect.bisect_left(lst, (t, -1))
        if k < len(lst) and lst[k][0] - t <= DAY and ok(v, lst[k][1]):
            out["address"].add((v, lst[k][1]))
    by_channel = collections.defaultdict(list)
    for u in us:
        if u.get("channel"):
            by_channel[u["channel"]].append((start[u["id"]], u["id"]))
    for lst in by_channel.values():
        lst.sort()
        for k, (t, w) in enumerate(lst):
            got, j = 0, k - 1
            while j >= 0 and got < CHANNEL_K and t - lst[j][0] <= HOUR:
                v = lst[j][1]
                if actor[v] != actor[w]:
                    out["channel"].add((v, w))
                    got += 1
                j -= 1
    seq = collections.defaultdict(list)
    for uid in judged:
        seq[actor[uid]].append((start[uid], uid))
    for lst in seq.values():
        lst.sort()
        for (t0, v), (t1, w) in zip(lst, lst[1:]):
            if t1 - t0 <= DAY:
                out["next"].add((v, w))
    return out


def load(con):
    """the flow model of this index (cached per state of the index and the atlas), or None without an atlas"""
    from . import features as FE
    d = FE.get(con)
    if not d or "atlas" not in d:
        return None
    a = d["atlas"]
    key = (con.execute("PRAGMA database_list").fetchone()[2], len(a["units"]), a.get("coverage"))
    if key in _CACHE:
        return _CACHE[key]
    rows, us = FE.units(con)
    judged = {u["id"]: u for u in a["units"]}
    flagged = any("uniform" in u for u in a["units"])
    units = [{"id": u["id"], "start": u["start"], "actor": u["actor"], "channel": u["channel"], "ids": u["ids"],
              "f": judged[u["id"]]["f"] if u["id"] in judged else None,
              "uniform": bool(judged[u["id"]].get("uniform", True)) if u["id"] in judged else False,
              "target": bool(judged[u["id"]].get("uniform", True) or judged[u["id"]].get("flow"))
              if u["id"] in judged else False} for u in us]
    short = d.get("short_names") or {}
    feats = []
    for f in a["features"]:
        n = short.get(f["id"]) or {}
        feats.append({"id": f["id"], "text": f["text"], "name": n.get("name") if n.get("text") == f["text"] else None})
    notes = [] if flagged else ["the atlas does not mark which judged stretches were drawn uniformly (rebuild it with "
                                "`features build`); all judged stretches are used as targets, so flagged ones that "
                                "were over-sampled can raise rates"]
    labels = dict(con.execute("SELECT id, label FROM actors"))
    m = Flow(units, edges_from(con, rows, us, judged), feats, notes, labels)
    for k in [k for k in _CACHE if k[0] == key[0]]:
        del _CACHE[k]
    _CACHE[key] = m
    return m


# ---------------------------------------------------------------- what the tools return

HOW = ("Numbers computed by code from the behaviour atlas (no LLM). A stretch is one actor's events in a row; judged "
       "stretches carry the behaviours AI judges marked; rates use only the uniform sample. Edges: reuse (copied "
       "words), reply, address (a mention, then the addressee's next stretch within a day), channel (what was posted "
       "in the same place in the hour before), next (the same actor's next stretch: persistence, not spread). "
       "rr = risk ratio of the behaviour in a stretch when a source along the edges shows it (or shows A, for "
       "couplings) versus not, Mantel-Haenszel over weeks so a trend shared by everyone that week cancels; lo/hi = "
       "95% interval; o_e = observed / expected from that week's base rate. Regimes = the split of time that best "
       "predicts held-out stretches' behaviour. These are associations that fit spread, not proof of influence: "
       "behaviour carried inside copied text spreads mechanically along reuse edges, and similar actors may connect. "
       "Open the events before you rely on a number.")


def _model(con):
    m = load(con)
    if m is None:
        raise ValueError("no behaviour atlas yet (docs/BEHAVIOUR_FEATURES.md)")
    return m


def overview_view(con):
    m = _model(con)
    return {"how": HOW, **m.overview(),
            "next": "flow_shift around a boundary for what changed and which chunks to read; flow_feature for one "
                    "behaviour's course and spread; flow_cascades for who passed it to whom; flow_coupling for which "
                    "behaviour follows which; flow_test to check a claim on a held-out part before you report it."}


def feature_view(con, fid):
    return {"how": HOW} | _model(con).feature(fid)


def shift_view(con, start=None, end=None, at=None, days=3):
    from datetime import timedelta
    m = _model(con)
    if at:  # the `days` active days from `at` on, against the `days` active days before it (quiet days skipped)
        active = sorted({m.u[i]["day"] for i in m.uni})
        k = bisect.bisect_left(active, at[:10])
        after, before = active[k:k + days], active[max(0, k - days):k]
        if not after or not before:
            return {"error": f"no sampled stretches on active days on both sides of {at[:10]}"}
        nxt = lambda d: (datetime.fromisoformat(d) + timedelta(days=1)).strftime("%Y-%m-%d")
        start, end, base = after[0], nxt(after[-1]), (before[0], nxt(before[-1]))
    elif start and end:
        base = None
    else:
        return {"error": "give start and end (a window against the rest), or at and days (after against before)"}
    out = m.shift(start, end, base)
    try:
        ch = con.execute("SELECT id, start FROM w.chunks WHERE start < ? AND end >= ? ORDER BY start",
                         (end, start)).fetchall()
        out["chunks_to_read"] = {"ids": [c[0] for c in ch[:40]], "of": len(ch),
                                 "how": "open one with `chunk {\"id\": N}` (what the readers found there), or ask your "
                                        "own question of them with `delegate`"}
    except Exception:
        pass
    return out


def cascades_view(con, fid, edge="any", limit=5):
    m = _model(con)
    if fid not in m.label:
        return {"error": f"no behaviour {fid}"}
    return {"behaviour": m.name(fid), "edge": edge} | m.cascades(fid, edge, limit)


def coupling_view(con, a=None, b=None, edge="any", limit=10):
    m = _model(con)
    for f in (a, b):
        if f and f not in m.label:
            return {"error": f"no behaviour {f}"}
    if a and b:
        return m.coupling(a, b, edge)
    if a or b:  # one side given: every partner, the strongest first
        rows = [m.coupling(a or x, b or x, edge) for x in m.fids if x != (a or b)]
        rows = [r for r in rows if r["exposed_with"] >= MIN_CASES and r["lo"] is not None and r["lo"] > 1]
        rows.sort(key=lambda r: -r["lo"])
        return {"edge": edge, "pairs": rows[:limit]}
    return {"edge": edge, "pairs": m.coupling(edge=edge, limit=limit)}


def test_view(con, claim, split):
    m = _model(con)
    for k in ("feature", "a", "b"):
        if claim.get(k) and claim[k] not in m.label:
            return {"error": f"no behaviour {claim[k]}"}
    return m.test(claim, split)


def page_view(con):
    """what the atlas page draws: regime boundaries with what rose and fell, and the behaviours that spread most"""
    m = load(con)
    if m is None:
        return {"error": "no behaviour atlas yet"}
    r = m.regimes()
    slim = lambda xs: [{k: x[k] for k in ("id", "before", "after")} for x in xs]
    return {"k": r["k"], "heldout_explained": r["heldout_explained"],
            "boundaries": [{"date": b["date"], "shift": b["shift"], "stable": b["stable_in_both_halves"],
                            "rose": slim(b["rose"][:3]), "fell": slim(b["fell"][:2])} for b in r["boundaries"]],
            "spreads": [{k: x[k] for k in ("id", "rr", "lo", "hi")} for x in m.spread_ranking(limit=5)]}


def card_view(con, f):
    """one behaviour on the page: does it pass between actors, who passed it on, what follows it, does it persist"""
    m = load(con)
    if m is None or f not in m.label:
        return {"error": f"no behaviour {f}"}
    key = ("card", f)
    if key not in m._memo:
        keep = lambda r: {k: r[k] for k in ("rr", "lo", "hi", "exposed", "exposed_with")}
        follows = [m.coupling(f, b) for b in m.fids if b != f]
        follows = sorted((r for r in follows if r["exposed_with"] >= MIN_CASES and r["lo"] and r["lo"] > 1),
                         key=lambda r: -r["lo"])[:3]
        m._memo[key] = {"id": f, "spread": keep(m.transmission(f)), "persists": keep(m.transmission(f, "next")),
                        "by": [{"who": x["actor"], "n": x["passed_on"]} for x in m.spreaders(f, limit=3)],
                        "follows": [{"id": r["b"], "rr": r["rr"], "lo": r["lo"]} for r in follows]}
    return m._memo[key]
