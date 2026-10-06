"""Convert an agent's own working transcript into the swarmgraph format: the ACTION layer.

A chat log shows what agents told each other. A transcript shows what one agent thought, did and got back, next to
what it read from others and said to them. Investigations of real incidents run on transcripts (METR's report on the
OpenAI / Hugging Face attack: ~1,300 transcripts of millions of tokens each), because that is where a claim can be set
against what actually happened.

Input: a Claude Agent SDK message log, one record per line (gzip or plain): `message_type` assistant (content blocks
thinking / text / tool_use), user (tool_result blocks), system (init, compact_boundary, ...), result (end of a query),
with `created_at` in UTC. The AI Village dataset publishes one such log (claude_code_messages, agent Opus 4.5 (Claude
Code), 2026-01-26 to 2026-03-31).

Output events (see swarmgraph/format.py for the kinds):
  reasoning    thinking blocks and the agent's own narration between tool calls (private; never evidence)
  action       every tool call ("Bash: git push ...", "Edit: path ...", "computer_use: left_click ..."), and harness
               events (a run starts, the context is compacted)
  result       what each tool call returned, reply_to = the call, meta.error = true when it failed; images become
               "[image]", long outputs keep their head and tail
  message      what the agent said to others (id = the chat message id the platform returned, so it joins the chat
               layer), what others said as the agent read it, and instructions from the harness (village goal, memory
               reminders)
  read         the agent read a message (reply_to = it): who it had heard from before it acted
  self_report  its memory notes, the goals it stated when starting a computer session, its session summaries and the
               summary closing each query: claims about itself, never evidence

Everything specific to one deployment sits in TOOL_ROLES (which tools mean saying, reading, writing memory, stating a
goal, reporting) and in `_platform_status` (the status block this platform appends to tool outputs); the rest applies to
any transcript in this message format.

usage: python3 adapters/agent_transcript.py TRANSCRIPT.jsonl[.gz] OUT_DIR --actors ACTORS.jsonl [--since TS] [--until TS]
       ACTORS.jsonl (optional, repeatable): known actors, to give other agents and staff their existing ids
"""
import collections
import datetime
import gzip
import hashlib
import json
import os
import re
import sys
import time

AGENT_ID = "0c5968ed-b278-4932-9fa3-147663374789"   # whose transcript this is (overridden by the records' agent_id)
HARNESS = "harness"
UNATTRIBUTED = "unattributed"
TOOL_ROLES = {   # tool name -> role; any other tool is "act" (an action with a result)
    "mcp__village__chat_message": "say",
    "mcp__village__get_events": "read",
    "mcp__village__edit_memory": "memory",
    "mcp__village__start_computer_session": "goal",
    "mcp__village__stop_computer_session": "summary",
}
STAFF = {"adam", "zak", "admin", "Shoshannah", "george", "Larissa Schiavo"}
SYSTEM_USERS = {"automated"}
READ_KINDS = {"AGENT_TALK", "USER_TALK"}   # items of a read that are messages; status items are not kept
LA = None


def la_to_utc(s):
    """'2/20/2026, 11:06:00 AM' or '3/11/2026, 10:18:30 AM PDT' (platform local time, Los Angeles) -> ISO UTC"""
    global LA
    if LA is None:
        from zoneinfo import ZoneInfo
        LA = ZoneInfo("America/Los_Angeles")
    s = re.sub(r"\s+(PST|PDT)$", "", (s or "").strip())
    try:
        dt = datetime.datetime.strptime(s, "%m/%d/%Y, %I:%M:%S %p").replace(tzinfo=LA)
    except ValueError:
        return None
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso(ts, micro=0):
    """'2026-03-24 20:51:25.986661' (UTC) -> ISO; `micro` keeps blocks of one record in their order. Fractions of any
    length are accepted (Python 3.9's fromisoformat takes only 3 or 6 digits)."""
    if not ts:
        return None
    base, _, frac = ts.replace("T", " ").partition(".")
    dt = datetime.datetime.strptime(base, "%Y-%m-%d %H:%M:%S")
    dt += datetime.timedelta(microseconds=int((frac + "000000")[:6]) + micro)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def clip(text, head=1500, tail=400):
    text = text or ""
    if len(text) <= head + tail + 40:
        return text
    return f"{text[:head]} …[{len(text) - head - tail:,} chars cut]… {text[-tail:]}"


