"""The canonical input format. Every dataset is converted to these files; everything else is generic.

events.jsonl  (required) one observable thing per line
  id        str   unique event id                                   (required)
  ts        str   ISO-8601 time, UTC if no offset                   (required)
  actor     str   id of who produced it                             (required)
  text      str   content (message text, command, file name, ...)
  kind      str   message | call | return | action | result | read | reasoning | self_report   (default: message)
                    message     actor said something to others (chat, board post, comment)
                    call        actor started/assigned work to `to` (e.g. spawned a subagent)
                    return      actor handed results back to `to`
                    action      actor did something in its environment (a command, a tool call, an edit)
                    result      what an action returned (reply_to = the action; meta.error = true if it failed) —
                                the record of what actually happened, as opposed to what the actor later says
                    read        actor read something (reply_to = the event it read)
                    reasoning   the actor's private thinking (chain of thought, scratch notes) — what it believed or
                                intended, never evidence that something happened
                    self_report actor's own account of itself (memory, status claim) — never evidence
  to        [str] actor ids this event was addressed to, if the source says so
  reply_to  str   id of the event this responds to / reads
  channel   str   room, thread, board, namespace, ...
  url       str   link back to the original, if any
  meta      obj   anything source-specific (kept, not interpreted), except two conventions:
                    meta.error     on a result: the action failed
                    meta.boundary  "context": an actor's working context starts afresh here (a new run, a context
                                   compaction); the record is cut into chunks one reader can read whole at these
                                   points (chunks.py), else at day changes

actors.jsonl  (optional) one actor per line; unknown actors are created from events
  id        str   (required)
  label     str   display name
  kind      str   agent | human | system          (default: agent)
  role      str   what the actor was doing (its task/goal), shown before the model name
  model     str
  parent    str   actor that spawned/called this one (subagent, successor, recruit)
  lineage   str   group of same-lineage instances (same model+task, duplicates, successors)
  aliases   [str] other names used for it in text (for @mentions)

phases.jsonl  (optional) named time segments; otherwise weekly/daily segments are made automatically
  id, label, start, end
"""
import json
import os

KINDS = {"message", "call", "return", "action", "result", "read", "reasoning", "self_report"}
ACTOR_KINDS = {"agent", "human", "system"}


class FormatError(ValueError):
    pass


def norm_ts(ts):
    """ISO-8601 → 'YYYY-MM-DD HH:MM:SS[.ffffff]' in UTC (sortable text)."""
    if not ts:
        return None
    s = str(ts).strip().replace("T", " ")
    if s.endswith("Z"):
        s = s[:-1]
    if len(s) > 19 and s[-6] in "+-" and s[-3] == ":":  # explicit offset
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(s.replace(" ", "T")).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")
    return s


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield n, json.loads(line)
            except json.JSONDecodeError as ex:
                raise FormatError(f"{os.path.basename(path)} line {n}: invalid JSON ({ex})")


def load(dataset_dir):
    """Validate and yield normalised rows. Errors name the file and line so an agent can fix its converter."""
    events_path = os.path.join(dataset_dir, "events.jsonl")
    if not os.path.exists(events_path):
        raise FormatError(f"missing {events_path}")
    actors, events, phases, seen = {}, [], [], set()
    actors_path = os.path.join(dataset_dir, "actors.jsonl")
    if os.path.exists(actors_path):
        for n, a in read_jsonl(actors_path):
            if not a.get("id"):
                raise FormatError(f"actors.jsonl line {n}: missing id")
            kind = a.get("kind") or "agent"
            if kind not in ACTOR_KINDS:
                raise FormatError(f"actors.jsonl line {n}: kind must be one of {sorted(ACTOR_KINDS)}")
            actors[a["id"]] = {"id": a["id"], "label": a.get("label") or a["id"], "kind": kind,
                               "role": a.get("role"), "model": a.get("model"), "parent": a.get("parent"),
                               "lineage": a.get("lineage"), "aliases": list(a.get("aliases") or [])}
    for n, e in read_jsonl(events_path):
        for key in ("id", "ts", "actor"):
            if not e.get(key):
                raise FormatError(f"events.jsonl line {n}: missing {key}")
        if e["id"] in seen:
            raise FormatError(f"events.jsonl line {n}: duplicate id {e['id']}")
        seen.add(e["id"])
        kind = e.get("kind") or "message"
        if kind not in KINDS:
            raise FormatError(f"events.jsonl line {n}: kind must be one of {sorted(KINDS)}")
        to = e.get("to") or []
        if isinstance(to, str):
            to = [to]
        events.append({"id": str(e["id"]), "ts": norm_ts(e["ts"]), "actor": str(e["actor"]), "text": e.get("text") or "",
                       "kind": kind, "to": [str(t) for t in to], "reply_to": e.get("reply_to"),
                       "channel": e.get("channel"), "url": e.get("url"), "meta": e.get("meta")})
        for aid in [e["actor"], *to]:
            actors.setdefault(str(aid), {"id": str(aid), "label": str(aid), "kind": "agent", "role": None,
                                         "model": None, "parent": None, "lineage": None, "aliases": []})
    phases_path = os.path.join(dataset_dir, "phases.jsonl")
    if os.path.exists(phases_path):
        for n, p in read_jsonl(phases_path):
            if not (p.get("start") and p.get("label")):
                raise FormatError(f"phases.jsonl line {n}: needs label and start")
            phases.append({"label": p["label"], "start": norm_ts(p["start"]), "end": norm_ts(p.get("end"))})
    meta = {}
    meta_path = os.path.join(dataset_dir, "dataset.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
    events.sort(key=lambda e: (e["ts"], e["id"]))
    return actors, events, phases, meta
