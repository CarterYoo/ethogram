"""How an actor's behaviour changes over time, from the record alone (code; no LLM).

Every event of an actor who acts (or, in logs without actions, of every actor) is counted under the behaviours it
shows, by rules any log in the canonical format allows:
  kind          acted, said, read, thought, self-report
  tool          the tool an action used (meta.tool), and for shell commands the program run ("git push",
                "gh pr create", "curl", "python3")
  failure       a failed result, by tool
  statements    claims an outcome, corrects or retracts, carries a (personal) e-mail address or a phone number,
                failed to post
  reads         messages read, by who wrote them (human, agent, platform)
  reasoning     doubts its own work, uses concealment or evasion words (the ledger's cue lists)
  notes         the LLM behaviour tags of each event, where the index has them (chat-scale logs)
  judged        what the sweep's sub-agents found per chunk (statements the record does not bear out, instructions
                broken, concerns by kind), marked as judgments and counted only over chunks they read

Per day (per hour in short, dense records, where actors are active on few days), a behaviour BURSTS when its
count is far above what the actor's other active days predict (Poisson tail
below 1e-4, at least 5 events and 3x the expected): `alone` if its share of the actor's events of the same kind rose
too (this behaviour specifically), `with volume` if the actor simply did more of everything that day. It is NEW when
it starts after at least 5 active days without it, and it STOPS when it ends after being regular. Per period the
behaviours that set it apart (share against the whole record) are listed. Every finding carries example events.
"""
import collections
import json
import math
import os
import re

from .ledger import CLAIM, CORRECTION, DOUBT, FIRST_PERSON, INTENT

EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PERSONAL_MAIL = re.compile(r"@(gmail|googlemail|yahoo|hotmail|outlook|live|icloud|me|aol|gmx|proton(mail)?|pm)\.", re.I)
PHONE = re.compile(r"(?<![\w.])(?:\+?\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w.])")
SKIP_PROGRAMS = {"cd", "echo", "sleep", "export", "set", "true", "source", ".", "printf", "pwd", "clear", "then",
                 "fi", "do", "done", "else", "if", "for", "while"}
MULTI = {"git", "gh", "npm", "npx", "pip", "pip3", "docker", "kubectl", "cargo", "yarn", "pnpm", "apt", "apt-get",
         "brew", "systemctl"}
MIN_ACTIVE = 20  # events by the actor on a day for the day to count as active
WATCH = {"posts a personal e-mail address", "posts a phone number", "message failed to post",
         "thought: concealment or evasion words", "request: query shaped like code injection",
         "request: routed through a redirect service", "request: unusual path (doubled slashes)"}
JUDGED_WATCH = ("broke", "ignored", "privacy", "deception", "rule_breaking", "unsafe_action", "manipulation")


def shell_program(cmd):
    """'bash: cd x && git push origin main' -> 'git push'; 'gh pr create ...' -> 'gh pr create'"""
    s = cmd.split(":", 1)[1] if re.match(r"^\s*\w+:", cmd[:20] or "") else cmd
    for part in re.split(r"&&|\|\||;|\||\n", s[:600]):
        toks = part.strip().split()
        while toks and (re.match(r"^[A-Za-z_]\w*=", toks[0]) or toks[0] in ("sudo", "timeout", "time", "nohup", "env",
                                                                            "exec", "(", "{")
                        or re.match(r"^\d+[smh]?$", toks[0])):
            toks = toks[1:]
        if not toks:
            continue
        prog = os.path.basename(toks[0].strip("'\"()"))
        if (not re.match(r"^[\w.+-]+$", prog) or prog in SKIP_PROGRAMS or
                re.search(r"\.(json|md|txt|html?|csv|ya?ml|log|xml|png|jpe?g)$", prog, re.I)):  # data, not a program
            continue
        words = [t for t in toks[1:4] if re.match(r"^[a-z][\w-]*$", t)]
        if prog in MULTI and words:
            if prog == "gh" and len(words) > 1:
                return f"{prog} {words[0]} {words[1]}"
            return f"{prog} {words[0]}"
        return prog
    return None


