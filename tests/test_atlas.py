"""The behaviour map: stretches cut by pauses and length, topic words replaced or dropped, linear erasure leaves no
class mean apart, prediction and resampling tell real kinds from arbitrary ones, and the page carries names and checks
but not the code's feature names. Numeric parts run only where numpy, scipy and scikit-learn are installed."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_sweep as T  # noqa: E402
from swarmgraph import atlas as A, query as Q  # noqa: E402
from swarmgraph.build import build  # noqa: E402
from swarmgraph.store import open_work  # noqa: E402

try:
    import numpy as np
    import scipy  # noqa: F401
    import sklearn  # noqa: F401
    NUMERIC = True
except ImportError:
    NUMERIC = False


def ev(i, ts, actor):
    return {"id": i, "ts": ts, "actor": actor}


class Stretches(unittest.TestCase):
    def test_pause_and_span_end_a_stretch(self):
        rows = [ev("a1", "2026-06-01 10:00:00", "a"), ev("a2", "2026-06-01 10:10:00", "a"),
                ev("b1", "2026-06-01 10:11:00", "b"),
                ev("a3", "2026-06-01 10:40:00", "a"),  # 30 minutes after a2: a new stretch
                ev("b2", "2026-06-01 10:20:00", "b")]
        rows.sort(key=lambda e: e["ts"])
        st = A.stretches(rows)
        self.assertEqual([[e["id"] for e in s["events"]] for s in st], [["a1", "a2"], ["b1", "b2"], ["a3"]])
        long = [ev(f"c{k}", f"2026-06-01 {10 + k // 6:02d}:{(k % 6) * 10:02d}:00", "c") for k in range(13)]
        self.assertEqual([len(s["events"]) for s in A.stretches(long)], [7, 6])  # at most an hour each

    def test_topic_replaced_by_kind(self):
        toks = A.delex("R4 CONFIRMED: Romania 81% at 14:58:02, see https://x.org/a?b=1 -- AgentRelent",
                       names={"AgentRelent"})
        self.assertIn("LINK", toks)
        self.assertIn("TIME", toks)
        self.assertIn("NAME", toks)
        self.assertNotIn("agentrelent", toks)
        self.assertNotIn("81", " ".join(toks))

    def test_words_of_few_channels_or_actors_are_topic(self):
        st = []
        for k in range(60):  # 30 channels and 30 actors, each with two stretches
            st.append({"channel": f"p{k % 30}", "actor": f"x{k // 2}",
                       "words": ["confirmed", "relay"] + (["romania"] if k < 4 else [])})
        voc = A.topic_vocabulary(st)
        self.assertIn("confirmed", voc)
        self.assertNotIn("romania", voc)


@unittest.skipUnless(NUMERIC, "numpy, scipy and scikit-learn not installed")
class Numeric(unittest.TestCase):
    def test_leace_leaves_no_class_mean_apart(self):
        rng = np.random.default_rng(0)
        y = rng.integers(0, 3, 600)
        X = rng.normal(size=(600, 6))
        X[:, 0] += 3 * (y == 1)
        X[:, 2] -= 2 * (y == 2)
        Z = np.eye(3)[y]
        E = A.leace(X, Z)
        means = np.array([E[y == c].mean(0) for c in range(3)])
        self.assertLess(np.abs(means - means.mean(0)).max(), 1e-8)
        self.assertGreater(np.abs(E - X).sum(), 0)

    def test_real_kinds_predict_and_come_back(self):
        from scipy.cluster.hierarchy import fcluster, linkage
        rng = np.random.default_rng(1)
        centres = np.array([[0, 0], [8, 0], [0, 8], [8, 8]], float)
        n, st, P, kind = 0, [], [], []
        for a in range(40):  # each actor keeps to one kind; stretches a day apart
            c = a % 4
            for d in range(6):
                st.append({"actor": f"a{a}", "channel": None, "start": f"2026-06-{1 + d:02d} {a % 24:02d}:00:00"})
                P.append(centres[c] + rng.normal(scale=0.6, size=2))
                kind.append(c)
                n += 1
        order = sorted(range(n), key=lambda i: st[i]["start"])
        st, P, kind = [st[i] for i in order], np.array([P[i] for i in order]), np.array([kind[i] for i in order])
        tr = A.transitions(st)
        day0 = A._ts(st[0]["start"])
        days = [int((A._ts(s["start"]) - day0) // 86400) for s in st]
        true = A.predictive(P, kind, tr["same actor"], days)[0]
        noise = A.predictive(P, rng.integers(0, 4, n), tr["same actor"], days)[0]
        self.assertGreater(true, 0.8)
        self.assertLess(noise, 0.1)
        Z = linkage(P, "ward")
        lab = fcluster(Z, 4, "maxclust")
        idx = [np.sort(rng.choice(n, int(0.8 * n), replace=False)) for _ in range(4)]
        stab, aris = A.stability(lab, 4, [(i, linkage(P[i], "ward")) for i in idx])
        self.assertTrue(all(v > 0.95 for v in stab.values()))
        self.assertGreater(min(aris), 0.95)


class Page(unittest.TestCase):
    def test_page_has_names_and_checks_not_feature_names(self):
        tmp = tempfile.mkdtemp()
        T.dataset(os.path.join(tmp, "ds"))
        db = os.path.join(tmp, "x.sqlite")
        build(os.path.join(tmp, "ds"), db)
        work = open_work(db)
        work.execute("CREATE TABLE IF NOT EXISTS atlas(key TEXT PRIMARY KEY, created TEXT, value TEXT)")
        types = [{"id": 0, "size": 2, "stable": 0.9, "name": "Push the same fix again", "description": "d",
                  "fits_if": "f", "check": {"precision": 1.0, "recall": 0.83, "coverage": 0.0, "share": 0.5,
                                            "checked": {"in": 6, "near": 6, "random": 6},
                                            "picked": {"in": 5, "near": 0, "random": 0}},
                  "revised": False, "apart": ["- ran: git push: 0.90 here, 0.10 overall"]}]
        points = [{"actor": "a1", "start": "2026-03-01T10:00:00Z", "end": "2026-03-01T10:20:00Z", "n": 3,
                   "ids": ["act1-0"], "x": 0.1, "y": 0.2, "type": 0, "unusual": False, "flagged": True}]
        for k, v in (("points", points), ("types", types), ("report", {"stretches": 1})):
            work.execute("INSERT OR REPLACE INTO atlas VALUES (?,?,?)", (k, "now", json.dumps(v)))
        work.commit()
        d = A.page(Q.connect(db))
        self.assertEqual(d["points"][0]["who"], "Agent One")
        self.assertEqual(d["types"][0]["name"], "Push the same fix again")
        self.assertNotIn("git push", json.dumps(d["types"]))  # the code's feature names stay with analysts
        self.assertTrue(d["how"])
        self.assertIn("apart", A.view(Q.connect(db))["types"][0])


if __name__ == "__main__":
    unittest.main()
