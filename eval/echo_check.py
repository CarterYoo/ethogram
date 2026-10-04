"""Does a hypothesis hold only because B repeats A? ("after the topic is raised, the topic comes back")

A and B patterns that describe the same kind of event make "B follows A" true by construction: the follow-up is the same
conversation continuing, and the actors' own off-windows (the baseline) hold less of it. This measures, for every
after-then hypothesis the grader formalised, the share of A-events that are also B-events on the events it was graded on,
and tabulates the held-out outcome by that share and by condition.

  python3 eval/echo_check.py GRADED_DIR TEST_INDEX HELD_OUT_IDS_FILE [--cut 0.4] [--arms raw,sg,rawtool,sgfixed]

GRADED_DIR is a `hyp_test.py run` output (tests.json = the formalised specs, results.json = the verdicts). Matching is
the grader's own (patterns searched in "<note summary> || <raw text>").
"""
import collections
import json
import os
import statistics as st
import sys
from math import comb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swarmgraph.claims import Log  # noqa: E402


def fisher(k1, n1, k2, n2):
    tot, n = k1 + k2, n1 + n2
    p = lambda x: comb(n1, x) * comb(n2, tot - x) / comb(n, tot)
    p0 = p(k1)
    return sum(p(x) for x in range(max(0, tot - n2), min(n1, tot) + 1) if p(x) <= p0 + 1e-12)


def overlaps(graded, index, ids_file):
    ids = set(open(ids_file).read().split())
    log = Log(index, only_ids=ids)
    specs = json.load(open(os.path.join(graded, "tests.json")))
    res = json.load(open(os.path.join(graded, "results.json")))
    arm = {h["hid"]: h["arm"] for h in res["hypotheses"]}
    rows = []
    for hid, s in specs.items():
        if not s.get("testable") or s.get("shape") != "after_then":
            continue
        A, B = log.match(s["a_patterns"], "text"), log.match(s["b_patterns"], "text")
        na = sum(A)
        if not na or not sum(B):
            continue
        both = sum(1 for a, b in zip(A, B) if a and b)
        rows.append({"hid": hid, "arm": arm[hid], "a_also_b": both / na, "verdict": res["results"][hid]["verdict"]})
    return rows


def main():
    a = sys.argv[1:]
    cut = float(a[a.index("--cut") + 1]) if "--cut" in a else 0.4
    arms = (a[a.index("--arms") + 1] if "--arms" in a else "raw,sg,rawtool,sgfixed").split(",")
    rows = overlaps(a[0], a[1], a[2])
    by = collections.defaultdict(list)
    for r in rows:
        by[r["arm"]].append(r)
    print(f"{len(rows)} after-then hypotheses with matches\n")
    print(f"{'':9s}{'n':>4s}{'median share of A-events that are also B':>44s}   holds by share (<10% | 10-{int(cut * 100)}% | >={int(cut * 100)}%)")
    for arm in arms:
        v = by[arm]
        bins = [[0, 0], [0, 0], [0, 0]]
        for r in v:
            b = 0 if r["a_also_b"] < 0.10 else (1 if r["a_also_b"] < cut else 2)
            bins[b][1] += 1
            bins[b][0] += r["verdict"] == "holds"
        print(f"{arm:9s}{len(v):4d}{st.median(r['a_also_b'] for r in v):44.2f}   " + " | ".join(f"{h}/{n}" for h, n in bins))
    print(f"\nhypotheses where fewer than {int(cut * 100)}% of the A-events are also B-events")
    out = {}
    for arm in arms:
        sel = [r for r in by[arm] if r["a_also_b"] < cut]
        out[arm] = (sum(r["verdict"] == "holds" for r in sel), len(sel))
        print(f"  {arm:9s} holds {out[arm][0]:2d}/{out[arm][1]:2d} = {out[arm][0] / max(1, out[arm][1]):.0%}")
    for x, y in (("raw", "rawtool"), ("sg", "sgfixed"), ("raw", "sgfixed"), ("rawtool", "sgfixed")):
        if x in out and y in out:
            print(f"  Fisher {x} vs {y}: p = {fisher(*out[x], *out[y]):.3f}")


if __name__ == "__main__":
    main()