URL = re.compile(r"https?://[^\s'\"<>]+")
INJECTION = re.compile(r"(\b(or|and)\b\s*'?\d+'?\s*=\s*'?\d+|\bunion\b\s+(all\s+)?\bselect\b|'\s*--|;\s*--|"
                       r"\bsleep\s*\(|<\s*script|\.\./\.\./|%00)", re.I)
CACHE_TAG = re.compile(r"[?&](zz|zzbulk|cb|nonce|prepnonce|uniq|rand|ts|_)=[^&]{3,}", re.I)
REDIRECT = re.compile(r"redirect[^?]*\?[^ ]*url=|[?&](url|u|target|dest)=https?%3a", re.I)


def request_categories(url):
    """what a requested URL shows (generic web-request behaviours, described by kind)"""
    from urllib.parse import unquote_plus, urlparse
    u = urlparse(url)
    host = (u.netloc or "").split(":")[0].lower()
    out = [f"requested: {host}"] if host else []
    q = unquote_plus(u.query or "")
    if INJECTION.search(q) or INJECTION.search(unquote_plus(u.path or "")):
        out.append("request: query shaped like code injection")
    if CACHE_TAG.search("?" + (u.query or "")):
        out.append("request: random tag that defeats caching")
    if REDIRECT.search(url):
        out.append("request: routed through a redirect service")
    if "//" in (u.path or "")[1:]:
        out.append("request: unusual path (doubled slashes)")
    return out


def categories(e, author_kind=None):
    """behaviours one event shows (readable names)"""
    k, t, m = e["kind"], e["text"] or "", e["meta"]
    out = []
    if k == "action":
        tool = m.get("tool")
        out.append(f"acted: {tool}" if tool else "acted")
        if not t.lower().startswith(("bash:", "edit:", "write:", "read:")):  # a request rather than a command
            urls = URL.findall(t[:2000])
            if urls:
                out += request_categories(urls[0])
        if tool and tool.lower() in ("bash", "shell", "terminal", "exec", "run_command") or t.lower().startswith("bash:"):
            p = shell_program(t)
            if p:
                out.append(f"ran: {p}")
    elif k == "result":
        if e["error"]:
            out.append(f"failed: {m.get('tool') or 'action'}")
    elif k in ("message", "self_report"):
        out.append("said" if k == "message" else f"self-report: {e['channel'] or 'note'}")
        if CLAIM.search(t) and (k == "self_report" or FIRST_PERSON.search(t)):
            out.append("claims an outcome")
        if k == "message":
            if CORRECTION.search(t):
                out.append("corrects or retracts")
            mails = EMAIL.findall(t)
            if any(PERSONAL_MAIL.search(x) for x in mails):
                out.append("posts a personal e-mail address")
            elif mails:
                out.append("posts an e-mail address")
            if PHONE.search(t):
                out.append("posts a phone number")
            if m.get("posted") is False:
                out.append("message failed to post")
    elif k == "read":
        out.append(f"read: message from {author_kind or 'someone'}")
    elif k == "reasoning":
        out.append("thought")
        if DOUBT.search(t):
            out.append("thought: doubts its work")
        if INTENT.search(t):
            out.append("thought: concealment or evasion words")
    elif k in ("call", "return"):
        out.append("delegated work" if k == "call" else "returned work")
    return out


# the events a behaviour is a share of: claims among statements, failures among results, doubt among thoughts
KIND_OF = {"acted": "action", "ran": "action", "failed": "result", "said": "message", "claims an outcome": "statement",
           "corrects or retracts": "message", "posts a personal e-mail address": "message",
           "posts an e-mail address": "message", "posts a phone number": "message",
           "message failed to post": "message", "self-report": "self_report", "read": "read", "thought": "reasoning",
           "delegated work": "call", "returned work": "return"}


def _family(cat):
    return KIND_OF.get(cat) or KIND_OF.get(cat.split(":")[0])  # None: a share of all the actor's events


