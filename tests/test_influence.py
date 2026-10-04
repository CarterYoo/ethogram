"""Influence: the model finds a planted loop of influence (A -> B -> C -> A) and does not mistake agents who are alike
and often in contact for agents who move each other. Needs numpy and scipy (skipped without them)."""
import math
import os
import random
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from swarmgraph import flow as FL  # noqa: E402

try:
    import numpy  # noqa: F401
    import scipy  # noqa: F401
    from swarmgraph import influence as IN
except ImportError:
    IN = None

T0 = datetime(2026, 3, 2)
LOOP = {("A", "B"): 2.0, ("B", "C"): 2.0, ("C", "A"): 2.0}
BASE = {"A": -2.5, "B": -2.5, "C": -2.5, "H1": 0.5, "H2": 0.5, "L1": -2.5, "L2": -2.5}  # H1, H2: alike, no influence


def record(seed=0, n=9000, days=40):
    rng = random.Random(seed)
    actors = list(BASE)
    units = []
    for i in range(n):
        d = rng.random() * days
        units.append({"_d": d, "actor": rng.choice(actors), "channel": "room", "ids": [f"e{i}"], "uniform": True})
    units.sort(key=lambda u: u["_d"])
    shock = {k: rng.gauss(0, 0.6) for k in range(days + 1)}  # a common cause by day
    edges = set()
    for k, u in enumerate(units):
        u["id"] = k
        u["start"] = (T0 + timedelta(days=u["_d"])).strftime("%Y-%m-%d %H:%M:%S")
        got, j = 0, k - 1
        while j >= 0 and got < 3 and (u["_d"] - units[j]["_d"]) * 24 <= 1:
            if units[j]["actor"] != u["actor"]:
                edges.add((j, k))
                got += 1
            j -= 1
        # H1 and H2 meet each other far more than anyone else
        if u["actor"] in ("H1", "H2"):
            for j in range(k - 1, max(-1, k - 60), -1):
                if units[j]["actor"] in ("H1", "H2") and units[j]["actor"] != u["actor"]:
                    edges.add((j, k))
                    break
    src = {}
    for v, w in edges:
        src.setdefault(w, []).append(v)
    for k, u in enumerate(units):
        eta = BASE[u["actor"]] + shock[int(u["_d"])]
        for v in src.get(k, ()):
            if "X" in units[v]["f"]:
                eta += LOOP.get((units[v]["actor"], u["actor"]), 0.0)
        u["f"] = {"X": 1.0} if rng.random() < 1 / (1 + math.exp(-eta)) else {}
    return units, {"channel": edges}


@unittest.skipIf(IN is None, "needs numpy and scipy")
class Influence(unittest.TestCase):
    def test_a_planted_loop_is_found_and_alike_agents_are_not_influence(self):
        units, edges = record()
        m = FL.Flow(units, edges, [{"id": "X", "text": "does X"}])
        r = IN.one(m, "X", set(BASE), cut="2026-03-28")
        alpha = {(p["from"], p["to"]): p["alpha"] for p in r["pairs"]}
        clear = {(p["from"], p["to"]) for p in r["pairs"] if p["clear"]}
        self.assertGreater(min(alpha.get(k, 0) for k in LOOP), 1.2)  # planted at 2.0
        self.assertEqual(clear, set(LOOP))  # exactly the loop: alike and in contact (H1, H2) is not influence
        self.assertEqual(r["two_way"], [])  # a loop of three, not pairs that move each other
        self.assertEqual({x["actor"] for x in r["returns_to_self"][:3]}, {"A", "B", "C"})  # what goes round comes back
        self.assertGreater(r["heldout"]["gain"], 0)  # the alphas predict the later part
        self.assertTrue(0 < r["R"] < 1)


if __name__ == "__main__":
    unittest.main()
