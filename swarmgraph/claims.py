"""Claim measurement: does behaviour B follow behaviour A more often than a fair baseline says it should?

One implementation used in two places: by the analyst (tools `test_claim`, `grep`, `context`: check a hypothesis on the
log you have before you report it) and by the grader (eval/hyp_test.py: the same measure on events the analyst did not
see). Patterns are Python regular expressions searched case-insensitively in "<note summary> || <raw text>" (the
summary is empty when the index has no notes).

  after_then  after an A-event, does B follow within the window more often than in equal off-windows?
              scope same_actor: B by the A-actor | target: B by the actors the A-event was aimed at (relations) |
              others_same_place: B by other actors in the same place. The baseline is the same candidates' own
              off-windows (2-3 widths before/after), counted only where they were present AND active, and the window
              after A must also have activity: the question is how often B is done WHEN ACTIVE, so agents that work in
              sessions cannot make unrelated pairs look linked. Cases without a baseline are left out (`unpaired`).
  spreads     A's first uses: do adopters have contact with an earlier adopter more often than chance?
  co_occurs   do actors who do A also do B more than chance?
Verdicts: holds (gap >= 0.10 and >= 1.5x baseline, replicated in both time halves, a cluster bootstrap over actors
whose one-sided 95% lower bound is above zero), not_holds, insufficient (too little data; never a pass).
"""
import bisect
import collections
import hashlib
import math
import os
import random
import re
import sqlite3

from .build import epoch

# ---------------------------------------------------------------------------- the log

class Log:
    def __init__(self, index, since=None, until=None, only_ids=None):
        self.index = index
        con = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
        w, v = "", []
        if since:
            w += " AND ts >= ?"
            v.append(since)
        if until:
            w += " AND ts < ?"
            v.append(until)
        rows = con.execute(f"SELECT id, ts, actor, channel, text FROM events WHERE 1{w} ORDER BY ts", v).fetchall()
        if only_ids is not None:
            rows = [r for r in rows if r[0] in only_ids]
        self.ids = [r[0] for r in rows]
        self.ts = [r[1] for r in rows]
        self.ep = [epoch(r[1]) for r in rows]
        self.actor = [r[2] for r in rows]
        self.channel = [r[3] for r in rows]
        summ = {}
        if os.path.exists(index + ".work"):
            try:
                w = sqlite3.connect(f"file:{index}.work?mode=ro", uri=True)
                summ = dict(w.execute("SELECT event_id, summary FROM tags"))
            except sqlite3.OperationalError:
                pass
        # patterns are searched in the event's one-line note summary (normalised language written by the LLM when it
        # read the event) followed by the raw text, so semantic behaviours and literal strings can both be matched
        self.summary = [summ.get(r[0]) or "" for r in rows]
        self.raw = [r[4] or "" for r in rows]
        self.text = [f"{a} || {b}" for a, b in zip(self.summary, self.raw)]
        self.n = len(rows)
        self.by_actor, self.by_channel = collections.defaultdict(list), collections.defaultdict(list)
        for i in range(self.n):
            self.by_actor[self.actor[i]].append(i)
            if self.channel[i]:
                self.by_channel[self.channel[i]].append(i)
        self.arr_actor = {a: [self.ep[i] for i in ix] for a, ix in self.by_actor.items()}
        self.arr_chan = {c: [self.ep[i] for i in ix] for c, ix in self.by_channel.items()}
        # who each event was aimed at (relations carry the event that shows them)
        pos = {e: i for i, e in enumerate(self.ids)}
        self.targets = collections.defaultdict(set)
        for ev, dst in con.execute("SELECT event_id, dst FROM relations"):
            if ev in pos:
                self.targets[pos[ev]].add(dst)
        self.con = con

    def match(self, patterns, field="text"):
        """one boolean per event; each single pattern is matched once over the log and cached, so mismatched-pair
        calibration and repeated specs cost nothing extra. field: text (summary || raw) | raw | summary"""
        cache = self.__dict__.setdefault("_cache", {})
        src = {"text": self.text, "raw": self.raw, "summary": self.summary}[field]
        arrs = []
        for p in patterns:
            if (field, p) not in cache:
                rx = re.compile(p, re.I | re.S)
                cache[(field, p)] = [bool(rx.search(t)) for t in src]
            arrs.append(cache[(field, p)])
        return [any(col) for col in zip(*arrs)] if arrs else [False] * self.n


