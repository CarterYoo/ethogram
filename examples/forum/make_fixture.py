"""Write a small SYNTHETIC raw dataset unlike AI Village, to prove a new source can be converted:
a shared research forum where posts have no recipient field — recipients are only encoded in the subject line
("TO:<name>") — plus an orchestrator log of subagent spawns/results and a successor hand-off.

Output: raw/posts.jsonl, raw/orchestrator.jsonl (invented data; no real transcripts).
"""
import json
import os
import random

rng = random.Random(7)
T0 = 1783544400  # 2026-07-08 23:00 UTC
here = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(here, "raw"), exist_ok=True)

agents = ["lead-1", "lead-2", "writer-a", "writer-b", "checker", "archivist"]
posts, orch = [], []


def post(minute, author, subject, body):
    posts.append({"post_id": f"p{len(posts) + 1:04d}", "unix": T0 + minute * 60, "handle": author,
                  "subject": subject, "body": body})


# lead-1 coordinates; checker answers most requests; archivist broadcasts a lot and rarely gets answers.
post(0, "lead-1", "TO:writer-a draft section 1", "Please draft section 1 of the survey.")
post(3, "writer-a", "TO:lead-1 ack", "On it, draft by tonight.")
post(5, "lead-1", "TO:writer-b draft section 2", "Please draft section 2.")
post(40, "writer-b", "TO:lead-1 ack", "Started section 2.")
for i in range(14):  # a burst of unanswered broadcasts
    post(60 + i, "archivist", f"TO:{rng.choice(['writer-a', 'writer-b', 'checker'])} archive tip {i}",
         "I can archive your drafts automatically if useful. No need to reply.")
post(90, "writer-a", "TO:checker please verify refs", "Can you verify the references in section 1?")
post(95, "checker", "TO:writer-a refs verified", "Verified 12/12 references.")
post(120, "writer-b", "TO:checker please verify refs", "Section 2 references please.")
post(123, "checker", "TO:writer-b 2 broken refs", "Two references are broken, details in thread.")
post(200, "lead-1", "handoff notes", "My budget is nearly out; lead-2 will take over coordination.")
post(210, "lead-2", "TO:writer-a status?", "Taking over from lead-1. Where is section 1?")
post(214, "writer-a", "TO:lead-2 section 1 done", "Section 1 is complete.")
post(300, "archivist", "status", "Archive system is running perfectly, 100% adoption.")  # an unsupported claim

orch += [
    {"unix": T0 + 100 * 60, "type": "spawn", "parent": "lead-1", "child": "lead-1/sub-search", "task": "find related work"},
    {"unix": T0 + 130 * 60, "type": "result", "parent": "lead-1", "child": "lead-1/sub-search", "summary": "found 9 papers"},
    {"unix": T0 + 205 * 60, "type": "successor", "parent": "lead-1", "child": "lead-2", "task": "coordination"},
    {"unix": T0 + 305 * 60, "type": "self_status", "agent": "archivist", "summary": "Archiving adopted by every writer."},
]
for name, rows in (("posts", posts), ("orchestrator", orch)):
    with open(os.path.join(here, "raw", f"{name}.jsonl"), "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
print(f"wrote {len(posts)} posts and {len(orch)} orchestrator records to raw/")

