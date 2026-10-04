"""Behaviour features: what judges return is mapped back to event ids (labels outside a unit are dropped and
counted), agreement and detection are computed from sets, and activations are shares of the events a judge saw."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from swarmgraph import features as F  # noqa: E402


class Marks(unittest.TestCase):
    def test_labels_map_to_events_and_unknown_ones_are_dropped(self):
        d = tempfile.mkdtemp()
        json.dump([{"label": "U1", "unit": 7, "lines": {"e1": ["a", "b"], "e2": ["c"]}},
                   {"label": "U2", "unit": 9, "lines": {"e1": ["d"]}}], open(os.path.join(d, "batch_01.json"), "w"))
        json.dump({"units": [{"unit": "U1", "marks": [{"feature": "F06", "events": ["e1", "e9"]}]},
                             {"unit": "U3", "marks": []}]}, open(os.path.join(d, "out_01.json"), "w"))
        marks, judged, dropped = F.read_marks(d)
        self.assertEqual(marks[7]["F06"], {"a", "b"})  # a folded line stands for all its events
        self.assertEqual(judged, {7, 9})  # a file read counts every unit in it as judged
        self.assertEqual(dropped["unknown event"], 1)
        self.assertEqual(dropped["unknown unit"], 1)

    def test_kappa(self):
        self.assertAlmostEqual(F.kappa({1, 2}, {1, 2}, 10), 1.0)
        self.assertLess(F.kappa({1, 2}, {3, 4}, 10), 0)
        self.assertIsNone(F.kappa(set(), set(), 10))  # nobody marked anything: undefined, not perfect

    def test_detection_scores(self):
        d = tempfile.mkdtemp()
        json.dump([{"label": "D1", "feature": "F07", "units": {"U1": [1, "top"], "U2": [2, "top"], "U3": [3, "near"],
                                                                "U4": [4, "random"]}}],
                  open(os.path.join(d, "batch_01.json"), "w"))
        json.dump({"results": [{"feature": "D1", "fits": ["U1", "U3"]}]}, open(os.path.join(d, "out_01.json"), "w"))
        r = F.read_detection(d)["F07"]
        self.assertEqual((r["precision"], r["recall"], r["coverage"]), (0.5, 0.5, 0.0))

    def test_activation_is_share_of_seen_events(self):
        us = [{"id": 1, "actor": "a", "start": "2026-06-01 10:00:00", "end": "2026-06-01 10:05:00",
               "ids": ["x", "y", "z", "w"]}]
        fs = [{"id": "F01", "anchor": "copies"}, {"id": "F06"}, {"id": "R1", "readers": "readers_concern"}]
        m1 = {"marks": {1: {"F06": {"x"}}}, "units": {1}}
        m2 = {"marks": {1: {"F06": {"x", "y"}}}, "units": {1}}
        shown = {1: {"x", "y"}}  # z and w were not shown to the judges
        act = F.activations(us, fs, [1], [m1, m2], shown, {"copies": {"x", "y", "z"}}, {"readers_concern": {"w"}})
        self.assertEqual(act[1]["F06"], 0.75)  # (1/2 + 2/2) / 2
        self.assertEqual(act[1]["F01"], 0.75)  # code: 3 of the 4 events
        self.assertEqual(act[1]["R1"], 0.25)

    def test_stretches_with_no_behaviour_get_no_map_position(self):
        try:
            import umap  # noqa: F401
        except ImportError:
            self.skipTest("needs umap-learn")
        import random
        random.seed(0)
        fs = [{"id": f"F{k}"} for k in range(6)]
        act = {u: {f"F{k}": round(random.random(), 2) for k in random.sample(range(6), 2)} for u in range(30)}
        act.update({u: {} for u in range(30, 40)})
        prof = {f["id"]: {"units": [1, 2, 3], "rate": [random.random() for _ in range(5)]} for f in fs}
        fpos, upos = F.layout(fs, prof, act)
        self.assertEqual(set(upos), set(range(30)))
        xs = sorted(p[0] for p in upos.values())
        self.assertTrue(all(0 <= x <= 1 for x in xs) and max(b - a for a, b in zip(xs, xs[1:])) <= 0.5)


if __name__ == "__main__":
    unittest.main()