# ---------------------------------------------------------------------------- measurements

def _binom_p(pos, n):
    """one-sided: P(X >= pos) for X~Bin(n, 0.5)"""
    return sum(math.comb(n, k) for k in range(pos, n + 1)) / 2 ** n if n else 1.0


def _window(log, B, cands, lo, hi, exclude_actor=None, skip=None):
    """(active, hit): was any candidate active in (lo, hi] (apart from `skip`), and the index of a B-event there."""
    active = False
    for idx, eps in cands:
        i = bisect.bisect_right(eps, lo)
        while i < len(eps) and eps[i] <= hi:
            j = idx[i]
            if j != skip and (exclude_actor is None or log.actor[j] != exclude_actor):
                active = True
                if B[j]:
                    return True, j
            i += 1
    return active, None


def measure_after_then(log, A, B, scope, window_min):
    W = window_min * 60
    a_idx = [i for i in range(log.n) if A[i]]
    rows = []  # (ep, group, after(bool), base_hits, base_n, ids)
    silent = 0
    for i in a_idx:
        actor, ch, t = log.actor[i], log.channel[i], log.ep[i]
        if scope == "target":
            tg = sorted(x for x in log.targets.get(i, ()) if x != actor and x in log.by_actor)  # sets: fix the order
            if not tg:
                continue
            cands = [(log.by_actor[x], log.arr_actor[x]) for x in tg]
            group, exclude = tg[0], None
        elif scope == "others_same_place":
            if not ch:
                continue
            cands = [(log.by_channel[ch], log.arr_chan[ch])]
            group, exclude = actor, actor
        else:
            cands = [(log.by_actor[actor], log.arr_actor[actor])]
            group, exclude = actor, None
        span_lo = min(c[1][0] for c in cands)
        span_hi = max(c[1][-1] for c in cands)
        act, hit = _window(log, B, cands, t, t + W, exclude, skip=i)
        if not act:  # nobody active afterwards: nothing to compare (session ended)
            silent += 1
            continue
        bh = bn = 0
        for k in (-3, -2, 2, 3):  # off-windows, only where the candidates were present AND active
            lo = t + k * W
            if lo < span_lo or lo + W > span_hi:
                continue
            a2, h2 = _window(log, B, cands, lo, lo + W, exclude)
            if a2:
                bn += 1
                bh += h2 is not None
        rows.append((t, group, hit is not None, bh, bn, (log.ids[i], log.ids[hit]) if hit is not None else None))
    unpaired = sum(1 for r in rows if r[4] == 0)  # no off-window where the candidates were active (e.g. one-shot labels)
    rows = [r for r in rows if r[4] > 0]  # compare like with like: only cases that have a baseline of their own
    n = len(rows)
    if not n:
        return {"shape": "after_then", "cases": 0, "unpaired": unpaired, "silent_after": silent}

    def rates(rs):
        c = len(rs)
        after = sum(r[2] for r in rs) / c if c else 0
        bn = sum(r[4] for r in rs)
        base = sum(r[3] for r in rs) / bn if bn else None
        return after, base, c

    rate, base, _ = rates(rows)
    srt = sorted(r[0] for r in rows)
    mid = srt[len(srt) // 2]
    halves = []
    for rs in ([r for r in rows if r[0] < mid], [r for r in rows if r[0] >= mid]):
        a, b, c = rates(rs)
        halves.append({"cases": c, "rate": round(a, 3), "base": None if b is None else round(b, 3),
                       "gap": None if b is None else round(a - b, 3)})
    by_g = collections.defaultdict(lambda: [0, 0, 0, 0])  # group -> [A cases, followed, base hits, base windows]
    for r in rows:
        g = by_g[r[1]]
        g[0] += 1
        g[1] += r[2]
        g[2] += r[3]
        g[3] += r[4]
    gap = None if base is None else rate - base
    lo = None
    if gap is not None and n >= 15 and gap >= 0.05:  # cluster bootstrap over groups (actors), one-sided 95% lower bound
        rnd = random.Random(5)
        gl = list(by_g.values())
        k = len(gl)
        diffs = []
        for _ in range(300):
            pick = [gl[rnd.randrange(k)] for _ in range(k)]
            c, f, bh, bn = (sum(x[i] for x in pick) for i in range(4))
            if c and bn:
                diffs.append(f / c - bh / bn)
        diffs.sort()
        lo = round(diffs[int(0.05 * len(diffs))], 3) if diffs else None
    fol = [r[5] for r in sorted(rows, key=lambda r: r[0]) if r[5]]
    ex = [fol[j] for j in sorted({0, len(fol) // 2, len(fol) - 1})] if fol else []  # first, middle, last: not just the earliest
    return {"shape": "after_then", "cases": n, "unpaired": unpaired, "silent_after": silent, "groups": len(by_g),
            "rate": round(rate, 3),
            "base": None if base is None else round(base, 3), "halves": halves, "gap_ci_low": lo,
            "see": [x for p in ex for x in p]}


def measure_spread(log, A):
    from .metrics import _spread
    first = {}
    for i in range(log.n):
        if A[i] and log.actor[i] not in first:
            first[log.actor[i]] = (log.actor[i], log.con.execute("SELECT ts FROM events WHERE id=?", (log.ids[i],)).fetchone()[0],
                                   log.ids[i])
    lst = sorted(first.values(), key=lambda x: x[1])
    if len(lst) < 8:
        return {"shape": "spreads", "adopters": len(lst)}
    linked, base, pairs = _spread(log.con, lst)
    halves = []
    for part in (lst[:len(lst) // 2], lst[len(lst) // 2:]):
        if len(part) >= 4:
            l2, b2, _ = _spread(log.con, part)
            halves.append({"adopters": len(part), "linked": l2, "base": b2})
    return {"shape": "spreads", "adopters": len(lst), "linked": linked, "base": base, "halves": halves,
            "see": [x for p in pairs[:2] for x in (p["contact_event"], p["first_use"])]}


def measure_co(log, A, B):
    A_act, B_act, actors = set(), set(), set(log.by_actor)
    for i in range(log.n):
        if A[i]:
            A_act.add(log.actor[i])
        if B[i]:
            B_act.add(log.actor[i])
    n = len(actors)
    both = A_act & B_act
    if not A_act or not B_act or not n:
        return {"shape": "co_occurs", "a_actors": len(A_act), "b_actors": len(B_act)}
    share, base = len(both) / len(A_act), len(B_act) / n
    halves = []
    for par in (0, 1):
        sel = lambda s: {a for a in s if int(hashlib.md5(a.encode()).hexdigest(), 16) % 2 == par}
        a_h, b_h, all_h = sel(A_act), sel(B_act), sel(actors)
        halves.append({"a_actors": len(a_h), "share": round(len(a_h & b_h) / len(a_h), 3) if a_h else None,
                       "base": round(len(b_h) / len(all_h), 3) if all_h else None})
    return {"shape": "co_occurs", "a_actors": len(A_act), "b_actors": len(B_act), "both": len(both),
            "share": round(share, 3), "base": round(base, 3), "halves": halves}


# ---------------------------------------------------------------------------- verdict

def verdict(m):
    s = m.get("shape")
    if s == "after_then":
        if m.get("cases", 0) < 15 or m.get("groups", 0) < 4 or m.get("base") is None:
            return "insufficient"
        h = m["halves"]
        if any(x["cases"] < 5 or x["gap"] is None for x in h):
            return "insufficient"
        gap = m["rate"] - m["base"]
        lift_ok = m["rate"] >= 0.10 if m["base"] < 0.01 else m["rate"] / m["base"] >= 1.5
        ok = gap >= 0.10 and lift_ok and all(x["gap"] >= 0.03 for x in h) and (m.get("gap_ci_low") or 0) > 0
        return "holds" if ok else "not_holds"
    if s == "spreads":
        if m.get("adopters", 0) < 8 or m.get("base") in (None, 0) or m.get("linked") is None or len(m.get("halves", [])) < 2:
            return "insufficient"
        ok = m["linked"] / m["base"] >= 1.3 and m["linked"] - m["base"] >= 0.1 and \
            all(x["linked"] is not None and x["base"] is not None and x["linked"] > x["base"] for x in m["halves"])
        return "holds" if ok else "not_holds"
    if s == "co_occurs":
        if m.get("a_actors", 0) < 8 or m.get("b_actors", 0) < 8 or m.get("both", 0) < 5:
            return "insufficient"
        if any(x["a_actors"] < 4 or x["share"] is None or not x["base"] for x in m["halves"]):
            return "insufficient"
        ok = m["share"] / m["base"] >= 1.5 and m["share"] - m["base"] >= 0.1 and \
            all(x["share"] / x["base"] >= 1.2 for x in m["halves"])
        return "holds" if ok else "not_holds"
    return "insufficient"


def run_test(log, spec):
    A = log.match(spec["a_patterns"])
    na = sum(A)
    out = {"a_matches": na}
    if spec["shape"] == "spreads":
        m = measure_spread(log, A)
    else:
        B = log.match(spec["b_patterns"])
        out["b_matches"] = sum(B)
        m = (measure_after_then(log, A, B, spec.get("scope", "same_actor"), spec.get("window_minutes", 90))
             if spec["shape"] == "after_then" else measure_co(log, A, B))
    m.update(out)
    m["verdict"] = verdict(m)
    return m


# ---------------------------------------------------------------------------- tools for the analyst

def _clip(t, n):
    t = " ".join((t or "").split())
    return t if len(t) <= n else t[:n] + " …"


def _example(log, i, n=420):
    return {"id": log.ids[i], "ts": log.ts[i][:16], "actor": log.actor[i], "place": log.channel[i],
            "summary": log.summary[i] or None, "text": _clip(log.raw[i], n)}


def grep(log, patterns, field="text", examples=8, clip=420):
    """Events matching any pattern: the exact count, how widely it recurs (actors, days, by-actor and by-day counts) and
    `examples` spread evenly over time with distinct actors preferred — not the first ones. Answers 'does this recur,
    and across what?' before a claim is built on it."""
    if not patterns:
        raise ValueError("give at least one pattern")
    try:
        m = log.match(patterns, field)
    except re.error as ex:
        raise ValueError(f"bad regular expression: {ex}")
    idx = [i for i in range(log.n) if m[i]]
    out = {"patterns": patterns, "searched": {"text": "summary || raw text", "raw": "raw text", "summary": "note summary"}[field],
           "total": len(idx), "share_of_events": round(len(idx) / max(1, log.n), 4), "events_in_log": log.n}
    if not idx:
        out["advice"] = "no matches: loosen the pattern (stems, alternatives) or search the summary field"
        return out
    days = collections.Counter(log.ts[i][:10] for i in idx)
    acts = collections.Counter(log.actor[i] for i in idx)
    out.update({"actors": len(acts), "days": len(days), "first": log.ts[idx[0]][:16], "last": log.ts[idx[-1]][:16],
                "top_actors": acts.most_common(5), "busiest_days": days.most_common(5),
                "concentration": f"the busiest day holds {days.most_common(1)[0][1] / len(idx):.0%} of the matches, "
                                 f"the busiest actor {acts.most_common(1)[0][1] / len(idx):.0%}"})
    pick, seen_actor = [], set()
    step = max(1, len(idx) // max(1, examples))
    for k in range(0, len(idx), step):  # evenly spaced in time; within each stretch prefer an actor not shown yet
        stretch = idx[k:k + step]
        best = next((i for i in stretch if log.actor[i] not in seen_actor), stretch[0])
        seen_actor.add(log.actor[best])
        pick.append(best)
        if len(pick) >= examples:
            break
    out["examples"] = [_example(log, i, clip) for i in pick]
    if len(idx) < 8:
        out["advice"] = "fewer than 8 matches: too narrow to support a regularity"
    elif len(idx) > 0.3 * log.n:
        out["advice"] = "matches over 30% of events: too broad to mean anything"
    return out


def context(log, event_id, before=4, after=4, scope="actor", clip=300):
    """The events around one event — the same actor's neighbours or the same place's — with summaries."""
    try:
        i = log.ids.index(event_id)
    except ValueError:
        raise ValueError(f"no event {event_id!r} in this log")
    lst = log.by_actor[log.actor[i]] if scope == "actor" else log.by_channel.get(log.channel[i], [i])
    k = lst.index(i)
    rows = lst[max(0, k - before):k + after + 1]
    return {"scope": scope, "event": event_id, "of": log.actor[i] if scope == "actor" else log.channel[i],
            "events": [{**_example(log, j, clip), "this": j == i} for j in rows]}


def test_claim(log, shape, a_patterns, b_patterns=None, scope="same_actor", window_minutes=90):
    """Measure 'after A, B' (or 'A spreads', 'A and B co-occur') on this log with the fair baseline described at the top,
    in plain words, with a few pairs of events to read. The measure is in-sample for you; whoever grades the claim later
    may use events you did not see."""
    if shape not in ("after_then", "spreads", "co_occurs"):
        raise ValueError("shape: after_then | spreads | co_occurs")
    if not a_patterns or (shape != "spreads" and not b_patterns):
        raise ValueError("a_patterns (and b_patterns for after_then / co_occurs) are required")
    try:
        spec = {"shape": shape, "a_patterns": a_patterns, "b_patterns": b_patterns or [], "scope": scope,
                "window_minutes": int(window_minutes)}
        m = run_test(log, spec)
    except re.error as ex:
        raise ValueError(f"bad regular expression: {ex}")
    out = {"verdict": m["verdict"], "a_matches": m.get("a_matches"), "b_matches": m.get("b_matches")}
    why = []
    for k, key in (("A", "a_matches"), ("B", "b_matches")):
        n = m.get(key)
        if n is None or (k == "B" and shape == "spreads"):
            continue
        if n < 8:
            why.append(f"{k} pattern matches only {n} events: too narrow (use grep to widen it)")
        elif n > 0.3 * log.n:
            why.append(f"{k} pattern matches {n} of {log.n} events: too broad to mean anything")
    if shape == "after_then":
        if m.get("cases"):
            gap = None if m.get("base") is None else m["rate"] - m["base"]
            out["reading"] = (
                f"{m['cases']} cases (A-events with a comparable baseline, {m['groups']} actors/groups; {m['unpaired']} left out "
                f"without one, {m['silent_after']} followed by silence). After A, B follows within {window_minutes} min in "
                f"{m['rate']:.0%} of cases, against {m['base']:.0%} in equal windows elsewhere for the same candidates"
                + (f" (gap {gap:+.2f}; 95% lower bound {m['gap_ci_low']:+.2f})" if m.get("gap_ci_low") is not None else
                   (f" (gap {gap:+.2f})" if gap is not None else "")) + ". Halves: "
                + ", ".join(f"{h['cases']} cases gap {h['gap']:+.2f}" if h.get("gap") is not None else f"{h['cases']} cases"
                            for h in m["halves"]) + ".")
            out["measure"] = {k: m.get(k) for k in ("cases", "groups", "rate", "base", "gap_ci_low", "halves", "unpaired")}
        else:
            out["reading"] = "no A-event has a comparable baseline (the actors are active only briefly around it)"
    elif shape == "spreads":
        out["reading"] = (f"{m.get('adopters', 0)} actors used A; {m['linked']:.0%} of adopters had contact with an earlier adopter "
                          f"before their first use vs {m['base']:.0%} by chance" if m.get("linked") is not None and m.get("base") else
                          f"{m.get('adopters', 0)} adopters: too few")
        out["measure"] = {k: m.get(k) for k in ("adopters", "linked", "base", "halves")}
    else:
        out["reading"] = (f"{m.get('both', 0)} actors did both; of the {m.get('a_actors')} who did A, {m.get('share', 0):.0%} also did B "
                          f"vs {m.get('base', 0):.0%} of all actors" if m.get("share") is not None else "too few actors")
        out["measure"] = {k: m.get(k) for k in ("a_actors", "b_actors", "both", "share", "base", "halves")}
    out["bar"] = ("holds = the effect is at least 0.10 and 1.5x the baseline, in both halves, and its 95% lower bound "
                  "(resampling actors) is above zero; halves = earlier/later in time for after_then and spreads, two "
                  "fixed groups of actors for co_occurs; insufficient = too little data, not a pass")
    if why:
        out["pattern_problems"] = why
    ex = []
    for k in range(0, len(m.get("see") or []) - 1, 2):
        try:
            a, b = log.ids.index(m["see"][k]), log.ids.index(m["see"][k + 1])
            ex.append({"A": _example(log, a, 300), "B": _example(log, b, 300)})
        except ValueError:
            pass
    if ex:
        out["pairs_to_read"] = ex
    return out
