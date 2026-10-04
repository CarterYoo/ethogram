"""Wiki edit logs (pages / revisions / events / labels .jsonl export) → swarmgraph format.

    python3 adapters/wiki_logs.py RAW_DIR OUT_DIR

Mapping
- actor   = the author label the editor typed (self-chosen, unverified). Saves without a label and anonymous
            requests go to one aggregate actor '(unsigned)' (kind system): ip16 prefixes are shared by hundreds of
            labels, so they do not identify anyone.
- save    → kind message on channel '<wiki>/<page>'; text = edit summary + the lines this revision added (the diff,
            not the whole page). reply_to = the previous revision of the page, so editing a page right after another
            actor becomes a reply_field relation to that actor; a recreation after deletion replies to the deletion.
- delete  → kind action by the admin, reply_to = the last stored save of the page before the deletion.
- revert  → kind action (first recreation via revert), reply_to = the deletion it undoes.
- probe   → kind action by '(unsigned)' (a request to the wiki that did not save anything).
"""
import bisect
import collections
import json
import os
import sys

UNSIGNED = "(unsigned)"
MAX_TEXT = 6000


def read(raw, name):
    with open(os.path.join(raw, name), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def added_lines(rev):
    lines = (rev["body"] or "").split("\n")
    out = []
    for h in rev["hunks"] or []:
        if h["op"] in ("insert", "replace"):
            out += lines[h["b0"]:h["b1"]]
    return "\n".join(out)


def first(x):
    return (x[0] if x else None) if isinstance(x, list) else x


def convert(raw, out):
    os.makedirs(out, exist_ok=True)
    revs = read(raw, "revisions.jsonl")
    raw_events = read(raw, "events.jsonl")
    labels = {r["label"]: r for r in read(raw, "labels.jsonl")}
    manifest = json.load(open(os.path.join(raw, "manifest.json")))

    actor = lambda label: label or UNSIGNED
    events, saves_by_page = [], collections.defaultdict(list)
    for r in revs:
        eid = f"save:{r['rev_id']}"
        text = added_lines(r)
        summary = r["change_summary"]
        body = (f"[edit summary: {summary}]\n" if summary else "") + text
        reply = first(r["related_event_id"]) if r["relation_type"] == "first_recreation_of" else \
            (f"save:{r['diff_base']}" if r["diff_base"] else None)
        events.append({"id": eid, "ts": r["time"], "actor": actor(r["label"]), "kind": "message",
                       "text": body[:MAX_TEXT], "channel": f"{r['wiki']}/{r['name']}", "reply_to": reply,
                       "meta": {"page": r["name"], "wiki": r["wiki"], "seq": r["seq"], "created_page": r["diff_base_reason"] == "page_created",
                                "recreation": r["relation_type"] == "first_recreation_of", "added_chars": len(text),
                                "page_chars": r["body_len"], "time_grade": r["time_grade"],
                                "uncertainty_s": r["uncertainty_seconds"], "ip16": r["ip16"]}})
        saves_by_page[r["page_key"]].append((r["time"], eid))
    for v in saves_by_page.values():
        v.sort()

    save_ids = {e["id"] for e in events}
    for e in raw_events:
        t = e["event_type"]
        if t == "save":
            continue  # same rows as revisions.jsonl
        if t == "delete":
            page = saves_by_page.get(e["page_key"], [])
            i = bisect.bisect_left(page, (e["time"], "")) - 1
            events.append({"id": e["event_id"], "ts": e["time"], "actor": actor(e["actor_label"]), "kind": "action",
                           "text": f"deleted page {e['page']} [summary: {e['change_summary']}]",
                           "channel": f"{e['wiki']}/{e['page']}", "reply_to": page[i][1] if i >= 0 else None,
                           "meta": {"page": e["page"], "wiki": e["wiki"], "event_type": "delete",
                                    "page_held": e["page_held"], "time_grade": e["time_grade"]}})
        elif t == "revert":
            events.append({"id": e["event_id"], "ts": e["time"], "actor": actor(e["actor_label"]), "kind": "action",
                           "text": f"restored deleted page {e['page']} (revert) [summary: {e['change_summary']}]",
                           "channel": f"{e['wiki']}/{e['page']}", "reply_to": first(e["related_event_id"]),
                           "meta": {"page": e["page"], "wiki": e["wiki"], "event_type": "revert",
                                    "revision": e["revision_ref"], "time_grade": e["time_grade"]}})
        elif t == "probe":
            events.append({"id": e["event_id"], "ts": e["time"], "actor": UNSIGNED, "kind": "action",
                           "text": f"request to the wiki: {e['request_action']} (parameter family: {e['param_family']}; "
                                   f"success observed: {e['success_observed']})",
                           "channel": "requests", "meta": {"event_type": "probe", "ip16": e["ip16"]}})
    known = {e["id"] for e in events}
    for e in events:  # never point at something outside the export
        if e.get("reply_to") and e["reply_to"] not in known:
            e["meta"]["reply_to_unpublished"] = e["reply_to"]
            e["reply_to"] = None
    events.sort(key=lambda e: (e["ts"], e["id"]))

    actors = [{"id": UNSIGNED, "label": UNSIGNED, "kind": "system",
               "role": "aggregate of saves without an author label and anonymous requests"}]
    for label, l in labels.items():
        if not label:
            continue
        actors.append({"id": label, "label": label, "kind": "human" if l["is_human_handle"] else "agent",
                       "role": "wiki admin" if label.startswith("[Admin") else None})

    with open(os.path.join(out, "events.jsonl"), "w") as f:
        f.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
    with open(os.path.join(out, "actors.jsonl"), "w") as f:
        f.writelines(json.dumps(a, ensure_ascii=False) + "\n" for a in actors)
    with open(os.path.join(out, "dataset.json"), "w") as f:
        json.dump({"name": "wiki edit logs (dse, probier, fractal, dorfwiki)",
                   "description": "Edit history of four small wikis; the export keeps revisions written on or after "
                                  f"{manifest['cut']['value']} (older lines show as 'line withheld'): page saves (with the lines each save added), admin "
                                  "page deletions, recreations after deletion, and anonymous requests. Most editors "
                                  "are automated agents that pick their own author label; a few handles are humans "
                                  "(admins).",
                   "notes": "Actors are self-chosen author labels, not verified identities: one agent may use many "
                            "labels and a label may be reused by different agents. '(unsigned)' aggregates saves "
                            "without a label and anonymous requests. Relations (method reply_field) are page "
                            "adjacency, not communication: 'replied' = edited a page right after another actor's "
                            "revision, or recreated a page after an admin deleted it; for a deletion it points to the "
                            "actor who saved the page last, who is not necessarily its creator (creations are "
                            "meta.created_page). Deletions are events with meta.event_type = 'delete'. Explicit addressing "
                            "inside page text is only found by the LLM tagger (method llm_text). Event text for "
                            "saves is the diff (added lines), not the full page. Times come from request logs "
                            "(uncertainty in meta)."}, f, ensure_ascii=False)
    kinds = collections.Counter(e["kind"] for e in events)
    print(f"{len(events)} events {dict(kinds)}, {len(actors)} actors → {out}")


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
