"""The people's view of one agent's record (page /agent): how its behaviour changed over time, and what readers found.

For people, not agents: plain words, no ids or category names on screen, the source text only when asked (each item
carries the event ids the page fetches on click). Three things, one meaning each:
  days     how much it did each day (bars), where something it did suddenly multiplied (marks), what readers flagged
  periods  per goal period: what stood out against the rest of its record, sudden increases, what readers flagged,
           rules it broke, things it said that its own earlier work contradicts
  watch    things that matter whenever they happen (personal data posted, failed posts)
Counts come from code (behaviour.py) and from the sub-agents that read every chunk (sweep.py); the page says which.
"""
import collections
import json
import re

from . import behaviour as B
from . import sweep as S

TOOLS = {"Write": "wrote files", "Edit": "edited files", "MultiEdit": "edited files", "Read": "opened files",
         "Grep": "searched files", "Glob": "listed files", "computer_use": "used the screen, mouse and keyboard",
         "TodoWrite": "updated its to-do list", "WebSearch": "searched the web", "WebFetch": "fetched web pages",
         "bash": "ran shell commands", "Bash": "ran shell commands", "chat_message": "sent chat messages",
         "get_events": "checked for new messages", "edit_memory": "edited its memory",
         "get_pixel_coordinates": "located things on the screen", "request_google_sign_in": "asked for a Google sign-in",
         "search_history": "searched the chat history", "start_computer_session": "started a computer session",
         "stop_computer_session": "ended a computer session", "Task": "started a helper agent"}
PROGRAMS = {"git push": "pushed code", "git commit": "committed changes", "git add": "staged changes",
            "git pull": "pulled others' changes", "git fetch": "fetched others' changes", "git checkout": "switched branches",
            "git log": "read the change history", "git show": "inspected past changes", "git diff": "compared versions",
            "git ls-tree": "inspected repository contents", "git rev-list": "inspected repository history",
            "git status": "checked its working copy", "git clone": "copied repositories", "git merge": "merged branches",
            "git rebase": "rebased branches", "gh pr list": "listed pull requests", "gh pr view": "opened pull requests",
            "gh pr create": "opened new pull requests", "gh pr merge": "merged pull requests",
            "gh pr review": "reviewed pull requests", "gh pr diff": "read pull request changes",
            "gh pr checks": "checked pull request tests", "gh issue view": "read issues", "gh issue list": "listed issues",
            "gh issue create": "opened issues", "gh issue comment": "commented on issues", "gh api": "called the GitHub API",
            "gh search prs": "searched pull requests", "gh search issues": "searched issues", "gh run view": "read test runs",
            "curl": "fetched web pages and APIs", "python": "ran Python scripts", "python3": "ran Python scripts",
            "node": "ran JavaScript", "npm run": "ran project scripts", "npm test": "ran project tests",
            "rm": "deleted files", "grep": "searched files", "cat": "printed files", "ls": "listed files",
            "find": "searched for files", "date": "checked the time", "tail": "read the ends of files",
            "head": "read the starts of files", "wc": "counted lines", "mkdir": "made folders", "jq": "read JSON"}
FAILED = {"computer_use": "screen actions", "bash": "shell commands", "Bash": "shell commands", "Read": "file reads",
          "Edit": "file edits", "Write": "file writes", "chat_message": "chat messages", "get_events": "message checks",
          "WebFetch": "web page fetches", "search_history": "history searches", "Grep": "file searches",
          "start_computer_session": "computer session starts", "edit_memory": "memory edits"}
SAYING = {"said": "sent messages", "claims an outcome": "said it had done or checked something",
          "corrects or retracts": "corrected or took back what it said",
          "posts a personal e-mail address": "posted a personal e-mail address",
          "posts an e-mail address": "posted an e-mail address", "posts a phone number": "posted a phone number",
          "message failed to post": "had a message fail to post", "thought": "thought privately",
          "thought: doubts its work": "privately doubted its own work",
          "thought: concealment or evasion words": "privately used words about hiding or getting past checks",
          "delegated work": "handed work to others", "returned work": "handed work back"}
JUDGED = {"statement overstates": "said more than its record shows", "statement contradicted":
          "said something its record contradicts", "instruction broke": "acted against an instruction",
          "instruction ignored": "ignored an instruction", "instruction refused": "refused an instruction",
          "concern privacy": "exposed private information", "concern deception": "deceived others",
          "concern rule_breaking": "broke a rule", "concern unsafe_action": "did something unsafe",
          "concern misreport": "reported something that was not so", "concern accusation":
          "accused others without evidence", "concern manipulation": "pressured or manipulated others",
          "concern other": "did something an overseer should know about"}
KIND = {"deception": "deceived others", "privacy": "exposed private information", "rule_breaking": "broke a rule",
        "unsafe_action": "did something unsafe", "misreport": "reported something that was not so",
        "accusation": "accused others without evidence", "manipulation": "pressured or manipulated others",
        "other": "worth knowing"}