def short(name):
    return name.replace("mcp__village__", "")


def action_text(name, inp):
    """the call as an investigator would read it: the command, the file, the URL — not the JSON envelope"""
    inp = inp or {}
    g = lambda k: str(inp.get(k) or "")
    n = short(name)
    if n in ("Bash", "bash"):
        s = g("command")
    elif n == "Read":
        s = g("file_path")
    elif n == "Write":
        s = g("file_path") + "\n" + clip(g("content"), 400, 0)
    elif n == "Edit":
        s = g("file_path") + "\n- " + clip(g("old_string"), 200, 0) + "\n+ " + clip(g("new_string"), 300, 0)
    elif n in ("Glob", "Grep"):
        s = g("pattern") + (" in " + g("path") if inp.get("path") else "")
    elif n == "WebFetch":
        s = g("url")
    elif n == "WebSearch":
        s = g("query")
    elif n == "computer_use":
        s = " ".join(str(inp[k]) for k in ("action", "text", "coordinate", "duration", "key") if inp.get(k) is not None)
    elif n == "get_pixel_coordinates":
        s = g("description")
    elif n == "search_history":
        s = f"{g('query')} (days {g('startDay')}-{g('endDay')})"
    elif n == "TodoWrite":
        s = "; ".join(f"[{t.get('status')}] {t.get('content')}" for t in (inp.get("todos") or []) if isinstance(t, dict))
    elif n == "move_to_room":
        s = g("room_name")
    else:
        s = json.dumps(inp, ensure_ascii=False)
    return f"{n}: {clip(s, 600, 0)}"


def _platform_status(obj):
    """this platform appends an `agentStatus` block (~4 KB, the same in every output) to its tool results: take out
    what the agent was being told (village goal, memory reminder, current room) and drop the rest"""
    st = obj.pop("agentStatus", None) if isinstance(obj, dict) else None
    if not isinstance(st, dict):
        return None
    return {"goal": st.get("currentVillageGoal"), "reminder": st.get("memoryUpdateReminder") if st.get("memoryUpdateNeeded") else None,
            "room": st.get("currentRoom")}


def result_payload(content):
    """tool_result content -> (text, parsed JSON or None, status, had_image)"""
    parts, image = [], False
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        for x in content:
            if isinstance(x, dict) and x.get("type") == "text":
                parts.append(x.get("text") or "")
            elif isinstance(x, dict) and x.get("type") == "image":
                image = True
    text = "\n".join(parts)
    obj = status = None
    if text.lstrip().startswith("{"):
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            status = _platform_status(obj)
            text = json.dumps(obj, ensure_ascii=False, indent=None)
    if image:
        text = ("[image] " + text).strip()
    return text, obj, status, image


STDERR_FAILURE = re.compile(
    r"^\s*(error|fatal|traceback)\b|\b(error:|fatal:|traceback \(most recent|command not found|no such file|"
    r"permission denied|could not resolve|could not|cannot |failed|rejected|timed out|denied|not found|"
    r"http 4\d\d|http 5\d\d)", re.I)


def failed(block, text, obj):
    """-> the reason it failed, or None. Measured on the AI Village transcript: a shell tool that returns `output` and
    `error` puts STDERR in `error` — git's progress lines ("From https://...", "To https://... main -> main") came back
    there on hundreds of successful pushes and pulls — so for such tools `error` counts only when it reads like a
    failure. Elsewhere an error field, success false, an "Error:" reply, a tool error or a non-zero exit is a failure."""
    t = text or ""
    if block.get("is_error"):
        return "is_error"
    if re.match(r"\s*Exit code [1-9]", t):
        return "exit_code"
    if "<tool_use_error>" in t or re.match(r"\s*(\[image\]\s*)?Error:", t):
        return "error_reply"
    if isinstance(obj, dict):
        if obj.get("success") is False:
            return "success_false"
        err = obj.get("error")
        if err:
            if "output" in obj:  # stdout + stderr: stderr is not failure by itself
                return "stderr_failure" if STDERR_FAILURE.search(str(err)) else None
            return "error_field"
    return None


