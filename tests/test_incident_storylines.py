"""Incident stories require connected source evidence, independently of atlas coverage."""
import copy
import unittest

from swarmgraph import incident_storylines as I
from swarmgraph.storyline_adapters import MemoryAtlasAdapter


class Client:
    model, effort, sandbox, timeout = "fixture", "medium", "injected", 1

    def __init__(self, value):
        self.value, self.calls = value, []

    def run(self, prompt, schema):
        self.calls.append((prompt, schema))
        return copy.deepcopy(self.value), 0.


class IncidentStories(unittest.TestCase):
    def setUp(self):
        self.events = [
            {"id": "request", "ts": "2031-02-01 09:00:00", "actor": "one", "kind": "message",
             "channel": "room", "text": "Please compute the value."},
            {"id": "response", "ts": "2031-02-02 09:01:00", "actor": "two", "kind": "message",
             "channel": "room", "reply_to": "request", "text": "I used your request and got fourteen."},
            {"id": "unrelated", "ts": "2031-02-02 10:00:00", "actor": "three", "kind": "message",
             "channel": "other", "text": "I am checking a different task."}]
        self.atlas = {"features": [{"id": "ASK", "text": "Requests a value", "source": "judged", "density": 1}],
            "bins": ["2031-02-01", "2031-02-02"], "unit": "day", "n_bin": [1, 0],
            "profiles": {"ASK": {"rate": [1, 0], "units": [1, 0]}}, "coverage": "One reviewed stretch",
            "fpos": {"ASK": [.2, .3]}, "units": [
                {"id": 12, "actor": "one", "start": self.events[0]["ts"], "end": self.events[0]["ts"],
                 "ids": ["request"], "x": .2, "y": .3, "f": {"ASK": 1}}]}
        self.discovery = {"incidents": [{"question": "How was the requested value answered?",
                                        "seeds": ["request", "response"]}]}
        self.plan = {"title": "A request and a reported answer", "summary": "A worker reports answering a request.",
            "stories": [{"title": "A value is requested", "summary": "A request is followed by a reported answer.",
                "question": "How was the requested value answered?", "outcome": "The result is reported, not independently checked.",
                "links": [{"from_event": "request", "to_event": "response", "kind": "reports_use",
                    "basis": "reported", "caption": "The second worker reports using the request.",
                    "support": [{"event": "request", "quote": "Please compute the value."},
                                {"event": "response", "quote": "I used your request and got fourteen."}]}],
                "scenes": [{"title": "A request receives a report", "caption": "A worker reports an answer to the request.",
                    "start": "2031-02-01", "end": "2031-02-02", "features": [],
                    "events": ["request", "response"], "caveat": "The answer has not been independently checked.",
                    "steps": [{"event": "request", "role": "Requester", "action": "Requests a value.", "status": "observed"},
                              {"event": "response", "role": "Responding worker", "action": "Reports an answer.", "status": "reported"}]}]}]}
        self.review = {"accept": True, "repair": "", "stories": [{"id": "story-1", "accept": True, "repair": ""}],
            "scenes": [{"id": "story-1-scene-1", "accept": True, "repair": ""}],
            "links": [{"id": "story-1-link-1", "accept": True, "repair": ""}]}

    def snapshot(self):
        return I.snapshot_from_adapter(MemoryAtlasAdapter(self.atlas, self.events,
            notes="Reply fields are literal recorded replies. Actor labels are not verified identities.",
            reading_leads=[{"event": r["id"], "summary": r["text"], "category": "did"} for r in self.events]))

    def execute(self, review=None):
        return I.execute(self.snapshot(), Client(self.discovery), Client(self.plan), Client(review or self.review))

    def test_unmapped_raw_record_can_complete_an_incident_without_feature_assignment(self):
        artifact = self.execute()
        story = artifact["result"]["stories"][0]
        steps = story["scenes"][0]["steps"]
        self.assertEqual(steps[0]["map_units"], [12])
        self.assertEqual(steps[1]["map_units"], [])
        self.assertEqual(steps[1]["status"], "reported")
        self.assertEqual(steps[1]["when"], self.events[1]["ts"])
        self.assertEqual(story["scenes"][0]["features"], [])
        self.assertEqual(self.atlas["n_bin"], [1, 0])

    def test_disconnected_factual_tour_is_not_a_story(self):
        raw = copy.deepcopy(self.plan)
        raw["stories"][0]["links"] = []
        checked = I.validate_plan(raw, self.snapshot(), self.discovery)
        self.assertFalse(checked["plan"]["stories"])
        self.assertTrue(checked["errors"])

    def test_link_support_requires_exact_quotes_from_both_endpoints(self):
        for support in ([{"event": "request", "quote": "Please compute the value."}],
                        [{"event": "request", "quote": "invented statement"},
                         {"event": "response", "quote": "I used your request and got fourteen."}]):
            raw = copy.deepcopy(self.plan)
            raw["stories"][0]["links"][0]["support"] = support
            self.assertFalse(I.validate_plan(raw, self.snapshot(), self.discovery)["plan"]["stories"])

    def test_rejection_or_missing_link_verdict_drops_dependent_story(self):
        for links in ([], [{"id": "story-1-link-1", "accept": False, "repair": "The uptake is not supported."}]):
            review = copy.deepcopy(self.review); review["links"] = links
            self.assertFalse(self.execute(review)["result"]["stories"])

    def test_replay_uses_frozen_discovery_and_verdicts_and_rejects_tampering(self):
        artifact = self.execute()
        self.assertEqual(I.replay(artifact), artifact["result"])
        changed = copy.deepcopy(artifact)
        changed["calls"]["writer"]["output"]["title"] = "Changed"
        with self.assertRaises(I.PlanError):
            I.replay(changed)
        self.events[1]["text"] = "A different report."
        with self.assertRaises(I.PlanError):
            I.replay(artifact, self.snapshot())

    def test_no_discovered_incident_is_honest_and_skips_later_agents(self):
        writer, reviewer = Client(self.plan), Client(self.review)
        artifact = I.execute(self.snapshot(), Client({"incidents": []}), writer, reviewer)
        self.assertFalse(artifact["result"]["stories"])
        self.assertEqual((writer.calls, reviewer.calls), ([], []))

    def test_three_agents_are_distinct(self):
        one = Client(self.discovery)
        with self.assertRaises(I.PlanError):
            I.execute(self.snapshot(), one, one, Client(self.review))

    def test_raw_events_outside_atlas_dates_remain_eligible(self):
        self.events[0]["ts"] = "2031-01-31 09:00:00"
        self.plan["stories"][0]["scenes"][0]["start"] = "2031-01-31"
        artifact = self.execute()
        steps = artifact["result"]["stories"][0]["scenes"][0]["steps"]
        self.assertEqual(steps[0]["when"], "2031-01-31 09:00:00")
        self.assertEqual(steps[0]["map_units"], [])
        self.assertEqual(artifact["snapshot"]["atlas"]["bins"], ["2031-02-01", "2031-02-02"])

    def test_neighborhood_fetches_a_missing_middle_outside_feature_sample(self):
        self.events.append({"id": "middle", "ts": "2031-02-01 10:00:00", "actor": "four",
            "channel": "room", "kind": "message", "text": "Here is an intermediate calculation."})
        evidence = I.hydrate(self.snapshot(), self.discovery)
        ids = {event["id"] for event in evidence["events"]}
        self.assertIn("middle", ids)
        middle = next(event for event in evidence["events"] if event["id"] == "middle")
        self.assertEqual(middle["map_units"], [])
        self.assertEqual(self.atlas["n_bin"], [1, 0])


if __name__ == "__main__":
    unittest.main()