def time_unit(con):
    """'day', or 'hour' when the actors are active on too few days for days to show change (short, dense records:
    requests in bursts of hours). Decided by the event-weighted median of active days per actor."""
    per = {}
    for actor, day, n in con.execute("SELECT actor, substr(ts, 1, 10), count(*) FROM events GROUP BY 1, 2"):
        a = per.setdefault(actor, [0, 0])
        a[0] += n
        a[1] += n >= MIN_ACTIVE
    if not per:
        return "day"
    weighted = sorted((days, n) for n, days in per.values())
    half, acc = sum(n for _, n in weighted) / 2, 0
    for days, n in weighted:
        acc += n
        if acc >= half:
            return "day" if days >= 6 else "hour"
    return "day"


def daily(con, unit=None):
    """{actor: {category: Counter(day -> n)}}, {actor: Counter(day -> events)}, {actor: {family: Counter(day)}},
    examples {(actor, category, day): [event ids]}; 'day' keys are hours ('YYYY-MM-DDTHH') when unit is 'hour'"""
    cut = 13 if (unit or time_unit(con)) == "hour" else 10
    doers = {a for (a,) in con.execute("SELECT DISTINCT actor FROM events WHERE kind IN ('action', 'result')")}
    if not con.execute("SELECT 1 FROM events WHERE kind='read' LIMIT 1").fetchone():
        doers = set()  # no reads: every actor's own events count (an edit log, a board)
    system = {a for (a,) in con.execute("SELECT id FROM actors WHERE kind='system'")}
    akind = dict(con.execute("SELECT id, kind FROM actors"))
    author = {}
    if doers:
        author = {r[0]: akind.get(r[1], "agent") for r in con.execute(
            "SELECT e.id, e.actor FROM events e WHERE e.id IN (SELECT reply_to FROM events WHERE kind='read')")}
    tags = collections.defaultdict(list)
    try:
        for eid, tag in con.execute("SELECT event_id, tag FROM w.event_tags"):
            tags[eid].append(tag)
    except Exception:
        pass
    cats = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    volume = collections.defaultdict(collections.Counter)
    fam = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    examples = collections.defaultdict(list)
    for i, ts, actor, kind, text, rt, channel, meta in con.execute(
            "SELECT id, ts, actor, kind, text, reply_to, channel, meta FROM events ORDER BY ts, id"):
        if actor in system or (doers and actor not in doers):
            continue
        m = json.loads(meta) if meta else {}
        e = {"id": i, "kind": kind, "text": text, "meta": m, "channel": channel, "error": bool(m.get("error"))}
        day = ts[:cut]
        volume[actor][day] += 1
        cs = categories(e, author.get(rt) if kind == "read" else None) + [f"note: {t}" for t in tags.get(i, ())]
        for c in cs:
            cats[actor][c][day] += 1
            ex = examples[(actor, c, day)]
            if len(ex) < 40:
                ex.append(i)
        for f in {kind} | ({"statement"} if kind in ("message", "self_report") else set()):
            fam[actor][f][day] += 1
    return cats, volume, fam, examples


def judged_daily(con, cut=10):
    """counts from the sweep's reads per day (or hour), and the days whose chunks were all read"""
    try:
        rows = con.execute(f"SELECT substr(c.start, 1, {cut}), r.result FROM w.chunk_reads r JOIN w.chunks c "
                           f"ON c.id = r.chunk_id WHERE r.qkey='sweep-v1'").fetchall()
        per_day = collections.Counter(d for (d,) in con.execute(f"SELECT substr(start, 1, {cut}) FROM w.chunks"))
    except Exception:
        return {}, set()
    read_day = collections.Counter(r[0] for r in rows)
    full = {d for d, n in per_day.items() if read_day[d] == n}
    out = collections.defaultdict(collections.Counter)
    ex = collections.defaultdict(list)
    for day, res in rows:
        r = json.loads(res)
        items = [(f"judged: statement {s['record']}", s) for s in r.get("said", []) if s["record"] in ("overstates",
                                                                                                         "contradicted")]
        items += [(f"judged: instruction {x['response']}", x) for x in r.get("instructions", [])
                  if x["response"] in ("broke", "ignored", "refused")]
        items += [(f"judged: concern {c['kind']}" + (" (sanctioned)" if c.get("sanctioned") else ""), c)
                  for c in r.get("concerns", []) if c["severity"] in ("medium", "high")]
        for cat, it in items:
            out[cat][day] += 1
            if it.get("events"):
                ex[(cat, day)].append(it["events"][0])
    return {"cats": out, "examples": ex}, full


