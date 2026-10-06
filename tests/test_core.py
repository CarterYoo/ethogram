import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from swarmgraph import hypotheses as H, metrics as M, query as Q, structure as ST  # noqa: E402
from swarmgraph.build import build, mention_regex  # noqa: E402
from swarmgraph.format import FormatError, load  # noqa: E402
from swarmgraph.store import open_work  # noqa: E402


def write(dir_, name, rows):
    with open(os.path.join(dir_, name), "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)


EVENTS = [
    {"id": "e1", "ts": "2026-01-01T10:00:00Z", "actor": "boss", "kind": "call", "to": ["worker"], "text": "spawn: survey"},
    {"id": "e2", "ts": "2026-01-01T10:01:00Z", "actor": "worker", "text": "@boss starting now"},
    {"id": "e3", "ts": "2026-01-01T10:02:00Z", "actor": "boss", "to": ["worker"], "text": "thanks"},
    {"id": "e4", "ts": "2026-01-01T10:30:00Z", "actor": "worker", "kind": "return", "to": ["boss"], "text": "done: 9 papers"},
    {"id": "e5", "ts": "2026-01-01T11:00:00Z", "actor": "noisy", "text": "@worker @boss want my template?"},
    {"id": "e6", "ts": "2026-01-01T11:01:00Z", "actor": "noisy", "text": "@worker still offering"},
    {"id": "e7", "ts": "2026-01-01T11:05:00Z", "actor": "worker", "reply_to": "e5", "text": "no thanks"},
    {"id": "e8", "ts": "2026-01-01T12:00:00Z", "actor": "noisy", "kind": "self_report", "text": "everyone adopted my template"},
]
ACTORS = [{"id": "worker", "role": "researcher", "parent": "boss", "lineage": "team-a"},
          {"id": "boss", "role": "coordinator", "lineage": "team-a"},
          {"id": "noisy", "aliases": ["N"]}]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.data = os.path.join(cls.tmp, "data")
        os.makedirs(cls.data)
        write(cls.data, "events.jsonl", EVENTS)
        write(cls.data, "actors.jsonl", ACTORS)
        cls.db = os.path.join(cls.tmp, "idx.sqlite")
        build(cls.data, cls.db)
        cls.con, cls.work = Q.connect(cls.db), open_work(cls.db)


class Format(unittest.TestCase):
    def check(self, rows, msg):
        with tempfile.TemporaryDirectory() as d:
            write(d, "events.jsonl", rows)
            with self.assertRaises(FormatError) as cm:
                load(d)
            self.assertIn(msg, str(cm.exception))

    def test_missing_field_names_the_line(self):
        self.check([{"id": "a", "ts": "2026-01-01T00:00:00Z", "actor": "x"}, {"id": "b", "actor": "x"}], "line 2: missing ts")

    def test_bad_kind(self):
        self.check([{"id": "a", "ts": "2026-01-01T00:00:00Z", "actor": "x", "kind": "shout"}], "kind must be one of")

    def test_duplicate_id(self):
        row = {"id": "a", "ts": "2026-01-01T00:00:00Z", "actor": "x"}
        self.check([row, row], "duplicate id")


class Mentions(unittest.TestCase):
    def found(self, text, aliases):
        return [m.group(1) for m in mention_regex(aliases).finditer(text)]

    def test_longest_alias_and_versions(self):
        self.assertEqual(self.found("hi @GPT-5.1 and @GPT-5! not @GPT-5.2", ["GPT-5", "GPT-5.1"]), ["GPT-5.1", "GPT-5"])

    def test_no_partial_words(self):
        self.assertEqual(self.found("@Lunar @luna.", ["Luna"]), ["luna"])


class Relations(Base):
    def rel(self, t, src, dst):
        return [tuple(r) for r in self.con.execute("SELECT method FROM relations WHERE type=? AND src=? AND dst=?", (t, src, dst))]

    def test_explicit_mention_reply_and_call_methods(self):
        self.assertEqual({r[0] for r in self.rel("invoked", "boss", "worker")}, {"call_kind", "actor_parent"})
        self.assertEqual([r[0] for r in self.rel("returned", "worker", "boss")], ["return_kind"])
        self.assertIn(("at_mention",), self.rel("addressed", "worker", "boss"))
        self.assertIn(("reply_field",), self.rel("addressed", "worker", "noisy"))

    def test_self_report_is_not_a_relation(self):
        self.assertEqual(self.con.execute("SELECT count(*) FROM relations WHERE event_id='e8'").fetchone()[0], 0)

    def test_replied_needs_an_address_back(self):
        ans = lambda e, src, dst: self.con.execute("SELECT answered_by FROM relations WHERE type='addressed' AND "
                                                   "event_id=? AND src=? AND dst=?", (e, src, dst)).fetchone()[0]
        self.assertEqual(ans("e2", "worker", "boss"), "e3")     # boss addressed worker back a minute later
        self.assertEqual(ans("e5", "noisy", "worker"), "e7")    # worker replied (reply_to) within the window
        self.assertIsNone(ans("e5", "noisy", "boss"))           # boss never addressed noisy back

    def test_card_shows_lineage_and_parent(self):
        card = Q.actor_card(self.con, "worker")
        self.assertEqual([x["actor"] for x in card["invoked_by"]], ["boss"])
        self.assertEqual([x["id"] for x in card["same_lineage"]], ["boss"])


class Hypotheses(Base):
    def test_details_prefix_and_baseline(self):
        r = M.run(self.con, "reply_rate", {"actor": "noisy"})
        self.assertEqual(M.check(r, {"field": "details.n", "op": "==", "value": 3})[0], True)
        self.assertIn(M.check(r, {"op": "<", "value": "others_rate"})[0], (True, False))

    def test_supported_requires_metric_and_evidence(self):
        # noisy's addresses: 2 of 3 answered (0.67)
        h = H.propose(self.con, self.work, "noisy is answered less than 90% of the time",
                      {"metric": "reply_rate", "params": {"actor": "noisy"}, "expect": {"op": "<", "value": 0.9}})
        self.assertEqual(H.verdict(self.con, self.work, h["id"], "supported")["verdict"], "inconclusive")  # no evidence
        H.add_evidence(self.con, self.work, h["id"], [{"event_id": "e6", "stance": "supports"}, {"event_id": "zz", "stance": "supports"}])
        self.assertEqual(H.get(self.work, h["id"])["evidence"][1]["verified"], False)
        self.assertEqual(H.verdict(self.con, self.work, h["id"], "supported")["verdict"], "supported")

    def test_failed_metric_cannot_be_supported(self):
        h = H.propose(self.con, self.work, "noisy is always answered", {"metric": "reply_rate", "params": {"actor": "noisy"},
                                                                         "expect": {"op": ">=", "value": 0.99}})
        H.add_evidence(self.con, self.work, h["id"], [{"event_id": "e7", "stance": "supports"}])
        self.assertEqual(H.verdict(self.con, self.work, h["id"], "supported")["verdict"], "inconclusive")

    def test_bad_plan_is_rejected_up_front(self):
        with self.assertRaises(Q.QueryError):
            H.propose(self.con, self.work, "x", {"metric": "nope", "params": {}, "expect": {"op": ">", "value": 1}})


class Behaviour(unittest.TestCase):
    """Tags written as the tagger would; the index is rebuilt so LLM-found addressees become llm_text relations."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        data = os.path.join(cls.tmp, "data")
        os.makedirs(data)
        write(data, "events.jsonl", EVENTS + [
            {"id": "e9", "ts": "2026-01-01T12:10:00Z", "actor": "boss", "kind": "action", "reply_to": "e6",
             "text": "removed noisy's offer"},
            {"id": "e10", "ts": "2026-01-01T12:20:00Z", "actor": "noisy", "text": "worker: reposting my template"}])
        write(data, "actors.jsonl", ACTORS)
        cls.db = os.path.join(cls.tmp, "idx.sqlite")
        build(data, cls.db)
        work = open_work(cls.db)
        tag = {"e5": ["coordinate"], "e6": ["retry_variant"], "e9": ["moderate", "remove_others"],
               "e10": ["restore", "coordinate"], "e2": ["report_result"], "e7": ["coordinate"]}
        for eid, tags in tag.items():
            work.execute("INSERT INTO tags(event_id, tags, other, summary, addressed_to, signed_as, confidence, model, "
                         "created, version) VALUES (?,?,?,?,?,?,?,?,?,2)",
                         (eid, json.dumps(tags), "", "s", json.dumps(["worker"] if eid == "e10" else []), "", "high", "t", "t"))
            work.executemany("INSERT INTO event_tags VALUES (?,?)", [(eid, t) for t in tags])
        work.commit()
        build(data, cls.db)
        cls.con = Q.connect(cls.db)

    def test_llm_addressee_becomes_relation(self):
        rows = self.con.execute("SELECT src, dst FROM relations WHERE event_id='e10' AND method='llm_text'").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("noisy", "worker")])

    def test_tag_rate_with_baseline(self):
        r = M.run(self.con, "tag_rate", {"tag": "coordinate", "actor": "noisy"})
        self.assertEqual((r["details"]["n"], r["details"]["count"]), (3, 2))
        self.assertEqual(r["details"]["others_rate"], round(1 / 3, 4))  # worker: e2, e7 → e7; boss: e9

    def test_reaction_after_moderation(self):
        r = M.run(self.con, "reaction", {"response_tag": "restore", "trigger_tag": "moderate", "window_min": 30})
        self.assertEqual((r["details"]["triggers"], r["details"]["responded"], r["value"]), (1, 1, 1.0))
        self.assertIn("e9", r["evidence"])
        self.assertIn("e10", r["evidence"])

    def test_spread_order(self):
        r = M.run(self.con, "tag_spread", {"tag": "coordinate"})
        self.assertEqual([a for a, _ in r["details"]["adopters"]], ["noisy", "worker"])
        self.assertEqual(r["details"]["linked_share"], 1.0)  # worker had contact with noisy before it coordinated

    def test_useful_links_are_typed_and_skip_plain_adjacency(self):
        ST.build_links(self.con, open_work(self.db), log=lambda m: None)
        con = Q.connect(self.db)  # reattach so the new table is visible
        links = {tuple(r) for r in con.execute("SELECT src, dst, type FROM w.links")}
        self.assertIn(("boss", "noisy", "removes"), links)          # e9: moderation attached to noisy's e6
        self.assertIn(("noisy", "worker", "communicates"), links)   # e10: addressee found by the LLM
        self.assertIn(("boss", "worker", "delegates"), links)       # e1: call
        self.assertFalse([l for l in links if l[:2] == ("worker", "noisy")])  # e7 only replies structurally

    def test_change_candidate_after_intervention(self):
        ST.build_links(self.con, open_work(self.db), log=lambda m: None)
        con = Q.connect(self.db)
        evs = ST.entity_events(con, "actor", "noisy")
        cands = ST.candidates(con, "actor", "noisy", evs)
        self.assertEqual([(c["at"], c["trigger"]) for c in cands if c["trigger"]], [("e10", ["e9"])])

    def test_verify_flags_bad_citations_numbers_and_order(self):
        card = {"role": {"text": "posts 99 offers", "voice": "observed", "evidence": ["e5", "zz"]},
                "phases": [{"start": "e5", "end": "e6", "title": "offer", "summary": {"text": "x", "voice": "observed",
                                                                                      "evidence": ["e5"]}}],
                "changes": [{"at": "e6", "trigger": ["e10"], "trigger_kind": "external", "before_after": "a → b"}]}
        ts = {"e5": "2026-01-01 11:00:00", "e6": "2026-01-01 11:01:00", "e10": "2026-01-01 12:20:00"}
        issues = ST.verify(card, {"e5", "e6"}, {"e5", "e6", "e10"}, ts, "input mentions 3 offers")
        self.assertTrue(any("zz" in i for i in issues))
        self.assertTrue(any("'99'" in i for i in issues))
        self.assertTrue(any("after the change" in i for i in issues))

    def test_event_shows_behaviour(self):
        self.assertEqual(Q.get_event(self.con, "e9")["behaviour"]["tags"], ["moderate", "remove_others"])


class Prepare(unittest.TestCase):
    def test_code_only_pipeline_runs_and_reports(self):
        with tempfile.TemporaryDirectory() as d:
            data = os.path.join(d, "data")
            os.makedirs(data)
            write(data, "events.jsonl", EVENTS)
            write(data, "actors.jsonl", ACTORS)
            db = os.path.join(d, "idx.sqlite")
            p = subprocess.run([sys.executable, "-m", "swarmgraph", "--db", db, "prepare", data, "--no-llm"], cwd=ROOT,
                               text=True, capture_output=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr[-500:])
            self.assertEqual([s["stage"] for s in json.loads(p.stdout)], ["build", "structure"])
            rep = json.load(open(db + ".prepare.json"))
            self.assertEqual(rep["llm"], False)


class MCP(Base):
    def test_handshake_list_and_errors(self):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "overview", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "get_event", "arguments": {"event_id": "nope"}}}]
        p = subprocess.run([sys.executable, "-m", "swarmgraph", "--db", self.db, "mcp"], cwd=ROOT, text=True,
                           input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, timeout=60)
        out = {r["id"]: r for r in map(json.loads, p.stdout.splitlines())}
        self.assertEqual(set(out), {1, 2, 3, 4})  # the notification gets no reply, nothing else on stdout
        self.assertGreaterEqual(len(out[2]["result"]["tools"]), 15)
        self.assertEqual(json.loads(out[3]["result"]["content"][0]["text"])["events"], len(EVENTS))
        self.assertTrue(out[4]["result"]["isError"])


class SelectEvents(Base):
    def test_select_by_recorded_fields_not_words(self):
        r = Q.select_events(self.con, kind="return")
        self.assertEqual([e["id"] for e in r["events"]], ["e4"])
        r = Q.select_events(self.con, text="still")
        self.assertEqual(r["total"], 1)
        g = Q.select_events(self.con, group_by="actor")
        self.assertEqual(sum(c["events"] for c in g["counts"]), len(EVENTS))
        with self.assertRaises(Q.QueryError):
            Q.select_events(self.con, meta={"x') OR 1=1 --": 1})


class Concerns(Base):
    def test_flags_with_basis_flow_into_queries(self):
        from swarmgraph import alignment as A
        w = open_work(self.db)
        w.execute("DELETE FROM concerns")
        w.execute("INSERT INTO concerns(event_id, kind, quote, confidence, basis, unconfirmed) VALUES "
                  "('e6', 'persist_after_stop', 'still offering', 'high', 'context', 'no stop order shown')")
        w.commit()
        ST.build_links(self.con, w, log=lambda m: None)  # as after the structure stage
        con = Q.connect(self.db)
        r = A.concern_events(con, kind="persist_after_stop")
        self.assertEqual((r["total"], r["rows"][0]["basis"], r["rows"][0]["unconfirmed"]), (1, "context", "no stop order shown"))
        self.assertIn("not", r["kind_means"] + r["note"])
        self.assertEqual(A.concern_actors(con)["actors"][0]["actor"], "noisy")
        self.assertEqual(A.patterns(con)[0]["basis"], {"context": 1})
        w.execute("DELETE FROM concerns")
        w.commit()


class Brief(Base):
    def test_map_and_node_are_code_clues(self):
        from swarmgraph import brief
        w = open_work(self.db)
        w.execute("DELETE FROM concerns")
        w.execute("INSERT INTO concerns(event_id, kind, quote, confidence, basis, unconfirmed) VALUES "
                  "('e6', 'cross_instance_sharing', 'still offering', 'high', 'shown', '')")
        w.execute("INSERT OR REPLACE INTO tags(event_id, tags, summary) VALUES ('e5', ?, 'offers a template')",
                  (json.dumps(["coordinate"]),))
        w.execute("INSERT INTO event_tags VALUES ('e5', 'coordinate')")
        w.commit()
        ST.build_links(self.con, w, log=lambda m: None)
        con = Q.connect(self.db)
        m = brief.overview_map(con)
        self.assertEqual(m["dataset"]["events"], len(EVENTS))
        allc = m["concerns"]["dominant"] + m["concerns"]["rest"]
        self.assertTrue(any(r["concern"] == "cross_instance_sharing" and r["basis"] == {"shown": 1} for r in allc))
        # a node resolves each entity form and points back at events
        a = brief.node(con, "noisy")
        self.assertEqual(a["node"], "actor")
        self.assertIn("see", a)
        c = brief.node(con, "concern:cross_instance_sharing")
        self.assertEqual(c["basis"], {"shown": 1})
        self.assertEqual(c["examples"][0]["event_id"], "e6")
        self.assertEqual(brief.node(con, "behaviour:coordinate")["node"], "behaviour")
        w.execute("DELETE FROM concerns")
        w.commit()


class Themes(Base):
    def test_assign_and_regularities(self):
        from swarmgraph import themes as TH
        w = open_work(self.db)
        w.execute("INSERT OR REPLACE INTO cards VALUES ('themes','all',1,'now','{}',?,?,'[]')",
                  (json.dumps([{"name": "offers help", "definition": "offers a template or help", "cues": ["offer"]},
                               {"name": "acts on help", "definition": "acts on an offer", "cues": ["start"]}]), "x"))
        w.execute("CREATE TABLE IF NOT EXISTS event_themes(event_id TEXT, theme TEXT, PRIMARY KEY(event_id, theme))")
        for e, th in (("e5", "offers help"), ("e6", "offers help"), ("e2", "acts on help")):
            w.execute("INSERT OR REPLACE INTO event_themes VALUES (?,?)", (e, th))
        w.commit()
        con = Q.connect(self.db)
        m = TH.theme_map(con)
        self.assertEqual({t["theme"] for t in m["themes"]}, {"offers help", "acts on help"})
        self.assertEqual(TH.theme_node(con, "offers help")["events"], 2)
        w.execute("DROP TABLE event_themes")
        w.execute("DELETE FROM cards WHERE kind='themes'")
        w.commit()

    def test_board_order_is_interest_not_size_and_classes_can_be_paged(self):
        """A small class an investigator should open first must not sink below a big routine one (measured: by size the
        class holding the agents' retractions came 19th of 35 and 23 of 24 analysts never opened it); and a class must
        be readable whole, page by page, not only as a sample."""
        from swarmgraph import themes as TH
        w = open_work(self.db)
        w.execute("INSERT OR REPLACE INTO cards VALUES ('themes','all',1,'now','{}',?,?,'[]')",
                  (json.dumps([{"name": "routine work", "definition": "does the task", "cues": ["x"], "interest": 1},
                               {"name": "takes back a claim", "definition": "retracts something it said", "cues": ["y"],
                                "interest": 5}]), "x"))
        w.execute("CREATE TABLE IF NOT EXISTS event_themes(event_id TEXT, theme TEXT, PRIMARY KEY(event_id, theme))")
        big = [e["id"] for e in EVENTS][:5]
        for e in big:
            w.execute("INSERT OR REPLACE INTO event_themes VALUES (?,?)", (e, "routine work"))
        w.execute("INSERT OR REPLACE INTO event_themes VALUES (?,?)", (EVENTS[-1]["id"], "takes back a claim"))
        w.commit()
        con = Q.connect(self.db)
        board = TH.theme_map(con)["themes"]
        self.assertEqual(board[0]["theme"], "takes back a claim", board)  # 1 event, but first
        self.assertEqual(board[0]["interest"], 5)
        page1 = TH.theme_node(con, "routine work", examples=2, offset=0)
        page2 = TH.theme_node(con, "routine work", examples=2, offset=2)
        page3 = TH.theme_node(con, "routine work", examples=2, offset=4)
        seen = [x["event_id"] for p in (page1, page2, page3) for x in p["examples"]]
        self.assertEqual(sorted(seen), sorted(big))  # every member reached, none twice
        self.assertIn("offset 2", page1["examples_are"])
        self.assertIn("SAMPLE", TH.theme_node(con, "routine work")["examples_are"])
        w.execute("DROP TABLE event_themes")
        w.execute("DELETE FROM cards WHERE kind='themes'")
        w.commit()


class CopiedIndex(Base):
    def test_opens_after_copy_without_side_files(self):
        import shutil
        w = open_work(self.db)
        w.execute("CREATE TABLE IF NOT EXISTS probe(x)")
        w.commit()
        w.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst = tempfile.mkdtemp()
        for suffix in ("", ".work"):  # what a user gets when the index is copied or shared: no -wal / -shm
            shutil.copy(self.db + suffix, os.path.join(dst, "copy.sqlite" + suffix))
        con = Q.connect(os.path.join(dst, "copy.sqlite"))
        self.assertEqual(con.execute("SELECT count(*) FROM events").fetchone()[0], len(EVENTS))
        con.execute("SELECT count(*) FROM w.probe").fetchone()


class Redact(unittest.TestCase):
    def test_withholds_detail_keeps_plain_words(self):
        from swarmgraph.redact import redact, redact_obj
        self.assertEqual(redact("Total is 42 of 350 points, see 3.2.1."), "Total is 42 of 350 points, see 3.2.1.")
        self.assertEqual(redact("open https://example.org/a?q=1&x=%41%42%43 now"),
                         "open [link to example.org — details withheld] now")
        self.assertEqual(redact("posted at https://example.org"), "posted at [link to example.org]")
        self.assertEqual(redact("x <script>alert(1)</script> y"), "x [markup withheld] y")
        self.assertIn("[secret withheld]", redact("password: hunter2abc9"))
        self.assertEqual(redact("ran `index.html` fine"), "ran `index.html` fine")
        self.assertIn("[command withheld]", redact("`ls -la | grep secret`"))
        o = redact_obj({"id": "https://a.b/c?d=1", "text": "see https://a.b/c?d=1"})
        self.assertEqual(o["id"], "https://a.b/c?d=1")
        self.assertEqual(o["text"], "see [link to a.b — details withheld]")


if __name__ == "__main__":
    unittest.main()
