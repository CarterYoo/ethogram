"""Grounded LLM summary of one node (actor × segment). Claims must cite pack ids; citations are checked."""
import json
import time

from .llm import Agent
from .query import clip

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["role", "summary", "key_interactions", "claims", "open_questions"],
    "properties": {
        "role": {"type": "string"},
        "summary": {"type": "string"},
        "key_interactions": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                             "required": ["peer", "nature", "evidence"],
                             "properties": {"peer": {"type": "string"}, "nature": {"type": "string"},
                                            "evidence": {"type": "array", "items": {"type": "string"}}}}},
        "claims": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                   "required": ["text", "kind", "evidence"],
                   "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": ["observed", "self_report"]},
                                  "evidence": {"type": "array", "items": {"type": "string"}}}}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}

PROMPT = """You are indexing a multi-agent dataset ("{dataset}") for later hypothesis testing.
Summarise how {actor} behaved in segment "{segment}", using ONLY the evidence pack. Do not run commands.
Rules: every claim and interaction cites pack ids (e* = events, s1 = the actor's own self-report). kind="observed" only
if events by or about the actor show it; kind="self_report" if it rests on s1 or on the actor describing its own success.
Prefer concrete, checkable statements. 3-6 sentence summary, ≤8 claims, ≤5 interactions, ≤3 open questions.

=== EVIDENCE PACK ===
{pack}
"""


def _spread(rows, n):
    if len(rows) <= n:
        return list(rows)
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def evidence_pack(con, actor, segment, own=45, inbound=20):
    names = {r[0]: r[1] for r in con.execute("SELECT id, coalesce(role || ' (' || label || ')', label) FROM actors")}
    seg = con.execute("SELECT label FROM segments WHERE id=?", (segment,)).fetchone()[0]
    node = con.execute("SELECT summary, self_report FROM nodes WHERE actor=? AND segment_id=?", (actor, segment)).fetchone()
    if not node:
        raise ValueError(f"no activity for {actor} in segment {segment}")
    mine = con.execute("SELECT e.id, e.ts, e.actor, e.kind, e.text, EXISTS(SELECT 1 FROM relations r WHERE r.event_id=e.id) "
                       "FROM events e WHERE e.actor=? AND e.segment_id=? AND e.kind<>'self_report' ORDER BY e.ts",
                       (actor, segment)).fetchall()
    rel = [m for m in mine if m[5]]
    chosen = _spread(rel, own * 2 // 3)
    chosen += _spread([m for m in mine if not m[5]], own - len(chosen))
    inc = _spread(con.execute("SELECT e.id, e.ts, e.actor, e.kind, e.text FROM relations r JOIN events e ON e.id=r.event_id "
                              "WHERE r.dst=? AND r.segment_id=? AND r.type IN ('addressed','invoked','returned') "
                              "ORDER BY e.ts", (actor, segment)).fetchall(), inbound)
    rows = sorted({r[0]: r for r in [m[:5] for m in chosen] + list(inc)}.values(), key=lambda r: r[1])
    refs, lines = {}, [f"ACTOR: {names.get(actor, actor)}", "INDEX FACTS:", node[0], "",
                       f"EVENTS (sample of {len(rows)}; the actor produced {len(mine)} in this segment):"]
    for i, (eid, ts, who, kind, text) in enumerate(rows, 1):
        refs[f"e{i}"] = eid
        lines.append(f"[e{i}] {ts[:16]} {names.get(who, who)} ({kind}): {clip(text, 380)}")
    if node[1]:
        sr = con.execute("SELECT ts, text FROM events WHERE id=?", (node[1],)).fetchone()
        refs["s1"] = node[1]
        lines += ["", f"[s1] SELF-REPORT at {sr[0][:16]} (the actor's own claims, unverified):", clip(sr[1], 2500)]
    return "\n".join(lines), refs, names.get(actor, actor), seg


def ground(result, refs):
    for item in result.get("claims", []) + result.get("key_interactions", []):
        item["evidence"] = [e for e in item.get("evidence", []) if e in refs]
    ok = sum(1 for c in result.get("claims", []) if c["evidence"])
    return ok / max(1, len(result.get("claims", [])))


def summarize_node(con, work, actor, segment, effort="low", model=None):
    dataset = dict(con.execute("SELECT key, value FROM meta")).get("name", "dataset")
    pack, refs, name, seg = evidence_pack(con, actor, segment)
    result, secs = Agent(effort=effort, model=model).cached(work, PROMPT.format(dataset=dataset, actor=name, segment=seg,
                                                                                pack=pack), SCHEMA)
    grounded = ground(result, refs)
    work.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?,?,?,?,?)",
                 (actor, segment, time.strftime("%Y-%m-%d %H:%M:%S"), model or "default", json.dumps(refs),
                  json.dumps(result), grounded))
    work.commit()
    return {"actor": actor, "segment": segment, "seconds": round(secs, 1), "grounded": grounded, "refs": refs, **result}