def _poisson_tail(n, lam):
    """P(X >= n) for X ~ Poisson(lam)"""
    if lam <= 0:
        return 0.0 if n > 0 else 1.0
    term = math.exp(-lam)
    cdf = 0.0
    for k in range(n):
        cdf += term
        term *= lam / (k + 1)
    return max(0.0, 1.0 - cdf)


def changes(con, actor=None, limit=15, min_count=5, pre=None):
    """bursts, new and stopped behaviours, and what set each period apart (pre: daily(con) already computed)"""
    from . import query as Q
    unit = time_unit(con)
    cats, volume, fam, examples = pre or daily(con, unit)
    jd, full_days = judged_daily(con, 13 if unit == "hour" else 10)
    names = dict(con.execute("SELECT id, label FROM actors"))
    if actor:
        a = Q.resolve(con, actor)
        cats, volume, fam = {a: cats.get(a, {})}, {a: volume.get(a, collections.Counter())}, {a: fam.get(a, {})}
    bursts, new, stopped = [], [], []
    lead = max(volume, key=lambda a: sum(volume[a].values())) if volume else None
    for a, cs in cats.items():
        active = sorted(d for d, n in volume[a].items() if n >= MIN_ACTIVE)
        if len(active) < 6:
            continue
        series = dict(cs)
        if a == lead and jd:
            for c, days in jd["cats"].items():
                series[c] = days
        for c, days in series.items():
            judged = c.startswith("judged:")
            act = [d for d in active if d in full_days] if judged else active
            if len(act) < 6:
                continue
            total = sum(days[d] for d in act)
            if total < min_count:
                continue
            f = _family(c)
            fam_days = (fam[a].get(f) if f else volume[a]) if not judged else None
            ftotal = sum(fam_days[d] for d in act) if fam_days else None
            for d in act:
                n = days[d]
                if n < min_count:
                    continue
                others = len(act) - 1
                mean_other = (total - n) / others
                p_abs = _poisson_tail(n, mean_other)
                if n < 3 * max(mean_other, 1.0) or p_abs > 1e-4:
                    continue
                kind = "with volume"
                if fam_days and ftotal - fam_days[d] > 0:
                    share_other = (total - n) / (ftotal - fam_days[d])
                    exp_share = fam_days[d] * share_other
                    if n >= 3 * max(exp_share, 1.0) and _poisson_tail(n, exp_share) < 1e-4:
                        kind = "alone"
                elif judged:
                    kind = "judged"
                bursts.append({"actor": names.get(a, a), "behaviour": c, "day": d, "count": n,
                               "typical_day": round(mean_other, 1), "ratio": round(n / max(mean_other, 0.5), 1),
                               "burst": kind, "score": -math.log10(max(p_abs, 1e-300)),
                               "examples": _ex(examples, jd, a, c, d)})
            # new: starts after >= 5 active days without it; stops: regular, then absent for >= 5 active days
            first = next((k for k, d in enumerate(act) if days[d] >= 3), None)
            if first is not None and first >= 5 and sum(days[d] for d in act[:first]) == 0:
                after = sum(days[d] for d in act[first:])
                new.append({"actor": names.get(a, a), "behaviour": c, "from": act[first], "count_after": after,
                            "active_days_before_without_it": first,
                            "examples": _ex(examples, jd, a, c, act[first])})
            last = max((k for k, d in enumerate(act) if days[d] >= 3), default=None)
            if last is not None and len(act) - 1 - last >= 5 and sum(days[d] for d in act[last + 1:]) == 0:
                before = [days[d] for d in act[:last + 1]]
                if sum(1 for x in before if x) >= 4 and sum(before) / len(before) >= 1:
                    stopped.append({"actor": names.get(a, a), "behaviour": c, "last_day": act[last],
                                    "count_before": sum(before), "active_days_after_without_it": len(act) - 1 - last,
                                    "examples": _ex(examples, jd, a, c, act[last])})
    bursts.sort(key=lambda b: (b["burst"] != "alone", -b["score"]))
    new.sort(key=lambda x: -x["count_after"])
    stopped.sort(key=lambda x: -x["count_before"])
    watch = []  # behaviours that matter whenever they occur, burst or not
    for a, cs in cats.items():
        series = dict(cs)
        if a == lead and jd:
            series.update(jd["cats"])
        for c, days in series.items():
            if not (c in WATCH or (c.startswith("judged:") and any(w in c for w in JUDGED_WATCH))):
                continue
            ex = [i for d in sorted(days) for i in _ex(examples, jd, a, c, d, n=2)][:6]
            watch.append({"actor": names.get(a, a), "behaviour": c, "total": sum(days.values()), "days": len(days),
                          "busiest_days": dict(days.most_common(3)), "examples": ex})
    watch.sort(key=lambda w: (not w["behaviour"].startswith("judged:"), -w["total"]))
    return {"read": "Counted by code from the record (`judged:` rows are the sweep's sub-agent findings, counted only "
                    "on days all of whose chunks were read). A burst is a day far above the actor's other active days; "
                    "'alone' means its share of that kind of event rose too, 'with volume' that the actor did more of "
                    "everything that day. Counts say where to look, not what happened: open the examples.",
            "unit": unit, "watch": watch[:limit], "bursts": bursts[:limit], "new": new[:limit],
            "stopped": stopped[:limit],
            "periods": distinctive(con, *_pooled(cats, volume, lead if not actor else Q.resolve(con, actor)), names),
            "judged_days_read": len(full_days)}


