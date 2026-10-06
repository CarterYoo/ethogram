"""The narrated-tour harness validates evidence and can be replayed without an LLM."""
import copy
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from swarmgraph import feature_storylines as FS, features, query  # noqa: E402
from swarmgraph.store import open_work  # noqa: E402
from swarmgraph.storyline_adapters import MemoryAtlasAdapter  # noqa: E402


class Client:
    model, effort, sandbox, timeout = "test-model", "medium", "read-only", 10

    def __init__(self, output):
        self.output, self.calls = output, []

    def run(self, prompt, schema):
        self.calls.append((prompt, schema))
        return copy.deepcopy(self.output), 0.1


def scene(start="2024-02-01", end="2024-02-01", fid="B1", event="evt-a"):
    return {"title": "Work begins", "caption": "Agents reported their progress.",
            "start": start, "end": end, "features": [fid], "events": [event],
            "caveat": "These are observations from reviewed stretches."}


def plan(scenes=None):
    return {"title": "Observed work", "summary": "The reviewed work shows reports and requests.",
            "stories": [{"title": "Reports and requests", "summary": "The actors shared updates.",
                         "scenes": scenes or [scene(), scene("2024-02-03", "2024-02-03", "B2", "evt-c")]}]}


def review(accept=True, rejected=()):
    return {"accept": accept, "repair": "" if accept else "Distinguish reports from demonstrated results.",
            "stories": [{"id": "story-1", "accept": True, "repair": ""}],
            "scenes": [{"id": f"story-1-scene-{i}", "accept": i not in rejected,
                        "repair": "" if i not in rejected else "This claim needs direct evidence."}
                       for i in (1, 2)]}


