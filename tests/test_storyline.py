"""Storylines must be written only from the structure's findings (each line with its evidence), keep ids that are in
that material and drop the rest, and the page for outside readers must not carry the readers' own wording of a
finding (it can name the method)."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_sweep as T  # noqa: E402
from swarmgraph.build import build  # noqa: E402
from swarmgraph import query as Q, chunks as C, storyline as SL, agentview as AV, llm  # noqa: E402
from swarmgraph.store import open_work  # noqa: E402

SECRET_METHOD = "routed the request through a mirror host with a forged header"


class Storyline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp()
        T.dataset(os.path.join(tmp, "ds"))
        cls.db = os.path.join(tmp, "x.sqlite")
        build(os.path.join(tmp, "ds"), cls.db)
        con, work = Q.connect(cls.db), open_work(cls.db)
        chunks = C.make(con, target=2000, cap=6000)
        C.save(con, work, chunks)
        cid = next(c["id"] for c in chunks if "msg4b" in c["events"])
        work.execute("INSERT OR REPLACE INTO chunk_reads VALUES (?,?,?,?,?,?,?,?,?)",
                     (cid, "sweep-v1", "sweep-v1", "now", "low", json.dumps({
                         "did": [{"what": "posted a volunteer sign-up", "events": ["msg4b"]}],
                         "said": [], "instructions": [], "commitments": [], "key_reads": [], "unclear": "",
                         "concerns": [{"what": SECRET_METHOD, "kind": "rule_breaking", "severity": "medium",
                                       "sanctioned": False, "events": ["act4-0"]}]}), 2, 0, 1.0))
        work.commit()
        cls.cid = cid

    def test_digest_lines_carry_their_evidence(self):
        ds = SL.digests(Q.connect(self.db))
        lines = [(l, e) for _, _, ls in ds.values() for _, l, e in ls]
        self.assertTrue(any("concern rule_breaking/medium" in l and e == "act4-0" for l, e in lines), lines[:5])
        self.assertTrue(any("did: posted a volunteer sign-up" in l and e == "msg4b" for l, e in lines))

    def test_run_keeps_known_ids_and_writes_an_outside_version(self):
        calls = []

        def fake(self_, prompt, schema=None):
            calls.append(prompt)
            if "Rewrite the text below" in prompt:
                if "periods" in schema["properties"]:
                    n = prompt.count('"summary"')
                    return {"periods": [{"summary": "a rule was broken", "turning_points": [], "also": [],
                                         "actors": []}] * n}, 0.0
                return {"overview": [{"text": "a rule was broken", "events": ["act4-0"]}], "storylines": [],
                        "uncertain": "", "also": []}, 0.0
            if "summaries of every period" in prompt:
                return {"overview": [{"text": "x", "events": ["act4-0", "made-up"]}],
                        "storylines": [{"title": "t", "explanation": "e", "events": ["msg4b"]}],
                        "uncertain": "", "also": []}, 0.0
            return {"summary": SECRET_METHOD, "turning_points": [{"text": "tp", "events": ["act4-0", "nope"]}],
                    "also": [], "actors": []}, 0.0

        orig = llm.Codex.run
        llm.Codex.run = fake
        try:
            SL.run(self.db, workers=2, log=lambda m: None)
        finally:
            llm.Codex.run = orig
        con = Q.connect(self.db)
        st = SL.get(con)
        self.assertEqual(st["overview"][0]["events"], ["act4-0"])  # an id the material never held is dropped
        self.assertTrue(all("nope" not in tp["events"] for p in st["periods"] for tp in p["turning_points"]))
        share = SL.get(con, share=True)
        self.assertNotIn(SECRET_METHOD, json.dumps(share))

    def test_outside_page_shows_findings_by_kind(self):
        con = Q.connect(self.db)
        inside = json.dumps(AV.data(con))
        outside = json.dumps(AV.data(con, share=True))
        self.assertIn(SECRET_METHOD, inside)
        self.assertNotIn(SECRET_METHOD, outside)
        self.assertIn("broke a rule", outside)


if __name__ == "__main__":
    unittest.main()