def _ex(examples, jd, a, c, d, n=3):
    ids = jd["examples"].get((c, d), []) if c.startswith("judged:") and jd else examples.get((a, c, d), [])
    if len(ids) <= n:
        return ids
    step = len(ids) / n
    return [ids[int(k * step)] for k in range(n)]


def _pooled(cats, volume, lead):
    """(cats, volume, key) for the period profile: the lead actor's own, or everyone's pooled when no actor holds 80%
    of the events (a group log)"""
    total = {a: sum(v.values()) for a, v in volume.items()}
    if lead in total and total[lead] >= 0.8 * sum(total.values()):
        return cats, volume, lead
    pc, pv = collections.defaultdict(collections.Counter), collections.Counter()
    for a in cats:
        for c, days in cats[a].items():
            pc[c].update(days)
        pv.update(volume[a])
    return {"*": pc}, {"*": pv}, "*"


def distinctive(con, cats, volume, actor_lead=None, names=None, top=5, min_n=8):
    """per period (segments): the behaviours with the highest share against the whole record"""
    a = actor_lead
    if a not in cats:
        return []
    segs = con.execute("SELECT id, label, start FROM segments ORDER BY start").fetchall()
    seg_of = {}
    for d in volume[a]:  # a day belongs to the last period that started on or before it
        for sid, _, start in segs:
            if (start or "")[:len(d)] <= d:
                seg_of[d] = sid
    total_v = sum(volume[a].values())
    out = []
    for sid, label, start in segs:
        days = {d for d, s in seg_of.items() if s == sid}
        v = sum(volume[a][d] for d in days)
        if not v:
            continue
        rows = []
        for c, ds in cats[a].items():
            n = sum(ds[d] for d in days)
            if n < min_n:
                continue
            N = sum(ds.values())
            lift = (n / v) / (N / total_v)
            if lift >= 1.5:
                rows.append({"behaviour": c, "count": n, "x_usual": round(lift, 1)})
        rows.sort(key=lambda r: -r["x_usual"] * math.log(1 + r["count"]))
        out.append({"period": sid, "label": (label or "")[:70], "from": (start or "")[:10], "events": v,
                    "stands_out": rows[:top]})
    return out
