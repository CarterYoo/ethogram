"""Given a match set too large to read and room for N events, which N should be shown?

Measured failure (RESULTS section 9): over 12 runs each, analysts saw 0.8 of the 68 July messages in which an agent
retracts or corrects an earlier claim - exactly the chance rate for the ~223 events a run looks at (223 x 68/21406 =
0.7). The analysts' own regular expressions already matched 66 of those 68, and our `grep` showed "examples spread
evenly over time with distinct actors": both sample REPRESENTATIVELY, and a class that is 0.3% of the log is then
invisible by construction. An investigation needs the opposite: the unlike-the-others first.

This compares code-only rankings on recall of two reference classes the ranking knows nothing about:

  corrections  an agent says an earlier claim of its own (or of a peer) was wrong, retracted or unverified
  concerns     events a labeller marked concerning (wiki hand labels, eval/labels_wiki2.json)

  python3 eval/surfacing.py INDEX [--n 10] [--class corrections|labels] [--labels eval/labels_wiki2.json]

Rankings: even (what we ship today), random, rare_terms (unusual vocabulary for this set), actor_novel (unlike the
actor's own other events), reversal (generic "takes something back" cue words, no class vocabulary), and combinations.
"""
import collections
import json
import math
import os
import random
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_corrections import PATTERN as CORRECTION  # noqa: E402

WORD = re.compile(r"[a-z][a-z'\-]{2,}")
# generic "an earlier statement is being taken back or qualified" cues: tense + stance, no class vocabulary
REVERSAL = re.compile(r"\b(earlier|previous|previously|before|yesterday|above|last (?:message|update|report))\b"
                      r"[^.!?]{0,80}\b(wrong|incorrect|inaccurate|not|never|mistake|error|actually|unverified)\b"
                      r"|\b(actually|in fact|turns out|turned out|to be clear|correction|apolog)\w*", re.I)


def tokens(t):
    return WORD.findall((t or "").lower())


def rank_even(ev, n):
    """what `grep` ships: spread evenly over time, one per actor while possible"""
    out, seen = [], set()
    step = max(1, len(ev) // n)
    for i in range(0, len(ev), step):
        e = ev[i]
        if e[2] not in seen or len(out) + (len(ev) - i) // step < n:
            out.append(e)
            seen.add(e[2])
        if len(out) >= n:
            break
    return out


def rank_random(ev, n, seed=0):
    return random.Random(seed).sample(ev, min(n, len(ev)))


def _df(ev):
    df = collections.Counter()
    for e in ev:
        df.update(set(tokens(e[3])))
    return df


def rank_rare_terms(ev, n):
    """events whose words are unusual inside this match set (length-normalised, long texts damped)"""
    df, N = _df(ev), len(ev)
    out = []
    for e in ev:
        ts = set(tokens(e[3]))
        if not ts:
            continue
        s = sum(math.log(N / df[w]) for w in ts) / math.sqrt(len(ts))
        out.append((s / (1 + math.log(1 + len(ts) / 60)), e))
    out.sort(key=lambda x: -x[0])
    return [e for _, e in out[:n]]


def rank_actor_novel(ev, n):
    """events unlike the same actor's other events in the set (its own words as the baseline)"""
    by = collections.defaultdict(collections.Counter)
    cnt = collections.Counter()
    for e in ev:
        by[e[2]].update(set(tokens(e[3])))
        cnt[e[2]] += 1
    out = []
    for e in ev:
        ts = set(tokens(e[3]))
        if not ts or cnt[e[2]] < 3:
            continue
        own = by[e[2]]
        s = sum(1 for w in ts if own[w] <= 1) / math.sqrt(len(ts))
        out.append((s, e))
    out.sort(key=lambda x: -x[0])
    return [e for _, e in out[:n]]


def rank_reversal(ev, n):
    """generic cue that an earlier statement is being taken back, then rarest first among those"""
    hits = [e for e in ev if REVERSAL.search(e[3] or "")]
    return rank_rare_terms(hits, n) if hits else []


def rank_mixed(ev, n):
    """a budget split over the shapes an investigation needs: unlike-the-set, unlike-the-actor, takes-back, spread"""
    picks, seen = [], set()
    for f, share in ((rank_reversal, 0.4), (rank_rare_terms, 0.2), (rank_actor_novel, 0.2), (rank_even, 0.2)):
        for e in f(ev, max(1, round(n * share)) + 2):
            if e[0] not in seen:
                picks.append(e)
                seen.add(e[0])
            if len(picks) >= n:
                return picks
    return picks[:n]


RANKINGS = {"even (shipped)": rank_even, "random": rank_random, "rare_terms": rank_rare_terms,
            "actor_novel": rank_actor_novel, "reversal": rank_reversal, "mixed": rank_mixed}


def main():
    a = sys.argv[1:]
    opt = lambda k, d: type(d)(a[a.index(k) + 1]) if k in a else d
    n, cls = opt("--n", 10), opt("--class", "corrections")
    con = sqlite3.connect(f"file:{a[0]}?mode=ro", uri=True)
    ev = [(i, ts, ac, tx) for i, ts, ac, tx in
          con.execute("SELECT id, ts, actor, text FROM events WHERE kind='message' ORDER BY ts")]
    if cls == "corrections":
        ref = {e[0] for e in ev if CORRECTION.search(e[3] or "")}
        what = "messages retracting or correcting an earlier claim"
    else:
        lab = json.load(open(opt("--labels", "eval/labels_wiki2.json")))
        ref = {k for k, v in lab.items() if v.get("concern")}
        ev = [e for e in ev if e[0] in lab]  # only the labelled sample can be scored for this class
        what = f"labelled concerning events (of {len(lab)} labelled)"
    print(f"{len(ev):,} messages; reference class: {len(ref)} {what} = {len(ref) / max(1, len(ev)):.2%} of the set")
    print(f"showing {n}; chance = {n * len(ref) / max(1, len(ev)):.2f}\n")
    print(f"{'ranking':18s}{'reference events in the top ' + str(n):>32s}{'lift over chance':>18s}")
    chance = n * len(ref) / max(1, len(ev))
    for name, f in RANKINGS.items():
        got = len([e for e in f(ev, n) if e[0] in ref])
        print(f"{name:18s}{got:>32d}{(got / chance if chance else 0):>17.0f}x")
    print("\nwhat `mixed` puts in front (first 6):")
    for e in rank_mixed(ev, n)[:6]:
        mark = "REF " if e[0] in ref else "    "
        print(f"  {mark}{e[1][:16]} {re.sub(chr(10), ' ', (e[3] or ''))[:150]}")


if __name__ == "__main__":
    main()
