"""The hypothesis-measurement code must pass real A→B structure and must not pass unrelated A and B."""
import json
import os
import random
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

import hyp_test as HT  # noqa: E402
from swarmgraph.build import build  # noqa: E402
from swarmgraph import query as Q  # noqa: E402


def make_log(kind, seed=1):
    """30 actors over 10 days. Each posts 'ping' events at random times. kind='real': a 'ack' by the same actor follows
    80% of pings within 20 min and acks occur nowhere else; kind='null': acks at independent random times."""
    rnd = random.Random(seed)
    tmp = tempfile.mkdtemp()
    ev, n = [], 0
    for a in range(30):
        name = f"agent{a}"
        # background chatter keeps every actor 'active' across the whole span
        for t in range(0, 10 * 24 * 60, 120):
            n += 1
            ev.append({"id": f"c{n}", "ts": 1767225600 + t * 60 + rnd.randint(0, 600), "actor": name, "text": "working on things", "channel": f"room{a % 3}"})
        for _ in range(30):
            t = rnd.randint(0, 10 * 24 * 60 - 60)
            n += 1
            ev.append({"id": f"p{n}", "ts": 1767225600 + t * 60, "actor": name, "text": "ping the others", "channel": f"room{a % 3}"})
            if kind == "real" and rnd.random() < 0.8:
                n += 1
                ev.append({"id": f"k{n}", "ts": 1767225600 + (t + rnd.randint(2, 20)) * 60, "actor": name, "text": "ack received", "channel": f"room{a % 3}"})
        if kind == "null":
            for _ in range(24):
                n += 1
                ev.append({"id": f"k{n}", "ts": 1767225600 + rnd.randint(0, 10 * 24 * 60 - 60) * 60, "actor": name, "text": "ack received", "channel": f"room{a % 3}"})
    import datetime
    d = os.path.join(tmp, "d")
    os.makedirs(d)
    with open(os.path.join(d, "events.jsonl"), "w") as f:
        for e in ev:
            e = dict(e)
            e["ts"] = datetime.datetime.utcfromtimestamp(e["ts"]).strftime("%Y-%m-%dT%H:%M:%SZ")
            f.write(json.dumps(e) + "\n")
    db = os.path.join(tmp, "i.sqlite")
    build(d, db)
    return HT.Log(db)


def make_session_log(seed=3):
    """Agents work only in a 4-hour session each day (nothing at night). 'ping' and 'ack' events are placed
    independently at random inside the sessions — no relation between them. A baseline that looks at windows hours
    away from a ping lands outside the session and finds no acks, which makes 'ping → ack' look real; conditioning
    both windows on activity must not."""
    import datetime
    rnd = random.Random(seed)
    tmp = tempfile.mkdtemp()
    ev, n = [], 0
    for a in range(30):
        for day in range(12):
            base = 1767225600 + day * 86400 + 9 * 3600
            for _ in range(24):  # chatter all through the session
                n += 1
                ev.append((base + rnd.randint(0, 4 * 3600), f"c{n}", f"agent{a}", "working on things"))
            for _ in range(4):
                n += 1
                ev.append((base + rnd.randint(0, 4 * 3600), f"p{n}", f"agent{a}", "ping the others"))
            for _ in range(4):
                n += 1
                ev.append((base + rnd.randint(0, 4 * 3600), f"k{n}", f"agent{a}", "ack received"))
    d = os.path.join(tmp, "d")
    os.makedirs(d)
    with open(os.path.join(d, "events.jsonl"), "w") as f:
        for ts, i, actor, text in ev:
            f.write(json.dumps({"id": i, "ts": datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "actor": actor, "text": text, "channel": "room"}) + "\n")
    db = os.path.join(tmp, "i.sqlite")
    build(d, db)
    return HT.Log(db)


class SessionConfound(unittest.TestCase):
    def test_independent_events_in_sessions_do_not_pass(self):
        log = make_session_log()
        m = HT.run_test(log, {"shape": "after_then", "a_patterns": ["ping"], "b_patterns": ["ack"],
                              "scope": "same_actor", "window_minutes": 60})
        self.assertNotEqual(m["verdict"], "holds", m)


