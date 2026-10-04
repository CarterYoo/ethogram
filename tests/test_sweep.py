"""Delegated reading must cover the whole record (every event in exactly one chunk, cut where the agent's context
starts afresh), keep sub-agents' citations inside what they read, and keep the analyst's questions, their answers and
what is still unread on record. Behaviour counts must find a burst of one behaviour, not just a busy day."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from swarmgraph.build import build  # noqa: E402
from swarmgraph import query as Q, chunks as C, sweep as S, behaviour as B  # noqa: E402
from swarmgraph.store import open_work  # noqa: E402


def dataset(tmp):
    ev = []

    def add(i, ts, actor, kind, text, **kw):
        ev.append({"id": i, "ts": ts, "actor": actor, "kind": kind, "text": text, **kw})

    add("peer-1", "2026-03-01T09:00:00Z", "b2", "message", "I merged PR #5, all tests pass")
    for d in range(1, 12):  # eleven active days of ordinary work; day 9 has a burst of pushes
        day = f"2026-03-{d:02d}"
        add(f"run:{d}", f"{day}T10:00:00Z", "harness", "action", f"run {d} starts", meta={"boundary": "context"})
        add(f"r{d}", f"{day}T10:00:01Z", "a1", "read", "", reply_to="peer-1")
        for k in range(24):
            t = f"{day}T10:{1 + k:02d}:00Z"
            push = (d == 9 and k < 14) or k == 0
            add(f"act{d}-{k}", t, "a1", "action", "bash: git push origin main" if push else "bash: ls -la",
                meta={"tool": "bash"})
            add(f"res{d}-{k}", t, "a1", "result", "error: failed" if k == 5 else "ok", reply_to=f"act{d}-{k}",
                meta={"error": k == 5})
            add(f"th{d}-{k}", t, "a1", "reasoning", "I think the push worked")
        add(f"msg{d}", f"{day}T10:40:00Z", "a1", "message", "Pushed the fix, verified.")
        if d == 2:
            add("write2", f"{day}T10:41:00Z", "a1", "action", "bash: cat > top5.html << EOF stories: EPA, Adobe EOF",
                meta={"tool": "bash"})
        if d == 3:
            add("claim3", f"{day}T10:41:00Z", "a1", "message", "My final top5.html lists NASA and BRICS.")
        if d == 4:
            add("compact:4", f"{day}T10:41:00Z", "harness", "action", "context compacted", meta={"boundary": "context"})
            add("msg4b", f"{day}T10:42:00Z", "a1", "message", "Volunteer signed up: jane.doe@gmail.com")
    os.makedirs(tmp, exist_ok=True)
    with open(os.path.join(tmp, "events.jsonl"), "w") as f:
        for e in ev:
            f.write(json.dumps(e) + "\n")
    with open(os.path.join(tmp, "actors.jsonl"), "w") as f:
        for a in ({"id": "a1", "label": "Agent One"}, {"id": "b2", "label": "Agent Two"},
                  {"id": "harness", "label": "harness", "kind": "system"}):
            f.write(json.dumps(a) + "\n")
    with open(os.path.join(tmp, "phases.jsonl"), "w") as f:
        f.write(json.dumps({"label": "week one", "start": "2026-03-01T00:00:00Z", "end": "2026-03-06T00:00:00Z"}) + "\n")
        f.write(json.dumps({"label": "week two", "start": "2026-03-06T00:00:00Z", "end": "2026-03-12T00:00:00Z"}) + "\n")
    return ev


class Sweep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp()
        cls.events = dataset(os.path.join(tmp, "ds"))
        cls.db = os.path.join(tmp, "x.sqlite")
        build(os.path.join(tmp, "ds"), cls.db)
        cls.con, cls.work = Q.connect(cls.db), open_work(cls.db)
        cls.chunks = C.make(cls.con, target=2000, cap=6000)
        C.save(cls.con, cls.work, cls.chunks)
        cls.con = Q.connect(cls.db)

    def test_every_event_of_the_record_in_exactly_one_chunk(self):
        ids = [i for c in self.chunks for i in c["events"]]
        record = {e["id"] for e in self.events if e["actor"] != "b2"}  # others' messages appear through reads
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), record)
        starts = {c["events"][0] for c in self.chunks}
        self.assertIn("compact:4", starts)  # a new context starts a new chunk
        self.assertTrue(all(c["chars"] <= 6000 for c in self.chunks))

    def test_rendering_numbers_lines_and_shortens_rereads(self):
        first = next(c for c in self.chunks if "r1" in c["events"])
        text, num = C.text(self.con, first["events"])
        self.assertIn("Agent Two said: I merged PR #5", text)
        self.assertIn("result FAILED", text)
        n = next(k for k, v in num.items() if v == "r1")
        self.assertIn(f"L{n} ", text)
        later = next(c for c in self.chunks if "r2" in c["events"])
        self.assertIn("(again) Agent Two", C.text(self.con, later["events"])[0])

    def test_repeated_requests_fold_into_one_line(self):
        ev = []
        for k in range(10):
            ev.append({"id": f"a{k}", "ts": f"2026-06-17T11:{k:02d}:00Z", "actor": "bot", "kind": "action",
                       "text": f"capture via web archive: https://x.gov/api?State_Id={k}&zz=oai{k}", "reply_to": None,
                       "channel": "x.gov", "meta": {}, "error": False})
            ev.append({"id": f"r{k}", "ts": f"2026-06-17T11:{k:02d}:00Z", "actor": "bot", "kind": "result",
                       "text": "HTTP 500" if k == 3 else "HTTP 200", "reply_to": f"a{k}", "channel": "x.gov",
                       "meta": {}, "error": k == 3})
        ev.append({"id": "m", "ts": "2026-06-17T12:00:00Z", "actor": "bot", "kind": "message", "text": "done",
                   "reply_to": None, "channel": None, "meta": {}, "error": False})
        gs = C.runs(ev)
        self.assertEqual([len(g) for g in gs], [20, 1])
        txt = C.group_line([ev[k] for k in gs[0]], {}, "bot")
        self.assertIn("action x10", txt)
        self.assertIn("State_Id (10 values)", txt)
        self.assertIn("FAILED HTTP 500 x1", txt)

    def test_fold_shows_values_unlike_the_rest(self):
        odd = C._odd({"2", "99", "102", "135", "%27", "1+OR+1%3D1", "%3C", "2147483648", "abc", "1%2C2"})
        for v in ("'", "1 OR 1=1", "<", "2147483648", "abc", "1,2"):
            self.assertIn(repr(v), odd)
        self.assertNotIn("'99'", odd)
        self.assertEqual(C._odd({"1", "2", "3"}), "")

    def test_citations_outside_the_chunk_are_dropped(self):
        res, cited, dropped = S.verify({"did": [{"what": "x", "lines": ["L1", "L99", "L-x", "2"]}],
                                        "key_reads": [{"line": "L2", "changed": "y"}], "unclear": ""}, {1: "e1", 2: "e2"})
        self.assertEqual(res["did"][0]["events"], ["e1", "e2"])
        self.assertEqual(res["key_reads"][0]["event"], "e2")
        self.assertEqual((cited, dropped), (3, 2))

    def test_shell_programs(self):
        self.assertEqual(B.shell_program("bash: cd /x && git push origin main"), "git push")
        self.assertEqual(B.shell_program("Bash: gh pr create --title t"), "gh pr create")
        self.assertEqual(B.shell_program("bash: FOO=1 timeout 30 python3 run.py | tail"), "python3")
        self.assertIsNone(B.shell_program("bash: cd /x && echo hi"))
        self.assertIsNone(B.shell_program("bash: data.json"))

    def test_burst_of_one_behaviour_and_watch_list(self):
        r = B.changes(self.con, limit=50)
        pushes = [b for b in r["bursts"] if b["behaviour"] == "ran: git push"]
        self.assertTrue(pushes and pushes[0]["day"] == "2026-03-09" and pushes[0]["burst"] == "alone", r["bursts"])
        self.assertFalse([b for b in r["bursts"] if b["behaviour"] == "acted: bash"])  # same volume every day
        mail = [w for w in r["watch"] if w["behaviour"] == "posts a personal e-mail address"]
        self.assertEqual(mail[0]["examples"], ["msg4b"])

    def test_delegated_question_queued_answered_and_kept(self):
        open(self.db + ".broker", "w").close()  # a broker is watching: submit must only queue
        try:
            some = [c["id"] for c in self.chunks[:2]]
            job = S.submit(self.db, "Did it verify its pushes?", some)
            self.assertEqual(job["to_read"], 2)
            con = Q.connect(self.db)
            self.assertEqual(S.collect(con, job["job"])["status"], "pending")
            k = S.qkey("Did it verify its pushes?")
            for cid in some:  # what the worker writes
                self.work.execute("INSERT INTO chunk_reads VALUES (?,?,?,?,?,?,?,?,?)",
                                  (cid, "Did it verify its pushes?", k, "now", "low",
                                   json.dumps({"findings": [{"finding": "no check", "events": ["msg1"]}],
                                               "none_here": False, "unclear": ""}), 1, 0, 1.0))
            self.work.execute("UPDATE jobs SET status='done' WHERE id=?", (job["job"],))
            self.work.commit()
            out = S.collect(Q.connect(self.db), job["job"])
            self.assertEqual((out["answered"], out["pending"]), (2, 0))
            again = S.submit(self.db, "did it verify its pushes? ", some)  # same question, normalised: nothing to read
            self.assertEqual(again["to_read"], 0)
            self.assertEqual(S.periods(Q.connect(self.db))["periods"][0]["your_questions_asked"], 2)
        finally:
            os.remove(self.db + ".broker")

    def test_statement_traced_back_to_where_it_made_the_thing(self):
        cid = next(c["id"] for c in self.chunks if "claim3" in c["events"])
        earlier = next(c["id"] for c in self.chunks if "write2" in c["events"])
        self.work.execute("INSERT OR REPLACE INTO chunk_reads VALUES (?,?,?,?,?,?,?,?,?)",
                          (cid, S.SWEEP_Q, S.SWEEP_Q, "now", "low", json.dumps({
                              "did": [], "instructions": [], "commitments": [], "concerns": [], "key_reads": [],
                              "said": [{"claim": "final list is NASA and BRICS", "record": "unsupported_here",
                                        "why": "", "events": ["claim3"]}], "unclear": ""}), 1, 0, 1.0))
        self.work.commit()
        plan = S.trace_plan(Q.connect(self.db), self.work)
        self.assertIn((earlier, 0), plan)
        item = plan[(earlier, 0)][0]
        self.assertEqual((item["statement"], item["last_change"]), ("claim3", ["top5.html"]))

    def test_map_counts_sweep_findings_and_shows_unread(self):
        cid = next(c["id"] for c in self.chunks if "msg4b" in c["events"])
        self.work.execute("INSERT OR REPLACE INTO chunk_reads VALUES (?,?,?,?,?,?,?,?,?)",
                          (cid, S.SWEEP_Q, S.SWEEP_Q, "now", "low", json.dumps({
                              "did": [{"what": "posted a signup", "events": ["msg4b"]}],
                              "said": [{"claim": "verified", "record": "overstates", "why": "no check",
                                        "events": ["msg4"]}],
                              "instructions": [], "commitments": [],
                              "concerns": [{"what": "posted a volunteer's e-mail", "kind": "privacy",
                                            "severity": "medium", "sanctioned": False, "events": ["msg4b"]}],
                              "key_reads": [], "unclear": ""}), 2, 0, 1.0))
        self.work.commit()
        con = Q.connect(self.db)
        p = S.periods(con)["periods"][0]
        self.assertEqual((p["read_by_sweep"], p["statements_overstated"], p["statements_contradicted"]), (1, 1, 0))
        self.assertLess(p["read_by_sweep"], p["chunks"])  # the rest of the period is visibly unread
        self.assertEqual(p["concerns_medium_or_high"], {"privacy": 1})
        v = S.chunk_view(con, cid)
        self.assertIn("jane.doe@gmail.com", v["concerns"][0]["evidence"][0])


if __name__ == "__main__":
    unittest.main()
