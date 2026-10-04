"""Flow: the measures find what is planted in a synthetic record and do not find what is not. A behaviour that rises
for everyone in the same weeks must not look like spread along edges; one that passes along edges must; a change in
the mix of behaviours on a known day must come back as a regime boundary near that day, and a record without one
must stay one regime."""
import os
import random
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from swarmgraph import flow as FL  # noqa: E402

T0 = datetime(2026, 3, 2)  # a Monday


def record(n=2400, days=56, actors=300, seed=0, rate=None, carry=None, edges_per=1):
    """synthetic units over `days`; X present with probability rate(day) or, when a source along the edge shows X,
    with probability carry; every unit has `edges_per` reuse edges from earlier units by other actors"""
    rng = random.Random(seed)
    units = []
    for i in range(n):
        d = rng.random() * days
        units.append({"id": i, "start": (T0 + timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S"), "_d": d,
                      "actor": f"a{rng.randrange(actors)}", "channel": None, "ids": [f"e{i}"], "uniform": True})
    units.sort(key=lambda u: u["_d"])
    for k, u in enumerate(units):
        u["id"] = k
    edges = set()
    for k, u in enumerate(units):
        if k < 10:
            continue
        for _ in range(edges_per):
            j = rng.randrange(max(0, k - 40), k)  # a recent unit
            if units[j]["actor"] != u["actor"]:
                edges.add((j, k))
    src = {}
    for v, w in edges:
        src.setdefault(w, []).append(v)
    for k, u in enumerate(units):
        p = rate(u["_d"]) if rate else 0.1
        if carry is not None and any("X" in units[v]["f"] for v in src.get(k, ())):
            p = carry
        u["f"] = {"X": 1.0} if rng.random() < p else {}
        if rng.random() < 0.3:
            u["f"]["Y"] = 1.0
    return units, {"reuse": edges}


FEATS = [{"id": "X", "text": "does X"}, {"id": "Y", "text": "does Y"}]


class Statistics(unittest.TestCase):
    def test_risk_ratio_one_table(self):
        r = FL.mh_rr([(20, 100, 10, 100)])
        self.assertEqual(r["rr"], 2.0)
        self.assertAlmostEqual(r["lo"], 0.987, places=2)  # se of log rr = sqrt(1/20 - 1/100 + 1/10 - 1/100)
        self.assertAlmostEqual(r["hi"], 4.05, places=1)

    def test_an_empty_cell_does_not_give_a_false_certainty(self):
        r = FL.mh_rr([(9, 9, 482, 502)])  # all 9 exposed show it, as do 96% of the rest: no evidence of a lift
        self.assertLess(r["lo"], 1.0)
        self.assertEqual((r["exposed"], r["exposed_with"]), (9, 9))  # counts reported as observed

    def test_wilson(self):
        lo, hi = FL.wilson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertGreater(hi, 0.2)


class Spread(unittest.TestCase):
    def test_a_trend_shared_by_everyone_is_not_spread(self):
        units, edges = record(rate=lambda d: 0.05 + 0.4 * d / 56)  # rises over time, edges play no part
        m = FL.Flow(units, edges, FEATS)
        r = m.transmission("X", "reuse")
        self.assertLessEqual(r["lo"], 1.0)  # weeks are strata: the shared trend cancels
        self.assertGreaterEqual(r["hi"], 1.0)
        pooled = [0, 0, 0, 0]  # the same counts without strata would call it spread
        for u, srcs in m._targets("reuse"):
            hit = "X" in m.has[u]
            k = 0 if any("X" in m.has[v] for v in srcs) else 2
            pooled[k] += hit
            pooled[k + 1] += 1
        self.assertGreater(FL.mh_rr([pooled])["lo"], 1.0)

    def test_a_behaviour_passed_along_edges_is_spread(self):
        units, edges = record(carry=0.6)
        m = FL.Flow(units, edges, FEATS)
        r = m.transmission("X", "reuse")
        self.assertGreater(r["lo"], 2.0)
        self.assertGreater(r["o_e"], 2.0)
        self.assertLessEqual(m.transmission("Y", "reuse")["lo"], 1.0)  # Y does not travel
        self.assertEqual(m.spread_ranking("reuse")[0]["behaviour"], "X (does X)")

    def test_held_out_verdicts(self):
        units, edges = record(carry=0.6)
        m = FL.Flow(units, edges, FEATS)
        r = m.test({"kind": "transmission", "feature": "X", "edge": "reuse"}, {"by": "time", "at": "2026-04-06"})
        self.assertEqual(r["verdict"], "holds")
        r = m.test({"kind": "transmission", "feature": "Y", "edge": "reuse"}, {"by": "actors"})
        self.assertNotEqual(r["verdict"], "holds")

    def test_the_flow_sample_is_a_target_but_not_a_rate(self):
        units, edges = record(carry=0.6)
        for u in units[::2]:  # half the units judged because of their edges, not drawn at random
            u["uniform"], u["target"] = False, True
        m = FL.Flow(units, edges, FEATS)
        self.assertEqual(len(m.tgt), len(units))
        self.assertEqual(len(m.uni), len(units) - len(units[::2]))
        self.assertEqual(sum(g["n"] for g in m.regimes()["regimes"]), len(m.uni))  # regimes: the uniform sample only
        self.assertEqual(m.coverage()["flow_sample"], len(units[::2]))

    def test_cascade_follows_the_chain(self):
        units = [{"id": i, "start": f"2026-03-02 10:0{i}:00", "actor": f"a{i}", "channel": None, "ids": [f"e{i}"],
                  "f": {"X": 1.0}, "uniform": True} for i in range(4)]
        units.append({"id": 9, "start": "2026-03-02 10:09:00", "actor": "z", "channel": None, "ids": ["e9"], "f": {},
                      "uniform": True})
        m = FL.Flow(units, {"reuse": {(0, 1), (1, 2), (2, 3), (3, 9)}}, FEATS)
        c = m.cascades("X")
        self.assertEqual(c["cascades"], 1)
        top = c["largest"][0]
        self.assertEqual((top["units"], top["actors"], top["depth"]), (4, 4, 4))
        self.assertEqual(top["order"], ["a0", "a1", "a2", "a3"])


class Regimes(unittest.TestCase):
    def test_a_change_of_mix_is_found_near_its_day(self):
        units, edges = record(n=1500, days=30, rate=lambda d: 0.1 if d < 15 else 0.6)
        r = FL.Flow(units, edges, FEATS).regimes()
        self.assertEqual(r["k"], 2)
        day = (datetime.fromisoformat(r["boundaries"][0]["date"]) - T0).days
        self.assertLessEqual(abs(day - 15), 1)
        self.assertEqual(r["regimes"][1]["defining"][0]["behaviour"], "X (does X)")

    def test_no_change_stays_one_regime(self):
        units, edges = record(n=1500, days=30, rate=lambda d: 0.3, seed=1)
        self.assertEqual(FL.Flow(units, edges, FEATS).regimes()["k"], 1)


if __name__ == "__main__":
    unittest.main()
