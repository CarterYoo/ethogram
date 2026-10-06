"""Convert the synthetic forum (raw/) into the swarmgraph format (dataset/). This is the kind of small script an
agent writes after reading SKILL.md: map fields, and turn the source's own addressing convention into `to`."""
import json
import os
import re
from datetime import datetime, timezone

here = os.path.dirname(os.path.abspath(__file__))
out = os.path.join(here, "dataset")
os.makedirs(out, exist_ok=True)
iso = lambda u: datetime.fromtimestamp(u, timezone.utc).isoformat()
read = lambda n: [json.loads(line) for line in open(os.path.join(here, "raw", n))]

events, actors = [], {}
for p in read("posts.jsonl"):
    to = re.findall(r"TO:([\w/-]+)", p["subject"])  # this source's convention for recipients
    events.append({"id": p["post_id"], "ts": iso(p["unix"]), "actor": p["handle"], "kind": "message", "to": to,
                   "text": f"{p['subject']}\n{p['body']}", "channel": "forum"})
for i, r in enumerate(read("orchestrator.jsonl")):
    eid = f"o{i:03d}"
    if r["type"] in ("spawn", "successor"):
        events.append({"id": eid, "ts": iso(r["unix"]), "actor": r["parent"], "kind": "call", "to": [r["child"]],
                       "text": f"{r['type']}: {r['task']}", "channel": "orchestrator"})
        actors[r["child"]] = {"id": r["child"], "parent": r["parent"], "lineage": "coordinator" if r["type"] == "successor" else None}
    elif r["type"] == "result":
        events.append({"id": eid, "ts": iso(r["unix"]), "actor": r["child"], "kind": "return", "to": [r["parent"]],
                       "text": r["summary"], "channel": "orchestrator"})
    elif r["type"] == "self_status":
        events.append({"id": eid, "ts": iso(r["unix"]), "actor": r["agent"], "kind": "self_report", "text": r["summary"]})
actors["lead-1"] = {"id": "lead-1", "role": "coordinator", "lineage": "coordinator"}
actors["lead-2"]["role"] = "coordinator"
for a in ("writer-a", "writer-b"):
    actors[a] = {"id": a, "role": "writer"}
actors["checker"] = {"id": "checker", "role": "fact checker"}
actors["archivist"] = {"id": "archivist", "role": "archivist"}

with open(os.path.join(out, "events.jsonl"), "w") as f:
    f.writelines(json.dumps(e) + "\n" for e in events)
with open(os.path.join(out, "actors.jsonl"), "w") as f:
    f.writelines(json.dumps(a) + "\n" for a in actors.values())
with open(os.path.join(out, "dataset.json"), "w") as f:
    json.dump({"name": "synthetic research forum", "description": "Invented test data: forum posts addressed via "
               "'TO:<name>' in subjects, plus an orchestrator log of subagent spawns, results and a successor hand-off."}, f)
print(f"{len(events)} events, {len(actors)} actors → {out}")