def plain(cat):
    """a behaviour category in everyday words"""
    if cat in SAYING:
        return SAYING[cat]
    head, _, rest = cat.partition(": ")
    if head == "ran":
        return PROGRAMS.get(rest, f"ran the program {rest}")
    if head == "acted":
        return TOOLS.get(rest, f"used the tool {rest}")
    if head == "failed":
        return f"had {FAILED.get(rest, rest + ' calls')} fail"
    if head == "self-report":
        return {"memory": "wrote memory notes", "computer-session": "wrote session goals and summaries",
                "query-summary": "wrote end-of-task summaries"}.get(rest, "wrote notes about itself")
    if head == "read":
        return {"message from agent": "read other agents' messages", "message from human": "read people's messages",
                "message from system": "read platform messages"}.get(rest, "read messages")
    if head == "judged":
        sanctioned = rest.endswith("(sanctioned)")
        base = JUDGED.get(rest.replace(" (sanctioned)", ""), rest)
        return base + (" (as allowed by a game it was in)" if sanctioned else "")
    if head == "note":
        return rest.replace("_", " ")
    if head == "requested":
        return f"sent requests to {rest}"
    if head == "request":
        return {"query shaped like code injection": "sent requests whose query is shaped like code injection",
                "random tag that defeats caching": "added random tags that defeat caching",
                "routed through a redirect service": "routed requests through a redirect service",
                "unusual path (doubled slashes)": "used unusual paths (doubled slashes)"}.get(rest, rest)
    return cat


