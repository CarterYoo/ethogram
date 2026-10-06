"""Count what analysts said confused them in the prepared index, per A/B round, with a classifier that does not know
which round a comment came from (comments are pooled and shuffled).

  python3 eval/feedback_themes.py OUT_DIR_A OUT_DIR_B ...
"""
import collections
import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swarmgraph.llm import Codex  # noqa: E402

THEMES = {
    "overstated": "labels or summaries claim more than the events show (imply intent, an explicit stop order, or a "
                  "verified outcome where there is only context, a claim or a plan)",
    "missing_context": "summaries or cards leave out decisive context: later corrections or admissions, refusals, "
                       "approvals, what happened next, chronology",
    "no_type_filter": "could not select or count events by type or recorded field; text search mixed in mentions",
    "relation_meaning": "what a relation means or points to was unclear or misleading",
    "wrong_description": "the dataset description or overview was wrong or confusing",
    "too_large": "a tool returned too much or an unsynthesized dump",
    "other": "another problem with the index",
}
PROMPT = """Below are comments from analysts about a prepared index of an event log: what in it was confusing,
misleading or missing. For each comment, list every theme it raises (none if it raises no problem). Themes:
{themes}

Comments (id: text):
{comments}
"""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "themes"],
                               "properties": {"id": {"type": "string"},
                                              "themes": {"type": "array", "items": {"type": "string",
                                                                                    "enum": list(THEMES)}}}}}}}


def main(dirs):
    rows = []
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "*_swarmgraph_*.json"))):
            a = (json.load(open(f)).get("answer") or {})
            if a.get("index_feedback"):
                rows.append((d, os.path.basename(f)[:-5], " ".join(a["index_feedback"].split())))
    order = list(range(len(rows)))
    random.Random(5).shuffle(order)
    comments = "\n".join(f"c{i}: {rows[i][2]}" for i in order)
    out, _ = Codex(effort="high", timeout=1500).run(
        PROMPT.format(themes="\n".join(f"- {k}: {v}" for k, v in THEMES.items()), comments=comments), SCHEMA)
    got = {it["id"]: it["themes"] for it in out["items"]}
    rep = {}
    for d in dirs:
        idx = [i for i, r in enumerate(rows) if r[0] == d]
        c = collections.Counter(t for i in idx for t in set(got.get(f"c{i}", [])))
        rep[d] = {"comments": len(idx), **{t: c.get(t, 0) for t in THEMES},
                  "no_problem": sum(1 for i in idx if not got.get(f"c{i}"))}
    print(json.dumps(rep, indent=1))
    return rep


if __name__ == "__main__":
    main(sys.argv[1:])