class FeatureStorylines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "index.sqlite")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE events(id TEXT PRIMARY KEY, ts TEXT, actor TEXT, kind TEXT, text TEXT)")
        con.executemany("INSERT INTO events VALUES (?,?,?,?,?)", [
            ("evt-a", "2024-02-01 10:00:00", "actor-a", "message", "I am reporting current progress."),
            ("evt-b", "2024-02-01 11:00:00", "actor-b", "message", "Please confirm the current status."),
            ("evt-c", "2024-02-03 10:00:00", "actor-b", "message", "Please relay an update.")])
        con.commit(); con.close()
        self.atlas = {"features": [{"id": "B1", "text": "Reports progress", "source": "judged", "density": .5},
                                    {"id": "B2", "text": "Requests updates", "source": "judged", "density": 1.}],
            "bins": ["2024-02-01", "2024-02-02", "2024-02-03"], "unit": "day", "n_bin": [2, 0, 1],
            "profiles": {"B1": {"rate": [.5, 0., 0.], "units": [1, 0, 0], "actors": [1, 0, 0]},
                         "B2": {"rate": [.5, 0., 1.], "units": [1, 0, 1], "actors": [1, 0, 1]}},
            "fpos": {"B1": [.1, .2], "B2": [.8, .7]}, "coverage": "3 reviewed stretches",
            "units": [{"id": 70, "actor": "actor-a", "start": "2024-02-01 10:00:00", "end": "2024-02-01 10:00:00",
                       "ids": ["evt-a"], "n": 1, "x": .1, "y": .2, "f": {"B1": 1.}},
                      {"id": 80, "actor": "actor-b", "start": "2024-02-01 11:00:00", "end": "2024-02-01 11:00:00",
                       "ids": ["evt-b"], "n": 1, "x": .8, "y": .7, "f": {"B2": 1.}},
                      {"id": 90, "actor": "actor-b", "start": "2024-02-03 10:00:00", "end": "2024-02-03 10:00:00",
                       "ids": ["evt-c"], "n": 1, "x": .6, "y": .5, "f": {"B2": 1.}}]}
        self.save_atlas()

    def save_atlas(self):
        work = open_work(self.db)
        features.save(work, "atlas", self.atlas)
        work.close()

    def snapshot(self):
        con = query.connect(self.db)
        try:
            return FS.build_snapshot(con)
        finally:
            con.close()

    def generated(self, rejection=()):
        w, r = Client(plan()), Client(review(rejected=rejection))
        result = FS.run(self.db, writer=w, reviewer=r, log=lambda _: None)
        return result, w, r

    def test_snapshot_is_dataset_agnostic_bounded_and_preserves_missing_samples(self):
        s = self.snapshot()
        self.assertEqual(s["evidence"]["bins"], self.atlas["bins"])
        self.assertEqual(s["evidence"]["days"][1], [None, 0])
        self.assertEqual(s["evidence"]["prior_story"], {"overview": [], "storylines": [], "uncertain": ""})
        self.assertLess(len(FS._json(s["evidence"])), FS.EVIDENCE_CHARS)
        self.assertEqual(s["fingerprint"], self.snapshot()["fingerprint"])
        source = next(e for e in s["evidence"]["sources"] if e["id"] == "evt-a")
        self.assertEqual(source["tagged_stretches"], [{"unit": 70, "bin": "2024-02-01", "features": ["B1"]}])

    def test_reversed_unknown_dates_and_inactive_features_are_rejected(self):
        cases = [dict(start="2024-02-02", end="2024-02-01"),
                 dict(start="2024-02-01 00:00:00"), dict(features=["made-up"]),
                 dict(events=["70"]), dict(events=[70]), dict(events=["not-an-event"])]
        for fields in cases:
            with self.subTest(fields=fields):
                c = scene(); c.update(fields)
                v = FS.validate_plan(plan([c]), self.snapshot())
                self.assertFalse(v["plan"]["stories"])
                self.assertTrue(v["errors"])

    def test_each_highlighted_feature_needs_tagged_sources_inside_span(self):
        c = scene(); c["features"] = ["B1", "B2"]
        self.assertFalse(FS.validate_plan(plan([c]), self.snapshot())["plan"]["stories"])
        c["events"] = ["evt-a", "evt-b"]
        self.assertEqual(len(FS.validate_plan(plan([c]), self.snapshot())["plan"]["stories"]), 1)
        c["events"] = ["evt-a", "evt-c"]
        self.assertFalse(FS.validate_plan(plan([c]), self.snapshot())["plan"]["stories"])

    def test_scene_ids_are_code_assigned_and_scene_order_is_checked(self):
        raw = plan(); raw["stories"][0]["id"] = "pretend-story"
        raw["stories"][0]["scenes"][0]["id"] = "pretend-scene"
        result = FS.validate_plan(raw, self.snapshot())
        self.assertEqual(result["plan"]["stories"][0]["id"], "story-1")
        self.assertEqual(result["plan"]["stories"][0]["scenes"][0]["id"], "story-1-scene-1")
        raw["stories"][0]["scenes"].reverse()
        result = FS.validate_plan(raw, self.snapshot())
        self.assertTrue(any("chronological" in e["reason"] for e in result["errors"]))

    def test_false_event_unit_identity_cannot_supply_feature_support(self):
        self.atlas["units"][0]["actor"] = "somebody-else"
        self.save_atlas()
        src = next(e for e in self.snapshot()["evidence"]["sources"] if e["id"] == "evt-a")
        self.assertEqual(src["tagged_stretches"], [])
        self.assertFalse(FS.validate_plan(plan([scene()]), self.snapshot())["plan"]["stories"])

    def test_missing_page_is_read_only_and_does_not_call_an_agent(self):
        con = query.connect(self.db)
        self.addCleanup(con.close)
        before = con.total_changes
        with patch.object(FS, "Codex", side_effect=AssertionError("GET must not generate")):
            self.assertEqual(FS.page(con)["status"], "missing")
        self.assertEqual(con.total_changes, before)

    def test_grounded_run_is_cached_and_audit_contains_exact_stage_inputs(self):
        result, w, r = self.generated()
        self.assertEqual((result["accepted"], result["rejected"], result["status"]), (2, 0, "ready"))
        self.assertEqual((len(w.calls), len(r.calls)), (1, 1))
        cached = FS.run(self.db, writer=w, reviewer=r, log=lambda _: None)
        self.assertTrue(cached["cached"])
        self.assertEqual((len(w.calls), len(r.calls)), (1, 1))
        con = query.connect(self.db)
        self.addCleanup(con.close)
        artifact = FS.latest_run(con)
        self.assertEqual(artifact["calls"]["writer"]["prompt"], w.calls[0][0])
        self.assertEqual(artifact["calls"]["reviewer"]["output"], r.output)
        self.assertIn("snapshot", artifact)
        self.assertEqual(FS.page(con)["status"], "ready")
        self.assertEqual(con.execute("SELECT count(*) FROM w.feature_storyline_runs").fetchone()[0], 1)

    def test_reviewer_rejection_omits_scenes_and_missing_decisions_fail_closed(self):
        result, _, _ = self.generated(rejection=(2,))
        self.assertEqual((result["accepted"], result["rejected"]), (1, 1))
        con = query.connect(self.db)
        self.addCleanup(con.close)
        self.assertEqual(len(FS.page(con)["stories"][0]["scenes"]), 1)
        rejected = Client(review(accept=False))
        a = FS.execute(self.snapshot(), Client(plan()), rejected)
        self.assertFalse(a["result"]["stories"])
        missing = review(); missing["scenes"] = []; missing["stories"] = []
        a = FS.execute(self.snapshot(), Client(plan()), Client(missing))
        self.assertFalse(a["result"]["stories"])
        self.assertTrue(a["review_errors"])

    def test_atlas_source_or_prompt_changes_withhold_the_stored_tour(self):
        self.generated()
        self.atlas["profiles"]["B1"]["rate"][0] = .4
        self.save_atlas()
        con = query.connect(self.db)
        self.assertEqual(FS.page(con)["status"], "stale"); con.close()
        self.generated()
        con = sqlite3.connect(self.db)
        con.execute("UPDATE events SET text='A different reported observation.' WHERE id='evt-a'")
        con.commit(); con.close()
        con = query.connect(self.db)
        self.assertEqual(FS.page(con)["status"], "stale"); con.close()
        self.generated()
        with patch.object(FS, "WRITER_PROMPT", FS.WRITER_PROMPT + "A changed protocol."):
            con = query.connect(self.db)
            self.assertEqual(FS.page(con)["status"], "stale"); con.close()

    def test_changed_model_config_regenerates_and_force_bypasses_run_cache(self):
        self.generated()
        w, r = Client(plan()), Client(review())
        w.model = "another-test-model"
        FS.run(self.db, writer=w, reviewer=r, log=lambda _: None)
        self.assertEqual(len(w.calls), 1)
        FS.run(self.db, writer=w, reviewer=r, force=True, log=lambda _: None)
        self.assertEqual(len(w.calls), 2)

    def test_exported_replay_rejects_tampering_and_different_snapshot_without_llm(self):
        self.generated()
        con = query.connect(self.db)
        artifact = FS.latest_run(con); con.close()
        with patch.object(FS, "Codex", side_effect=AssertionError("replay must not generate")):
            self.assertEqual(len(FS.replay(artifact)["stories"]), 1)
            self.assertEqual(FS.save_replay(self.db, artifact)["accepted"], 2)
        bad = copy.deepcopy(artifact); bad["calls"]["writer"]["output"]["title"] = "Tampered"
        with self.assertRaises(FS.PlanError):
            FS.replay(bad)
        bad = copy.deepcopy(artifact); bad["snapshot"]["evidence"]["coverage"] = "changed"
        bad["audit_hash"] = FS._hash({k:v for k,v in bad.items() if k != "audit_hash"})
        with self.assertRaises(FS.PlanError):
            FS.replay(bad)
        self.atlas["coverage"] = "changed coverage"; self.save_atlas()
        with self.assertRaises(FS.PlanError):
            FS.replay(artifact, self.snapshot())
        with self.assertRaises(FS.PlanError):
            FS.save_replay(self.db, artifact)

    def test_shared_stage_instance_is_rejected(self):
        client = Client(plan())
        with self.assertRaises(FS.PlanError):
            FS.execute(self.snapshot(), client, client)

    def test_cli_exports_exact_input_and_replays_audit_without_agents(self):
        from swarmgraph import cli
        self.generated()
        snapshot_path = os.path.join(self.tmp.name, "input.json")
        run_path = os.path.join(self.tmp.name, "run.json")
        with patch.object(FS, "Codex", side_effect=AssertionError("export/replay must not generate")), \
                patch.object(cli, "install_signal_handlers"), contextlib.redirect_stdout(io.StringIO()):
            cli.main(["--db", self.db, "feature-stories", "--overview", "--export-input", snapshot_path])
            cli.main(["--db", self.db, "feature-stories", "--overview", "--export-run", run_path])
            cli.main(["--db", self.db, "feature-stories", "--overview", "--replay", run_path])
        with open(snapshot_path) as file:
            self.assertEqual(json.load(file), self.snapshot())
        with open(run_path) as file:
            self.assertEqual(FS.replay(json.load(file))["title"], "Observed work")

    def test_storage_adapter_can_be_replaced_and_hourly_dataset_uses_same_stages(self):
        con = query.connect(self.db)
        events = [dict(row) for row in con.execute("SELECT * FROM events")]
        con.close()
        memory = MemoryAtlasAdapter(self.atlas, events)
        self.assertEqual(FS.snapshot_from_adapter(memory), self.snapshot())
        atlas = copy.deepcopy(self.atlas)
        atlas["unit"] = "hour"
        atlas["bins"] = ["2031-01-05T08", "2031-01-05T09", "2031-01-05T10"]
        feature_map = {"B1": "REPORT", "B2": "ASK"}
        event_map = {"evt-a": "log-101", "evt-b": "log-102", "evt-c": "log-103"}
        stamps = ["2031-01-05 08:05:00", "2031-01-05 08:20:00", "2031-01-05 10:05:00"]
        for row, stamp in zip(events, stamps):
            row["ts"], row["id"] = stamp, event_map[row["id"]]
        for u, stamp in zip(atlas["units"], stamps):
            u["start"] = u["end"] = stamp
            u["ids"] = [event_map[eid] for eid in u["ids"]]
            u["f"] = {feature_map[fid]: v for fid, v in u["f"].items()}
        for f in atlas["features"]:
            f["id"] = feature_map[f["id"]]
        for key in ("profiles", "fpos"):
            atlas[key] = {feature_map[fid]: v for fid, v in atlas[key].items()}
        snapshot = FS.snapshot_from_adapter(MemoryAtlasAdapter(atlas, events))
        raw = plan([scene(atlas["bins"][0], atlas["bins"][0], "REPORT", "log-101"),
                    scene(atlas["bins"][2], atlas["bins"][2], "ASK", "log-103")])
        artifact = FS.execute(snapshot, Client(raw), Client(review()))
        self.assertEqual(artifact["counts"]["accepted"], 2)
        self.assertEqual(FS.replay(artifact), artifact["result"])
        self.assertNotEqual(snapshot["fingerprint"], self.snapshot()["fingerprint"])


if __name__ == "__main__":
    unittest.main()

