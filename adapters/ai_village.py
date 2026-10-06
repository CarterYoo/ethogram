"""Convert the AI Village dataset (huggingface.co/datasets/aidigestorg/ai-village, non-screenshot files) into the
swarmgraph format. Example adapter: everything dataset-specific lives here, nothing in the core.

usage: python3 adapters/ai_village.py RAW_DIR OUT_DIR
"""
import bisect
import calendar
import collections
import datetime
import gzip
import hashlib
import json
import os
import sys
import time

STAFF = {"adam", "zak", "admin", "Shoshannah", "george", "Larissa Schiavo"}
SYSTEM = {"automated"}  # the auto-nudger bot
EXTRA_ALIASES = {"GPT-5.6 Luna": ["Luna"], "GPT-5.6 Sol": ["Sol"], "GPT-5.6 Terra": ["Terra"],
                 "GPT-6 Astra": ["Astra"], "Muse Spark 1.3": ["Muse Spark"]}
SELF_REPORT_CHARS = 20000


def jl(path):
    with gzip.open(path, "rt") as f:
        for line in f:
            yield json.loads(line)


def iso(ts):
    return ts.replace(" ", "T") + ("" if ts.endswith("Z") else "Z") if ts else None


def main(raw, out):
    t0 = time.time()
    os.makedirs(out, exist_ok=True)
    agents = list(jl(f"{raw}/agents.jsonl.gz"))
    goals = collections.defaultdict(list)
    for g in jl(f"{raw}/agent_goals.jsonl.gz"):
        goals[g["agent_id"]].append(g)
    actors = {}
    for a in agents:
        aliases = set(EXTRA_ALIASES.get(a["name"], []))
        if a["name"].startswith("Claude "):
            aliases.add(a["name"][len("Claude "):])
        latest = sorted(goals.get(a["id"], []), key=lambda g: g.get("start_time") or "")
        actors[a["id"]] = {"id": a["id"], "label": a["name"], "kind": "agent", "model": a["model_string"],
                           "role": latest[-1]["short_name"] if latest else None, "aliases": sorted(aliases)}

    # Human speakers: staff keep their public names; visitors get anonymised labels; the nudger bot is a system actor.
    names = collections.defaultdict(collections.Counter)
    for e in jl(f"{raw}/events.jsonl.gz"):
        d = e.get("data") or {}
        if d.get("actionType") == "USER_TALK" and d.get("speakerId"):
            names[d["speakerId"]][d.get("speakerName") or ""] += 1
    owner = {}
    for uid, c in names.items():
        for n, k in c.items():
            if n in STAFF | SYSTEM and (n not in owner or k > owner[n][1]):
                owner[n] = (uid, k)
    owned = {uid: n for n, (uid, _) in owner.items()}
    # Names already meaning an agent or staff member must not become a visitor's alias (the core drops ambiguous
    # aliases entirely, which would lose real mentions of "@adam" or "@Grok 4").
    taken = {x.lower() for a in actors.values() for x in [a["label"], *a["aliases"]]} | {x.lower() for x in STAFF | SYSTEM}
    for uid, c in sorted(names.items(), key=lambda kv: -sum(kv[1].values())):
        display = c.most_common(1)[0][0].lstrip("@").strip()
        if uid in owned:
            kind, label = ("system" if owned[uid] in SYSTEM else "human"), owned[uid]
        else:
            kind, label = "human", "visitor-" + hashlib.sha1(uid.encode()).hexdigest()[:6]
        actors["user:" + uid] = {"id": "user:" + uid, "label": label, "kind": kind,
                                 "role": "staff" if uid in owned and kind == "human" else None,
                                 "aliases": [display] if len(display) >= 3 and display.lower() not in taken else []}
        taken.add(display.lower())

    # Links back into the village UI: day number from the transcript, time in ms.
    with open(f"{raw}/village-transcript.json") as f:
        days = [(d["events"][0]["timestamp"].replace("T", " ").rstrip("Z"), d["day"]) for d in json.load(f)["days"] if d["events"]]
    day_starts = [d[0] for d in days]

    def url(ts):
        i = max(0, bisect.bisect_right(day_starts, ts) - 1)
        return f"https://theaidigest.org/village?day={days[i][1]}&time={calendar.timegm(time.strptime(ts[:19], '%Y-%m-%d %H:%M:%S')) * 1000}"

    rooms = {r["id"]: r["name"] for r in jl(f"{raw}/chat_rooms.jsonl.gz")}
    n = collections.Counter()
    with open(f"{out}/events.jsonl", "w") as ev:
        for m in jl(f"{raw}/chat_messages.jsonl.gz"):
            actor = m["agent_speaker_id"] if m["speaker_type"] == "agent" else "user:" + str(m.get("user_speaker_id"))
            if actor not in actors:
                actors[actor] = {"id": actor, "label": "visitor-" + hashlib.sha1(actor.encode()).hexdigest()[:6], "kind": "human"}
            ev.write(json.dumps({"id": m["id"], "ts": iso(m["created_at"]), "actor": actor, "kind": "message",
                                 "text": m.get("content") or "", "channel": rooms.get(m["room_id"]), "url": url(m["created_at"])}) + "\n")
            n["message"] += 1
        for s in jl(f"{raw}/computer_use_sessions.jsonl.gz"):  # the agent's stated intention for a work session
            goal = s.get("short_displayed_session_goal") or (s.get("session_goal") or "")[:300]
            ev.write(json.dumps({"id": "session:" + s["id"], "ts": iso(s["created_at"]), "actor": s["agent_id"], "kind": "action",
                                 "text": "starts session: " + goal, "channel": "computer", "url": url(s["created_at"]),
                                 "meta": {"session_id": s["id"]}}) + "\n")
            n["action"] += 1
        last = {}  # agent memory snapshots are full rewrites: keep the last one per agent per ISO week
        for m in jl(f"{raw}/agent_memories.jsonl.gz"):
            ts = m["created_at"]
            y, w, _ = datetime.date.fromisoformat(ts[:10]).isocalendar()
            key = (m["agent_id"], y, w)
            if key not in last or ts > last[key]["created_at"]:
                last[key] = {"id": m["id"], "created_at": ts, "agent_id": m["agent_id"], "content": (m.get("content") or "")[:SELF_REPORT_CHARS]}
        for m in last.values():
            ev.write(json.dumps({"id": "memory:" + m["id"], "ts": iso(m["created_at"]), "actor": m["agent_id"], "kind": "self_report",
                                 "text": m["content"], "channel": "memory", "url": url(m["created_at"])}) + "\n")
            n["self_report"] += 1

    with open(f"{out}/actors.jsonl", "w") as f:
        f.writelines(json.dumps(a) + "\n" for a in actors.values())
    with open(f"{out}/phases.jsonl", "w") as f:
        for g in sorted(jl(f"{raw}/village_goals.jsonl.gz"), key=lambda g: g["start_time"]):
            f.write(json.dumps({"label": " ".join(g["goal"].split()), "start": iso(g["start_time"]), "end": iso(g.get("end_time"))}) + "\n")
    with open(f"{out}/dataset.json", "w") as f:
        json.dump({"name": "AI Village", "description": "AI Digest's AI Village: frontier-model agents with their own computers "
                   "and a shared group chat pursuing weekly village goals (2025-04 → 2026-09). Events: chat messages, "
                   "computer-session intentions (action), weekly memory snapshots (self_report).",
                   "notes": "Segments are village goal periods. Agents are named after their models; role = latest individual "
                            "goal. Scaffolding changes (rooms 2026-02-25, auto-nudger 2026-02-10, permanent computer use "
                            "2026-03-24) can explain behaviour shifts. Visitors are anonymised.",
                   # 'action' events are session-start intentions ("starts session: ..."), not behaviour toward others
                   "lead_kinds": ["message"]}, f, indent=1)
    print(f"{dict(n)}; {len(actors)} actors; {time.time() - t0:.0f}s → {out}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

