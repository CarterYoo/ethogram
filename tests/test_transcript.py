"""The action-layer adapter must keep what the agent did apart from what it said, link every result to its call, mark
failures, and keep its private reasoning out of the relations."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "adapters"))

import agent_transcript as T  # noqa: E402
from swarmgraph.build import build  # noqa: E402
from swarmgraph import query as Q  # noqa: E402

ME = "0c5968ed-b278-4932-9fa3-147663374789"
STATUS = {"agentStatus": {"currentVillageGoal": "Build a game", "memoryUpdateNeeded": True,
                          "memoryUpdateReminder": "Update your memory now.", "currentRoom": "#general"}}


def rec(i, ts, mtype, content):
    return {"id": f"r{i}", "agent_id": ME, "message_type": mtype, "created_at": ts, "content": content}


def blocks(*bs):
    return {"message": {"content": list(bs)}}


def tool_result(tid, payload, is_error=None):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": text}], "is_error": is_error}


RECORDS = [
    rec(1, "2026-02-09 18:00:00.1", "system", {"subtype": "init", "model": "claude-x"}),
    rec(2, "2026-02-09 18:00:01.5", "assistant", blocks(
        {"type": "thinking", "thinking": "The push may not have worked; I will check before telling @Claude Sonnet 4.6."},
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git push origin main"}})),
    rec(3, "2026-02-09 18:00:03", "user", blocks(tool_result("t1", "Exit code 1\nerror: failed to push some refs", True))),
    rec(4, "2026-02-09 18:00:04", "assistant", blocks(
        {"type": "tool_use", "id": "t2", "name": "mcp__village__chat_message", "input": {"content": "Pushed! All done."}})),
    rec(5, "2026-02-09 18:00:05", "user", blocks(tool_result("t2", {"message": {
        "id": "chat-1", "createdAt": "2026-02-09T18:00:05.000Z", "roomId": "room-a", "hasBeenApproved": True}, **STATUS}))),
    rec(6, "2026-02-09 18:00:06", "assistant", blocks(
        {"type": "tool_use", "id": "t3", "name": "mcp__village__get_events", "input": {"limit": 30}})),
    rec(7, "2026-02-09 18:00:07", "user", blocks(tool_result("t3", {"events": [
        {"actionType": "AGENT_TALK", "agentName": "Claude Sonnet 4.6", "content": "Did it really push?",
         "createdAt": "2/9/2026, 10:00:06 AM", "id": "chat-2"},
        {"actionType": "START_USING_COMPUTER", "agentName": "Claude Sonnet 4.6", "createdAt": "2/9/2026, 10:00:06 AM",
         "id": "st-1"},
        {"actionType": "USER_TALK", "userName": "SomeVisitor", "content": "hello agents",
         "createdAt": "3/11/2026, 10:18:30 AM PDT", "id": "chat-3"}], **STATUS}))),
    rec(8, "2026-02-09 18:00:08", "assistant", blocks(
        {"type": "tool_use", "id": "t4", "name": "mcp__village__edit_memory", "input": {"content": "PR #5 merged."}})),
    rec(9, "2026-02-09 18:00:09", "user", blocks(tool_result("t4", {"content": "PR #5 merged.", "memoryId": "m1"}))),
    rec(10, "2026-02-09 18:00:10", "system", {"subtype": "compact_boundary", "compact_metadata": {"pre_tokens": 1000}}),
    rec(11, "2026-02-09 18:00:11", "result", {"result": "Day done: pushed everything.", "subtype": "success"}),
]
KNOWN = [{"id": "f7a0", "label": "Claude Sonnet 4.6", "kind": "agent"}, {"id": ME, "label": "Opus 4.5 (Claude Code)"}]


class Transcript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp()
        src = os.path.join(tmp, "t.jsonl")
        with open(src, "w") as f:
            # written newest first: the published log is ordered by record id, not by time, and every pairing below
            # (call -> result, run numbers, status changes) must still come out right
            for r in reversed(RECORDS):
                f.write(json.dumps(r) + "\n")
        ak = os.path.join(tmp, "known.jsonl")
        with open(ak, "w") as f:
            for a in KNOWN:
                f.write(json.dumps(a) + "\n")
        cls.out = os.path.join(tmp, "out")
        T.main(src, cls.out, [ak])
        with open(os.path.join(cls.out, "events.jsonl")) as f:
            cls.ev = {e["id"]: e for e in map(json.loads, f)}
        cls.db = os.path.join(tmp, "i.sqlite")
        build(cls.out, cls.db)

    def test_action_result_pair_and_failure(self):
        call, res = self.ev["t1"], self.ev["res:t1"]
        self.assertEqual(call["kind"], "action")
        self.assertTrue(call["text"].startswith("Bash: git push"))
        self.assertEqual(res["kind"], "result")
        self.assertEqual(res["reply_to"], "t1")
        self.assertTrue(res["meta"]["error"])
        self.assertLess(call["ts"], res["ts"])

    def test_said_vs_did(self):
        said = self.ev["chat-1"]  # the platform's chat id, so it joins the chat layer
        self.assertEqual((said["kind"], said["actor"], said["text"]), ("message", ME, "Pushed! All done."))
        self.assertTrue(said["meta"]["posted"])
        self.assertEqual(self.ev["t4"]["kind"], "self_report")  # memory is a claim about itself
        self.assertEqual([e for e in self.ev.values() if e["id"].startswith("end:")][0]["kind"], "self_report")

    def test_reads_keep_authors_and_convert_la_time(self):
        heard = self.ev["chat-2"]
        self.assertEqual((heard["actor"], heard["ts"]), ("f7a0", "2026-02-09T18:00:06Z"))  # PST = UTC-8
        self.assertEqual(self.ev["chat-3"]["ts"], "2026-03-11T17:18:30Z")  # PDT = UTC-7
        self.assertTrue(self.ev["chat-3"]["actor"].startswith("visitor:"))  # visitors keep no name
        self.assertNotIn("st-1", self.ev)  # status items are not messages
        read = self.ev["read:t3:chat-2"]
        self.assertEqual((read["kind"], read["actor"], read["reply_to"]), ("read", ME, "chat-2"))

    def test_harness_instructions_and_markers(self):
        texts = [e["text"] for e in self.ev.values() if e["actor"] == "harness"]
        self.assertIn("village goal: Build a game", texts)
        self.assertIn("Update your memory now.", texts)
        self.assertTrue(any(t.startswith("run 1 starts") for t in texts))
        self.assertTrue(any(t.startswith("context compacted") for t in texts))
        with open(os.path.join(self.out, "phases.jsonl")) as f:
            phases = [json.loads(l) for l in f]
        self.assertEqual(phases[0]["label"], "Build a game")

    def test_reasoning_addresses_nobody(self):
        """the thinking names @Claude Sonnet 4.6, but private reasoning is not a message to anyone"""
        con = Q.connect(self.db)
        rows = con.execute("SELECT r.type, e.kind FROM relations r JOIN events e ON e.id=r.event_id").fetchall()
        self.assertNotIn("reasoning", {k for _, k in rows})
        self.assertNotIn("result", {k for _, k in rows})
        self.assertIn(("read", "read"), {(t, k) for t, k in rows})  # the agent read Sonnet's message


if __name__ == "__main__":
    unittest.main()


class FailureFlag(unittest.TestCase):
    """A shell tool that returns stdout in `output` and stderr in `error` must not turn every git push into a failure
    (measured: hundreds of successful pushes and pulls carried git's progress lines in `error`)."""

    def check(self, payload, is_error=None):
        b = {"is_error": is_error}
        text, obj, st, image = T.result_payload([{"type": "text", "text": json.dumps(payload)}])
        return T.failed(b, text, obj)

    def test_stderr_progress_is_not_failure(self):
        self.assertIsNone(self.check({"output": "", "error": "To https://github.com/x/y.git\n   830d448..e3c838c  main -> main"}))
        self.assertIsNone(self.check({"output": "ok", "error": "From https://github.com/x/y\n * branch main -> FETCH_HEAD"}))

    def test_real_failures_are(self):
        self.assertEqual(self.check({"output": "", "error": "error: failed to push some refs to 'origin'"}), "stderr_failure")
        self.assertEqual(self.check({"output": None, "error": "GraphQL: Could not resolve to a PullRequest with the number of 338."}),
                         "stderr_failure")
        self.assertEqual(self.check({"error": "duration=90 is too long."}), "error_field")  # not a shell: any error counts
        self.assertEqual(T.failed({"is_error": True}, "Exit code 1", None), "is_error")
        self.assertEqual(T.failed({}, "Error: Agent is not in a computer use session", None), "error_reply")


class LedgerBoard(unittest.TestCase):
    """Cases are framed for the agent whose record it is: another agent's claim it heard is shown as `heard`, with who
    heard it, and an unfiltered listing shows every kind on its first page (measured: framed as 'peer', none of 10
    analysts opened them)."""

    def test_heard_framing_and_interleaving(self):
        from swarmgraph import ledger as L
        from swarmgraph.store import open_work
        Transcript.setUpClass()
        db = Transcript.db
        w = open_work(db)
        w.execute("CREATE TABLE IF NOT EXISTS claim_checks(case_type TEXT, event_id TEXT, actor TEXT, flags TEXT, "
                  "verdict TEXT, material INTEGER, why TEXT, evidence TEXT, PRIMARY KEY(case_type, event_id))")
        rows = [("own", "chat-1", ME, "overstates", 1), ("own", "t4", ME, "overstates", 1),
                ("peer", "chat-2", "f7a0", "contradicted", 1), ("intent", "r2:0", ME, "neither", 0)]
        for t, e, a, v, m in rows:
            w.execute("INSERT OR REPLACE INTO claim_checks VALUES (?,?,?,?,?,?,?,?)", (t, e, a, "[]", v, m, "why", "[]"))
        w.commit()
        b = L.board(Q.connect(db), limit=3)
        self.assertEqual([c["type"] for c in b["cases"]], ["intent", "own", "heard"])  # every kind on page one
        heard = [c for c in b["cases"] if c["type"] == "heard"][0]
        self.assertEqual(heard["heard_by"], ["Opus 4.5 (Claude Code)"])
        self.assertIn("ITS behaviour", b["kinds"]["heard"]["meaning"])
        self.assertEqual(b["kinds"]["heard"]["claims_made_by"], {"Claude Sonnet 4.6": 1})
        only = L.board(Q.connect(db), case_type="heard")
        self.assertEqual([c["type"] for c in only["cases"]], ["heard"])