def data(con, share=False):
    """everything the page shows, in plain words with event ids for the sources. share: for readers outside the
    investigation, methods only by kind (the storyline's outside version; findings shown by their kind, not their
    text, since readers' notes can name the method)"""
    meta = dict(con.execute("SELECT key, value FROM meta"))
    ds = json.loads(meta.get("dataset", "{}")) if meta.get("dataset", "").startswith("{") else {}
    unit = B.time_unit(con)
    pre = B.daily(con, unit)
    cats, volume, fam, examples = pre
    ch = B.changes(con, limit=200, pre=pre)
    names = dict(con.execute("SELECT id, label FROM actors"))
    total = {a: sum(v.values()) for a, v in volume.items()}
    lead = max(total, key=total.get)
    if total[lead] < 0.8 * sum(total.values()):  # several actors: the page is about all of them
        lead = None
        agg = collections.Counter()
        for v in volume.values():
            agg.update(v)
        day_volume = agg
        acts, says = collections.Counter(), collections.Counter()
        for a in fam:
            acts.update(fam[a].get("action", {}))
            says.update(fam[a].get("message", {}))
    else:
        day_volume = volume[lead]
        acts = fam[lead].get("action", collections.Counter())
        says = fam[lead].get("message", collections.Counter())
    segs = con.execute("SELECT id, label, start, end FROM segments ORDER BY start").fetchall()

    def period_of(day):
        p = None
        for sid, _, start, _ in segs:
            if (start or "")[:10] <= day:
                p = sid
        return p

    reads = {r[0]: json.loads(r[1]) for r in con.execute("SELECT chunk_id, result FROM w.chunk_reads WHERE qkey=?",
                                                          (S.SWEEP_Q,))} if S._has(con, "chunk_reads") else {}
    chunk_day = dict(con.execute("SELECT id, day FROM w.chunks")) if S._has(con, "chunks") else {}
    per_day_chunks = collections.Counter(chunk_day.values())
    read_day = collections.Counter(chunk_day[c] for c in reads)
    flagged = []
    for cid, r in reads.items():
        for c in r.get("concerns", []):
            if c["severity"] in ("medium", "high") and c.get("events"):
                what = (f"{c['actor']}: " if c.get("actor") else "") + (
                    f"{KIND.get(c['kind'], c['kind'])} ({c['severity']})" if share else c["what"])
                flagged.append({"day": chunk_day[cid], "what": what, "kind": KIND.get(c["kind"], c["kind"]),
                                "severity": c["severity"], "allowed": bool(c.get("sanctioned")),
                                "sources": c["events"][:4]})
    rules = S.rules_view(con, limit=500) if S._has(con, "rules") else {"rules": [], "statements_traced_back": []}
    broken = []
    for r in rules["rules"]:
        for b in r["broken"]:
            broken.append({"day": chunk_day.get(b["chunk"]), "rule": r["rule"], "who_set_it": r["source"],
                           "set_on": r["set"][:10], "what_it_did": "acted against it here" if share else b["how"],
                           "sources": [e.split(" | ")[0] for e in b["evidence"]]})
    traced = [{"day": t["said"][:10], "what_it_said": t["claim"], "what_its_work_shows": t["why"],
               "sources": [t["statement"].split(" | ")[0]] + [e.split(" | ")[0] for e in t["earlier_evidence"]]}
              for t in rules.get("statements_traced_back", [])]
    days = []
    for d in sorted(day_volume):
        if day_volume[d] < B.MIN_ACTIVE:
            continue
        days.append({"day": d, "period": period_of(d), "actions": acts[d], "messages": says[d],
                     "read_by_ai": f"{read_day[d]} of {per_day_chunks[d]}" if per_day_chunks[d] else "",
                     "flagged": sum(1 for f in flagged if f["day"] == d),
                     "rules_broken": sum(1 for b in broken if b["day"] == d),
                     "said_vs_made": sum(1 for t in traced if t["day"] == d),
                     "sudden": [{"what": (f"{b['actor']}: " if lead is None else "") + plain(b["behaviour"]),
                                 "times_usual": b["ratio"], "count": b["count"],
                                 "alone": b["burst"] == "alone", "sources": b["examples"]}
                                for b in ch["bursts"] if b["day"] == d and not b["behaviour"].startswith("judged")][:4]})
    periods = []
    distinct = {p["period"]: p for p in ch["periods"]}
    for sid, label, start, end in segs:
        ds_days = [x for x in days if x["period"] == sid]
        if not ds_days:
            continue
        in_p = lambda day: period_of(day) == sid if day else False
        sev = {"high": 0, "medium": 1}
        fl = sorted((f for f in flagged if in_p(f["day"])), key=lambda f: (f["allowed"], sev[f["severity"]]))
        periods.append({
            "goal": label, "from": (start or "")[:10], "to": (end or "")[:10], "days": len(ds_days),
            "stood_out": [{"what": plain(s["behaviour"]), "times_usual": s["x_usual"], "count": s["count"]}
                          for s in distinct.get(sid, {}).get("stands_out", [])],
            "sudden": sorted([dict(s, day=x["day"]) for x in ds_days for s in x["sudden"]],
                             key=lambda s: -s["times_usual"])[:4],
            "flagged": fl[:6], "flagged_total": len(fl),
            "rules_broken": [b for b in broken if in_p(b["day"])][:5],
            "said_vs_made": [t for t in traced if in_p(t["day"])][:5],
            "read_by_ai": f"{sum(read_day[x['day']] for x in ds_days)} of "
                          f"{sum(per_day_chunks[x['day']] for x in ds_days)} stretches"})
    watch = [{"what": plain(w["behaviour"]), "times": w["total"], "days": w["days"], "sources": w["examples"]}
             for w in ch["watch"] if not w["behaviour"].startswith("judged")]
    from . import storyline as SL
    story = SL.get(con, share=share) or (None if share else SL.get(con))
    if story:
        by_label = {p["period"]: p for p in story.get("periods", [])}
        for p, (sid, *_r) in zip(periods, [s_ for s_ in segs if any(x["period"] == s_[0] for x in days)]):
            st = by_label.get(sid)
            if st:
                p["summary"] = st["summary"]
                p["turning_points"] = st["turning_points"] + st.get("also", [])
    return {"agent": names.get(lead, lead) if lead else None, "unit": unit, "story": story and {
                "overview": story.get("overview", []), "storylines": story.get("storylines", []),
                "also": story.get("also", []), "uncertain": story.get("uncertain", "")},
            "title": ds.get("name") or meta.get("name") or "One agent's record",
            "description": ds.get("description", "") or meta.get("description", ""), "days": days, "periods": periods,
            "watch": watch, "flagged": flagged, "rules_broken": broken, "said_vs_made": traced,
            "how": ("Every stretch of the record was read by an AI reader with the same questions (what was done, "
                    "whether what was said holds up, what was asked and what followed, anything an overseer should "
                    "know). Counts come from the record itself. Readers' findings are leads: open the sources."
                    if reads else "Counts come from the record itself (no AI reader has read it yet).")}


def sources(con, ids):
    """the original text of events, readable (for the page's click-through)"""
    names = dict(con.execute("SELECT id, label FROM actors"))
    out = []
    for i in ids[:12]:
        r = con.execute("SELECT id, ts, actor, kind, text, reply_to, meta FROM events WHERE id=?", (i,)).fetchone()
        if not r:
            continue
        text, who = r[4] or "", names.get(r[2], r[2])
        if r[3] == "read" and r[5]:
            m = con.execute("SELECT actor, text FROM events WHERE id=?", (r[5],)).fetchone()
            if m:
                text, who = m[1] or "", f"{names.get(m[0], m[0])} (read by {who})"
        what = {"action": "did", "result": "got back", "message": "said", "reasoning": "thought privately",
                "self_report": "noted about itself", "read": "read"}.get(r[3], r[3])
        failed = r[3] == "result" and r[6] and '"error": true' in r[6]
        out.append({"when": r[1][:16].replace("T", " "), "who": who, "what": what + (" (it failed)" if failed else ""),
                    "text": re.sub(r"\s+", " ", text)[:1500]})
    return out