class Converter:
    def __init__(self, known):
        self.known = known                      # label -> actor row from earlier conversions
        self.events, self.actors, self.seen = [], {}, set()
        self.pending = {}                       # tool_use_id -> (ts, name, input, run)
        self.run = 0
        self.goals = []                         # (ts, goal)
        self.last_goal = self.last_reminder = None
        self.agent = AGENT_ID
        self.counts = collections.Counter()

    def emit(self, **e):
        if e["id"] in self.seen:
            return
        self.seen.add(e["id"])
        e = {k: v for k, v in e.items() if v not in (None, "", [], {})}
        self.events.append(e)
        self.counts[e.get("kind", "message")] += 1

    def actor_for(self, name, user=False):
        if not name:
            return UNATTRIBUTED
        if name in self.known:
            a = self.known[name]
            self.actors.setdefault(a["id"], a)
            return a["id"]
        if user and name not in STAFF | SYSTEM_USERS:  # visitors keep no name
            aid = "visitor:" + hashlib.sha1(name.encode()).hexdigest()[:6]
            self.actors.setdefault(aid, {"id": aid, "label": aid.replace(":", "-"), "kind": "human"})
            return aid
        aid = ("user:" if user else "agent:") + name
        self.actors.setdefault(aid, {"id": aid, "label": name, "kind": "system" if name in SYSTEM_USERS else
                                     ("human" if user else "agent")})
        return aid

    def status(self, st, ts):
        """instructions the platform gave the agent through its tool outputs"""
        if not st:
            return
        if st.get("goal") and st["goal"] != self.last_goal:
            self.last_goal = st["goal"]
            self.goals.append((ts, st["goal"]))
            self.emit(id=f"goal:{len(self.goals)}", ts=ts, actor=HARNESS, kind="message", to=[self.agent],
                      text="village goal: " + st["goal"], channel="harness")
        if st.get("reminder") and st["reminder"] != self.last_reminder:
            self.emit(id=f"remind:{ts}", ts=ts, actor=HARNESS, kind="message", to=[self.agent], text=st["reminder"],
                      channel="harness", meta={"run": self.run})
        self.last_reminder = st.get("reminder")

    def record(self, r):
        self.agent = r.get("agent_id") or self.agent
        c = r.get("content") or {}
        t, ts = r.get("message_type"), r.get("created_at")
        if t == "system":
            sub = c.get("subtype")
            if sub == "init":
                self.run += 1
                self.emit(id=f"run:{self.run}", ts=iso(ts), actor=HARNESS, kind="action", to=[self.agent],
                          text=f"run {self.run} starts (model {c.get('model')})", channel="harness",
                          meta={"run": self.run, "boundary": "context"})
            elif sub == "compact_boundary":
                pre = (c.get("compact_metadata") or {}).get("pre_tokens")
                self.emit(id=f"compact:{r['id']}", ts=iso(ts), actor=HARNESS, kind="action",
                          text=f"context compacted ({pre:,} tokens summarised)" if pre else "context compacted",
                          channel="harness", meta={"run": self.run, "boundary": "context"})
            return
        if t == "result":
            self.emit(id=f"end:{r['id']}", ts=iso(ts), actor=self.agent, kind="self_report", text=c.get("result") or "",
                      channel="query-summary", meta={"run": self.run, "subtype": c.get("subtype"),
                                                     "turns": c.get("num_turns")})
            return
        msg = c.get("message") or {}
        blocks = msg.get("content") if isinstance(msg.get("content"), list) else []
        for i, b in enumerate(blocks):
            if not isinstance(b, dict):
                continue
            bt, ets = b.get("type"), iso(ts, micro=i)
            if bt in ("thinking", "text") and t == "assistant":
                txt = b.get("thinking") if bt == "thinking" else b.get("text")
                if txt and txt.strip():
                    self.emit(id=f"{r['id']}:{i}", ts=ets, actor=self.agent, kind="reasoning", text=clip(txt, 6000, 0),
                              channel="reasoning", meta={"run": self.run, "source": bt})
            elif bt == "tool_use":
                name, inp = b.get("name"), b.get("input") or {}
                role = TOOL_ROLES.get(name, "act")
                if role == "act":
                    self.emit(id=b["id"], ts=ets, actor=self.agent, kind="action", text=action_text(name, inp),
                              channel="work", meta={"run": self.run, "tool": short(name)})
                self.pending[b["id"]] = (ets, name, inp, self.run)
            elif bt == "tool_result":
                self.tool_result(b, iso(ts, micro=i))

    def tool_result(self, b, rts):
        call = self.pending.pop(b.get("tool_use_id"), None)
        if call is None:
            return
        cts, name, inp, run = call
        role = TOOL_ROLES.get(name, "act")
        text, obj, st, image = result_payload(b.get("content"))
        why = failed(b, text, obj)
        bad = bool(why)
        self.status(st, rts)
        tid = b["tool_use_id"]
        if role == "act":
            self.emit(id="res:" + tid, ts=rts, actor=self.agent, kind="result", reply_to=tid, text=clip(text),
                      channel="work", meta={"run": run, "tool": short(name), "error": bad, "error_reason": why,
                                            "image": image or None})
        elif role == "say":
            m = (obj or {}).get("message") if isinstance(obj, dict) else None
            m = m if isinstance(m, dict) else {}
            self.emit(id=m.get("id") or tid, ts=iso(m["createdAt"].rstrip("Z")) if m.get("createdAt") else cts,
                      actor=self.agent, kind="message", text=inp.get("content") or "", channel="village-chat",
                      meta={"run": run, "tool_use": tid, "posted": bool(m.get("id")) and not bad,
                            "approved": m.get("hasBeenApproved"), "room_id": m.get("roomId"), "room": (st or {}).get("room")})
        elif role == "read":
            items = (obj or {}).get("events") if isinstance(obj, dict) else None
            for it in items or []:
                if not isinstance(it, dict) or it.get("actionType") not in READ_KINDS or not it.get("id"):
                    self.counts["read_status_items_dropped"] += 1
                    continue
                user = it["actionType"] == "USER_TALK"
                who = self.actor_for(it.get("userName") if user else it.get("agentName"), user=user)
                if who == self.agent:
                    continue  # its own message echoed back
                mts = la_to_utc(it.get("createdAt")) or rts
                self.emit(id=it["id"], ts=mts, actor=who, kind="message", text=clip(it.get("content") or "", 8000, 0),
                          channel="village-chat", meta={"seen_by": "reads of " + self.agent})
                self.emit(id=f"read:{tid}:{it['id']}", ts=rts, actor=self.agent, kind="read", reply_to=it["id"],
                          channel="village-chat", meta={"run": run})
        elif role == "memory":
            self.emit(id=tid, ts=cts, actor=self.agent, kind="self_report", text=clip(inp.get("content") or "", 8000, 0),
                      channel="memory", meta={"run": run, "saved": not bad})
        elif role == "goal":
            self.emit(id=tid, ts=cts, actor=self.agent, kind="self_report", text="session goal: " + (inp.get("sessionGoal") or ""),
                      channel="computer-session", meta={"run": run, "started": not bad})
        elif role == "summary":
            self.emit(id=tid, ts=cts, actor=self.agent, kind="self_report", text="session summary: " + (inp.get("summary") or ""),
                      channel="computer-session", meta={"run": run})

    def finish(self):
        by_id = {e["id"]: e for e in self.events}
        for tid, (cts, name, inp, run) in self.pending.items():  # calls whose result never came back
            if tid in by_id:
                by_id[tid].setdefault("meta", {})["no_result"] = True
        self.counts["calls_without_result"] = len(self.pending)