class SameInputSameVerdict(unittest.TestCase):
    """The same log and spec must give the same numbers in every process. The target scope groups a case under the
    first of its addressees; taking that from a set made the groups (and the bootstrap bound) depend on the process's
    string-hash seed, and two borderline hypotheses changed verdict between two gradings of identical input."""

    SCRIPT = r"""
import json, random, sys, collections
sys.path.insert(0, sys.argv[1])
from swarmgraph.claims import measure_after_then
rnd = random.Random(4)
names = [f"agent{i}" for i in range(14)]
ev = []  # (ep, actor, kind, targets)
for a in names:
    for k in range(40):
        t = 1767225600 + rnd.randint(0, 6 * 86400)
        others = rnd.sample([x for x in names if x != a], 2)
        ev.append((t, a, "A", others))
        for o in others:
            if rnd.random() < 0.6:
                ev.append((t + rnd.randint(60, 1500), o, "B", []))
    for k in range(120):
        ev.append((1767225600 + rnd.randint(0, 6 * 86400), a, "x", []))
ev.sort(key=lambda e: (e[0], e[1], e[2]))
class L: pass
log = L()
log.n = len(ev)
log.ids = [f"e{i}" for i in range(log.n)]
log.ep = [e[0] for e in ev]
log.actor = [e[1] for e in ev]
log.channel = ["" for _ in ev]
log.targets = collections.defaultdict(set)
for i, e in enumerate(ev):
    for o in e[3]:
        log.targets[i].add(o)
log.by_actor = collections.defaultdict(list)
for i in range(log.n):
    log.by_actor[log.actor[i]].append(i)
log.arr_actor = {a: [log.ep[i] for i in ix] for a, ix in log.by_actor.items()}
log.by_channel, log.arr_chan = {}, {}
A = [e[2] == "A" for e in ev]
B = [e[2] == "B" for e in ev]
r = measure_after_then(log, A, B, "target", 60)
print(json.dumps({k: r.get(k) for k in ("cases", "groups", "rate", "base", "gap_ci_low")}))
"""

    def test_target_scope_does_not_depend_on_hash_seed(self):
        import subprocess
        outs = set()
        for seed in ("0", "1", "2", "3", "7"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            p = subprocess.run([sys.executable, "-c", self.SCRIPT, ROOT], env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr)
            outs.add(p.stdout.strip())
        self.assertEqual(len(outs), 1, outs)
        self.assertIn('"gap_ci_low": ', next(iter(outs)))
        self.assertNotIn('"gap_ci_low": null', next(iter(outs)))  # the bootstrap ran, so the bound was compared


class ReactionSessionConfound(unittest.TestCase):
    """metrics.reaction: independent 'ask' (trigger) and 'answer' (response) events inside daily sessions must not
    look like a regularity just because the 'other times' windows fall in the silent hours."""

    def test_unrelated_events_in_sessions(self):
        import datetime
        from swarmgraph import metrics as M, query as Q
        from swarmgraph.store import open_work
        rnd = random.Random(8)
        tmp = tempfile.mkdtemp()
        ev, tags, n = [], {}, 0
        for a in range(20):
            for day in range(12):
                base = 1767225600 + day * 86400 + 9 * 3600
                for kind, k in (("chatter", 24), ("ask", 4), ("answer", 4)):
                    for _ in range(k):
                        n += 1
                        eid = f"{kind[0]}{n}"
                        ev.append((base + rnd.randint(0, 4 * 3600), eid, f"agent{a}", kind))
                        if kind != "chatter":
                            tags[eid] = ["ask_help"] if kind == "ask" else ["report_result"]
        d = os.path.join(tmp, "d")
        os.makedirs(d)
        with open(os.path.join(d, "events.jsonl"), "w") as f:
            for ts, i, actor, text in ev:
                # each ask is addressed to the same agent, so the 'target' is active in sessions
                rec = {"id": i, "ts": datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "actor": actor, "text": text, "channel": "room"}
                if text == "ask":
                    rec["to"] = [actor]
                f.write(json.dumps(rec) + "\n")
        db = os.path.join(tmp, "i.sqlite")
        build(d, db)
        work = open_work(db)
        for eid, tg in tags.items():
            work.execute("INSERT INTO tags(event_id, tags, other, summary, addressed_to, signed_as, confidence, model, "
                         "created, version) VALUES (?,?,?,?,?,?,?,?,?,2)", (eid, json.dumps(tg), "", "s", "[]", "", "high", "t", "t"))
            work.executemany("INSERT INTO event_tags VALUES (?,?)", [(eid, t) for t in tg])
        work.commit()
        con = Q.connect(db)
        r = M.run(con, "channel_reaction", {"response_tag": "report_result", "trigger_tag": "ask_help", "window_min": 60})
        d_ = r["details"]
        self.assertTrue(d_["conditioned_on_activity"])
        lift = d_["lift_vs_other_times"]
        self.assertIsNotNone(lift)
        self.assertLess(lift, 1.4, d_)  # unrelated → about 1, not the 2-4x the silent-hours baseline would give


class AnalystTools(unittest.TestCase):
    """grep / context / test_claim are what an analyst calls; they must be exact, spread their examples, and say plainly
    what the measurement shows."""

    @classmethod
    def setUpClass(cls):
        from swarmgraph.tools import Session, call
        cls.log = make_log("real")
        cls.s = Session(cls.log.index)
        cls.call = staticmethod(call)

    def test_grep_counts_exactly_and_spreads_examples(self):
        r = self.call(self.s, "grep", {"patterns": ["ping the others"], "examples": 8})
        self.assertEqual(r["total"], 900)  # 30 actors x 30 pings
        self.assertEqual(r["actors"], 30)
        ex = r["examples"]
        self.assertEqual(len(ex), 8)
        days = {e["ts"][:10] for e in ex}
        self.assertGreaterEqual(len(days), 5, days)  # spread over the ten days, not the first ones
        self.assertGreaterEqual(len({e["actor"] for e in ex}), 6)

    def test_context_shows_neighbours(self):
        eid = self.call(self.s, "grep", {"patterns": ["ack received"], "examples": 1})["examples"][0]["id"]
        r = self.call(self.s, "context", {"event_id": eid, "before": 2, "after": 2})
        self.assertEqual(len(r["events"]), 5)
        self.assertEqual([e["id"] for e in r["events"] if e["this"]], [eid])

    def test_test_claim_reads_in_words_and_gives_pairs(self):
        r = self.call(self.s, "test_claim", {"shape": "after_then", "a_patterns": ["ping"], "b_patterns": ["ack"],
                                             "window_minutes": 60})
        self.assertEqual(r["verdict"], "holds", r)
        self.assertIn("cases", r["reading"])
        self.assertTrue(r["pairs_to_read"])
        self.assertIn("holds =", r["bar"])

    def test_pattern_problems_and_errors_are_explained(self):
        r = self.call(self.s, "test_claim", {"shape": "after_then", "a_patterns": ["e"], "b_patterns": ["ack"]})
        self.assertIn("too broad", " ".join(r["pattern_problems"]))
        with self.assertRaises(ValueError):
            self.call(self.s, "grep", {"patterns": ["(unclosed"]})
        with self.assertRaises(ValueError):
            self.call(self.s, "test_claim", {"shape": "after_then", "a_patterns": ["ping"]})


class AfterThen(unittest.TestCase):
    def test_real_structure_holds(self):
        log = make_log("real")
        m = HT.run_test(log, {"shape": "after_then", "a_patterns": ["ping"], "b_patterns": ["ack"],
                              "scope": "same_actor", "window_minutes": 60})
        self.assertEqual(m["verdict"], "holds", m)
        self.assertGreater(m["rate"], 0.6)
        self.assertLess(m["base"], 0.2)

    def test_unrelated_events_do_not_hold(self):
        log = make_log("null")
        m = HT.run_test(log, {"shape": "after_then", "a_patterns": ["ping"], "b_patterns": ["ack"],
                              "scope": "same_actor", "window_minutes": 60})
        self.assertEqual(m["verdict"], "not_holds", m)

    def test_too_little_data_is_insufficient_not_a_pass(self):
        log = make_log("real")
        m = HT.run_test(log, {"shape": "after_then", "a_patterns": ["ping the others"], "b_patterns": ["ack received"],
                              "scope": "target", "window_minutes": 60})  # no relations → no cases
        self.assertEqual(m["verdict"], "insufficient", m)

    def test_pattern_validation_rejects_generic(self):
        log = make_log("real")
        t = {"testable": True, "shape": "after_then", "a_patterns": ["e"], "b_patterns": ["ack"], "reason": ""}
        ok, msg = HT.valid_spec(log, t)
        self.assertFalse(ok)
        self.assertIn("matches", msg)


if __name__ == "__main__":
    unittest.main()


class RiskyPatterns(unittest.TestCase):
    def test_nested_repetition_is_refused(self):
        from swarmgraph.claims import Log
        for p in ("(a+)+", "(.*\\s)*x", "(\\w+\\s?){3,}"):
            self.assertTrue(Log.risky(p), p)
        for p in ("(?:foo|bar)+", "retr(y|ies)", "[(]+x", "qualif\\w*", "(verif(y|ied))+"):
            self.assertFalse(Log.risky(p), p)