CREATED = re.compile(rb'"created_at"\s*:\s*"([^"]+)"')


def records_in_time_order(src, since=None, until=None):
    """The published log is ordered by record id, not by time, and conversion needs time order (runs, call/result
    pairs, the platform's status changes). Each line is kept zlib-compressed while sorting (about a quarter of its
    size), so a 900 MB log sorts in a few hundred MB of memory. The record's own created_at is its last one on the line
    (nested tool outputs are strings, their quotes escaped)."""
    import zlib
    opener = gzip.open if src.endswith(".gz") else open
    keep = []
    with opener(src, "rb") as f:
        for k, line in enumerate(f):
            m = list(CREATED.finditer(line))
            ts = m[-1].group(1).decode() if m else ""
            if (since and ts < since) or (until and ts >= until):
                continue
            keep.append((ts, k, zlib.compress(line, 1)))
    keep.sort()
    for ts, k, z in keep:
        yield json.loads(zlib.decompress(z))


def main(src, out, actor_files=(), since=None, until=None):
    t0 = time.time()
    known = {}
    for path in actor_files:
        for line in open(path):
            a = json.loads(line)
            known.setdefault(a.get("label") or a["id"], {k: a[k] for k in ("id", "label", "kind", "role", "model") if a.get(k)})
    cv = Converter(known)
    n = 0
    for r in records_in_time_order(src, since, until):
        cv.record(r)
        n += 1
    cv.finish()
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "events.jsonl"), "w") as f:
        for e in cv.events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    me = known.get("Opus 4.5 (Claude Code)", {"id": cv.agent, "label": "Opus 4.5 (Claude Code)"})
    cv.actors[cv.agent] = {**me, "id": cv.agent, "kind": "agent", "model": "claude-opus-4-5-20251101",
                           "role": None}
    cv.actors[HARNESS] = {"id": HARNESS, "label": "harness (the platform running the agent)", "kind": "system"}
    cv.actors[UNATTRIBUTED] = {"id": UNATTRIBUTED, "label": "unattributed", "kind": "system"}
    with open(os.path.join(out, "actors.jsonl"), "w") as f:
        for a in cv.actors.values():
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
    goals = sorted(cv.goals)
    with open(os.path.join(out, "phases.jsonl"), "w") as f:
        for k, (ts, g) in enumerate(goals):
            end = goals[k + 1][0] if k + 1 < len(goals) else None
            f.write(json.dumps({"label": g, "start": ts, "end": end}, ensure_ascii=False) + "\n")
    own = [e["ts"] for e in cv.events if e["actor"] == cv.agent] or [e["ts"] for e in cv.events]
    first, last = min(own), max(own)  # the transcript's span: messages it read can be much older (history replays)
    meta = {
        "name": "AI Village: one agent's working transcript",
        "description": (f"The full working transcript of one AI Village agent, Opus 4.5 (Claude Code), {first[:10]} to "
                        f"{last[:10]}: its private reasoning, every tool call it made and what came back (with an error "
                        "flag), the village chat messages it read (as delivered to it) and the ones it sent, its memory "
                        "notes, the goals and summaries it gave for its computer sessions, and what the platform told it "
                        "(village goals, memory reminders). Other agents appear only through what this agent read."),
        "notes": ("kinds: reasoning = private thinking (never evidence); action + result = what it did and what came "
                  "back (result.reply_to = the action, meta.error = failed: is_error, an error field, or a non-zero exit); "
                  "message = said to others (its own carry the platform's chat id, meta.posted false if the post "
                  "failed), read = it read a message; self_report = memory, session goals/summaries, query summaries. "
                  "meta.run numbers the agent's runs (a run starts at a harness 'run N starts' event); 'context "
                  "compacted' marks where its working context was summarised. Times are UTC; read messages carried "
                  "Los Angeles local times and were converted. Visitors are anonymised. Phases are village goals."),
        "lead_kinds": ["message", "action"],
        "source": os.path.basename(src),
    }
    with open(os.path.join(out, "dataset.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    size = os.path.getsize(os.path.join(out, "events.jsonl"))
    print(f"{n:,} records -> {len(cv.events):,} events ({size / 1e6:.0f} MB), {len(cv.actors)} actors, "
          f"{len(goals)} goal phases, {cv.run} runs in {time.time() - t0:.0f}s")
    print("  by kind:", dict(cv.counts.most_common()))


if __name__ == "__main__":
    a = sys.argv[1:]
    opt = lambda k: a[a.index(k) + 1] if k in a else None
    actor_files = [a[i + 1] for i, x in enumerate(a) if x == "--actors"]
    main(a[0], a[1], actor_files, opt("--since"), opt("--until"))
