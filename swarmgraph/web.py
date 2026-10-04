"""Browser explorer (network + timeline views) over any index. Read-only JSON API + one static page."""
import http.server
import json
import os
import urllib.parse

from . import query as Q
from .build import epoch
from .store import open_work

HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = {"addressed", "replied", "invoked", "returned", "read"}


def family(model, label):
    s = f"{model or ''} {label or ''}".lower()
    for key, name in (("claude", "Claude"), ("gpt", "GPT"), ("o1-", "GPT"), ("o3", "GPT"), ("o4", "GPT"),
                      ("gemini", "Gemini"), ("deepseek", "DeepSeek"), ("glm", "GLM"), ("kimi", "Kimi"), ("grok", "Grok")):
        if key in s:
            return name
    return "Other"


def api_segments(con, work, q):
    return [{"id": s["id"], "goal": s["label"], "start_ts": s["start"], "end_ts": s["end"], "agents": s["actors"],
             "msgs": s["events"]} for s in Q.segments(con)]


def api_graph(con, work, q):
    seg = int(q["period"][0])
    rtype = q.get("type", ["addressed"])[0]
    if rtype not in TYPES:
        raise Q.QueryError(f"type must be one of {sorted(TYPES)}")
    have = {r[0] for r in work.execute("SELECT actor FROM summaries WHERE segment_id=?", (seg,))}
    nodes = [{"id": r["actor"], "label": r["label"], "role": r["role"], "model": r["model"], "goal": None,
              "family": family(r["model"], r["label"]), "msgs": r["n_events"], "sessions": r["n_out"], "llm": r["actor"] in have}
             for r in con.execute("SELECT n.actor, n.n_events, n.n_out, a.label, a.role, a.model FROM nodes n "
                                  "JOIN actors a ON a.id = n.actor WHERE n.segment_id=? AND a.kind='agent'", (seg,))]
    ids = {n["id"] for n in nodes}
    edges = [{"src": r[0], "dst": r[1], "n": r[2], "replied": r[3]} for r in con.execute(
        "SELECT src, dst, count(*), count(answered_by) FROM relations WHERE segment_id=? AND type=? GROUP BY src, dst",
        (seg, rtype)) if r[0] in ids and r[1] in ids]
    s = con.execute("SELECT * FROM segments WHERE id=?", (seg,)).fetchone()
    return {"period": {"id": s["id"], "goal": s["label"], "start_ts": s["start"], "end_ts": s["end"]},
            "type": rtype, "nodes": nodes, "edges": edges}


def lane_order(nodes, edges):
    w, strength = {}, {n["id"]: 0 for n in nodes}
    for e in edges:
        k = tuple(sorted((e["src"], e["dst"])))
        w[k] = w.get(k, 0) + e["n"]
        strength[e["src"]] += e["n"]
        strength[e["dst"]] += e["n"]
    left = set(strength)
    order = [max(left, key=lambda i: strength[i])] if left else []
    left -= set(order)
    while left:
        nxt = max(left, key=lambda i: (w.get(tuple(sorted((order[-1], i))), 0), strength[i]))
        order.append(nxt)
        left.remove(nxt)
    return order


def api_timeline(con, work, q):
    g = api_graph(con, work, q)
    seg = g["period"]["id"]
    lane = {aid: i for i, aid in enumerate(lane_order(g["nodes"], g["edges"]))}
    by_id = {n["id"]: n for n in g["nodes"]}
    events = [[epoch(r[0]) * 1000, lane[r[1]], lane[r[2]], int(r[3] is not None), r[4]] for r in con.execute(
        "SELECT ts, src, dst, answered_by, event_id FROM relations WHERE segment_id=? AND type=? ORDER BY ts",
        (seg, g["type"])) if r[1] in lane and r[2] in lane]
    ticks = [[epoch(r[0]) * 1000, lane[r[1]]] for r in con.execute(
        "SELECT ts, actor FROM events WHERE segment_id=? ORDER BY ts", (seg,)) if r[1] in lane]
    return {"period": g["period"], "type": g["type"], "lanes": [by_id[i] for i in sorted(lane, key=lane.get)],
            "events": events, "ticks": ticks}


def api_episode(con, work, q):
    aid, seg = q["agent"][0], int(q["period"][0])
    n = con.execute("SELECT * FROM nodes WHERE actor=? AND segment_id=?", (aid, seg)).fetchone()
    if not n:
        raise Q.QueryError("no activity for this actor in this segment")
    first = con.execute("SELECT id, url FROM events WHERE actor=? AND segment_id=? ORDER BY ts LIMIT 1", (aid, seg)).fetchone()
    last = con.execute("SELECT id, url FROM events WHERE actor=? AND segment_id=? ORDER BY ts DESC LIMIT 1", (aid, seg)).fetchone()
    out = {"period_id": seg, "period_goal": con.execute("SELECT label FROM segments WHERE id=?", (seg,)).fetchone()[0],
           "summary": n["summary"],
           "pointers": {"first_message": {"id": first["id"]}, "last_message": {"id": last["id"]}, "sessions": n["n_events"],
                        "last_memory_snapshot": {"id": n["self_report"]}, "ui_first": first["url"], "ui_last": last["url"]}}
    from .tags import available, profile
    if available(con):
        p = profile(con, aid, segment=seg)
        out["behaviour"] = {"tagged": p["tagged_events"], "tags": p["tags"], "free_labels": p["free_labels"][:5]}
    row = work.execute("SELECT * FROM summaries WHERE actor=? AND segment_id=?", (aid, seg)).fetchone()
    if row:
        out["llm_summary"] = {"result": json.loads(row["result"]), "refs": json.loads(row["refs"]),
                              "grounded": row["grounded"], "effort": row["model"]}
    return out


def api_edge(con, work, q):
    r = Q.interactions(con, q["a"][0], q["b"][0], q.get("type", ["addressed"])[0], segment=int(q["period"][0]),
                       limit=int(q.get("limit", ["40"])[0]), newest=True)
    rows = [{"ts": x["ts"], "src": x["from"], "dst": x["to"], "response_id": x["answered_by"], "ui": x["url"],
             "snippet": x["text"], "event_id": x["event_id"], "method": x["method"],
             "behaviour": Q.tag_of(con, x["event_id"])} for x in reversed(r["rows"])]
    return {"total": r["total"], "evidence": rows}


def api_msg(con, work, q):
    e = Q.get_event(con, q["id"][0], 4000)
    return {"id": e["id"], "label": e["actor_name"], "ts": e["ts"], "room": e["channel"] or e["kind"], "text": e["text"],
            "ui": e["url"], "behaviour": e["behaviour"]}


from .tags import GROUPS, GROUP_OF  # noqa: E402


def _mix(evs):
    import collections
    c = collections.Counter(GROUP_OF.get(t, "other") for e in evs for t in (e.get("tags") or []))
    n = sum(c.values()) or 1
    return {g: round(v / n, 3) for g, v in c.items()}


def api_story(con, work, q):
    """Swarm behaviour mix per time bucket + swarm card + lanes (entities with LLM cards): phases and changes."""
    from . import cards as K, structure as ST
    kind = q.get("kind", ["actor"])[0]
    limit = int(q.get("limit", ["200"])[0])
    note = ST.notes(con)
    buckets = []
    for label, lo, hi in K.buckets(con):
        ids = [r[0] for r in con.execute("SELECT id FROM events WHERE ts >= ? AND ts < ?", (lo, hi))]
        if ids:
            buckets.append({"label": label, "lo": lo, "hi": hi, "n": len(ids), "mix": _mix([note.get(i, {}) for i in ids])})
    swarm = K.get_card(con, "all", "all")
    story = K.get_card(con, "story", "all")
    lanes = []
    try:
        rows = con.execute("SELECT key, card FROM w.cards WHERE kind=?", (kind,)).fetchall()
    except Exception:
        rows = []
    for key, card in rows:
        card = json.loads(card)
        evs = ST.entity_events(con, kind, key, note)
        ts = {e["id"]: e["ts"] for e in evs}
        phases = []
        for i, p in enumerate(card.get("phases") or [], 1):
            lo, hi = ts.get(p["start"]), ts.get(p["end"])
            if not lo or not hi:
                continue
            inside = [e for e in evs if lo <= e["ts"] <= hi]
            mix = _mix(inside)
            phases.append({"i": i, "title": p["title"], "start": lo, "end": hi, "n": len(inside),
                           "group": max(mix, key=mix.get) if mix else "other"})
        changes = []
        for c in card.get("changes") or []:
            trig = [{"id": t, "actor": r[0], "ts": r[1]} for t in c.get("trigger") or []
                    for r in [con.execute("SELECT actor, ts FROM events WHERE id=?", (t,)).fetchone()] if r]
            if c.get("at") in ts:
                changes.append({"at": c["at"], "ts": ts[c["at"]], "kind": c["trigger_kind"], "text": c["before_after"],
                                "trigger": trig})
        lanes.append({"key": key, "kind": kind, "events": len(evs), "first": evs[0]["ts"] if evs else None,
                      "last": evs[-1]["ts"] if evs else None, "role": (card.get("role") or {}).get("text"),
                      "phases": phases, "changes": changes})
    lanes.sort(key=lambda l: -l["events"])
    lanes = sorted(lanes[:limit], key=lambda l: l["first"] or "")
    return {"dataset": dict(con.execute("SELECT key, value FROM meta WHERE key IN ('name', 'description')")),
            "groups": {g: list(t) for g, t in GROUPS.items()}, "buckets": buckets,
            "swarm": swarm, "story": story, "plain": K.PLAIN_GROUP, "lanes": lanes}


def api_card(con, work, q):
    from . import cards as K
    kind, key = q["kind"][0], q["key"][0]
    if kind == "link":
        a, b = key.split("~", 1)
        return K.link(con, a, b)
    return K.get_card(con, kind, key) or K.trajectory(con, key)


def api_links(con, work, q):
    """Link cards and useful-link counts for one actor."""
    a = Q.resolve(con, q["actor"][0])
    rows = con.execute("SELECT src, dst, type, count(*) FROM w.links WHERE src=? OR dst=? GROUP BY src, dst, type "
                       "ORDER BY count(*) DESC LIMIT 40", (a, a)).fetchall()
    return [{"src": r[0], "dst": r[1], "type": r[2], "n": r[3], "key": "~".join(sorted((r[0], r[1])))} for r in rows]


def api_find(con, work, q):
    from . import cards as K
    return K.find(con, q["q"][0])


def api_events(con, work, q):
    """Events of one actor in a time range (an incident's sources), with their notes."""
    actor, lo, hi = Q.resolve(con, q["actor"][0]), q["since"][0], q["until"][0]
    rows = con.execute("SELECT id, ts, text, channel FROM events WHERE actor=? AND ts >= ? AND ts <= ? ORDER BY ts LIMIT ?",
                       (actor, lo, hi, int(q.get("limit", ["60"])[0]))).fetchall()
    return [{"id": r[0], "ts": r[1], "text": r[2], "where": r[3], "note": Q.tag_of(con, r[0])} for r in rows]


def api_concerns(con, work, q):
    from . import alignment as A
    return A.concerns_card(con)


def api_concern_actors(con, work, q):
    from . import alignment as A
    return A.concern_actors(con, int(q.get("limit", ["12"])[0]))


def api_concern_events(con, work, q):
    from . import alignment as A
    return A.concern_events(con, q.get("kind", [None])[0], q.get("actor", [None])[0], limit=int(q.get("limit", ["12"])[0]))


def api_hypotheses(con, work, q):
    """The hypothesis ledger for people: plain text, verdict, basis and the verifiers' evidence."""
    from . import hypotheses as H
    out = []
    for h in H.listing(work, limit=300):
        ver = h.get("verifiers") or {}
        ev = {role: [e for e in (v.get("evidence") or []) if e.get("stance") in ("supports", "contradicts")]
              for role, v in ver.items()}
        rep = h.get("replication") or {}
        basis = h.get("basis") or []
        bev = [{"id": r[0], "ts": r[1], "actor": r[2]} for r in con.execute(
            f"SELECT id, ts, actor FROM events WHERE id IN ({','.join('?' * len(basis))}) ORDER BY ts", basis)] if basis else []
        ts = [b["ts"] for b in bev]
        if not ts:  # hypotheses without a basis are anchored at their first supporting example
            ids = [e["event_id"] for v in ver.values() for e in (v.get("evidence") or [])][:10]
            ts = [r[0] for r in con.execute(f"SELECT ts FROM events WHERE id IN ({','.join('?' * len(ids))}) ORDER BY ts",
                                            ids).fetchall()] if ids else []
        out.append({"id": h["id"], "round": h.get("round") or 1, "statement": h["statement"], "plain": h.get("plain"),
                    "reason": h.get("plain_reason") or h.get("rationale"), "anchor": ts[0] if ts else None, "basis_ev": bev,
                    "verdict": h.get("verdict"), "status": h["status"], "confidence": h.get("confidence"),
                    "plain_note": h.get("plain_note"), "basis": h.get("basis") or [],
                    "sources": (h.get("signal") or {}).get("sources", []),
                    "for": [e["event_id"] for r in ev.values() for e in r if e["stance"] == "supports"][:8],
                    "against": [e["event_id"] for r in ev.values() for e in r if e["stance"] == "contradicts"][:8],
                    "verifiers": sorted(ver), "replicated": rep.get("replicated") if rep else None})
    return out


def api_cell(con, work, q):
    from .structure import swarm_incidents
    return swarm_incidents(con, int(q.get("min_level", ["2"])[0]), cell=(int(q["slot"][0]), q["group"][0]))


def api_incidents(con, work, q):
    from .structure import swarm_incidents
    return swarm_incidents(con, int(q.get("min_level", ["2"])[0]))


_AGENT = {}
SHARE = False  # set by serve(): the page for readers outside the investigation


def api_agent(con, work, q):
    """the people's view of one agent's record (agentview.py); computed once per state of the readings"""
    from . import agentview
    try:
        n = con.execute("SELECT count(*) FROM w.chunk_reads").fetchone()[0]
    except Exception:
        n = 0
    path = con.execute("PRAGMA database_list").fetchone()[2]
    key = (path, os.path.getmtime(path), n // 50)  # not on every new reading
    if _AGENT.get("key") != key:
        _AGENT.update(key=key, data=agentview.data(con, share=SHARE))
    return _AGENT["data"]


def api_atlas(con, work, q):
    from . import atlas
    return atlas.page(con)


def api_features(con, work, q):
    from . import features
    return features.page(con)


def api_spread(con, work, q):
    from . import features
    if q.get("id"):
        return features.stretch_sources(con, q["id"][0])
    return features.spread(con)


def api_flow(con, work, q):
    from . import flow
    if q.get("id"):
        return flow.card_view(con, q["id"][0])
    return flow.page_view(con)


def api_influence(con, work, q):
    from . import features
    b = ((features.get(con) or {}).get("influence") or {}).get("between")
    if not b:
        return {"error": "no influence model"}
    return {"R": b["R"], "placebo": b.get("placebo"), "significant": b["significant"],
            "links": [{k: x[k] for k in ("g", "f", "odds", "dp", "z")} for x in b["links"]]}


def api_feature_storylines(con, work, q):
    from . import story_jobs
    db = con.execute("PRAGMA database_list").fetchone()[2]
    # the storyline from the delegated reading comes first (written for overseers, with every kind of attempt kept);
    # a shared page shows reviewed storylines but never starts agent runs (viewers are outside the investigation)
    from . import storyline
    t = storyline.tour(con, share=SHARE)
    if t:
        return {**t, "can_create": False}
    return {**story_jobs.page(db, con), "can_create": not SHARE}


def api_agent_sources(con, work, q):
    from . import agentview
    return agentview.sources(con, [i for i in q.get("ids", [""])[0].split(",") if i])


ROUTES = {"/api/agent": api_agent, "/api/atlas": api_atlas, "/api/features": api_features, "/api/feature_storylines": api_feature_storylines, "/api/spread": api_spread, "/api/flow": api_flow, "/api/influence": api_influence, "/api/agent_sources": api_agent_sources, "/api/concern_actors": api_concern_actors, "/api/concerns": api_concerns, "/api/concern_events": api_concern_events, "/api/hypotheses": api_hypotheses, "/api/cell": api_cell, "/api/events": api_events, "/api/incidents": api_incidents, "/api/story": api_story, "/api/find": api_find, "/api/card": api_card, "/api/links": api_links, "/api/periods": api_segments, "/api/graph": api_graph, "/api/timeline": api_timeline,
          "/api/episode": api_episode, "/api/edge": api_edge, "/api/msg": api_msg}


class Handler(http.server.BaseHTTPRequestHandler):
    db = None
    share = False
    home = ""
    dbs = {}  # dataset name -> index path (the first is the default); pages pass ?ds=NAME

    def pick(self, q):
        return self.dbs.get((q.get("ds") or [""])[0], self.db)

    def datasets(self):
        out = []
        for name, path in self.dbs.items():
            try:
                con = Q.connect(path)
                label = (con.execute("SELECT value FROM meta WHERE key='name'").fetchone() or [name])[0]
                con.close()
            except Exception:
                label = name
            out.append({"id": name, "label": label})
        return out

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/healthz":  # for container and load-balancer checks
            return self.send(200, json.dumps({"ok": True, "datasets": sorted(self.dbs)}))
        if url.path == "/" and self.home:
            self.send_response(302)
            self.send_header("Location", self.home + (f"?{url.query}" if url.query else ""))
            self.end_headers()
            return
        pages = {"/": "explorer.html", "/index.html": "explorer.html", "/story": "story.html", "/agent": "agent.html", "/atlas": "atlas.html", "/features": "features.html", "/features/full": "features_full.html"}
        if url.path in pages:
            with open(os.path.join(HERE, pages[url.path]), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if url.path == "/api/datasets":
            return self.send(200, json.dumps(self.datasets()))
        if url.path not in ROUTES:
            return self.send(404, json.dumps({"error": "not found"}))
        q = urllib.parse.parse_qs(url.query)
        db = self.pick(q)
        # Storyline status is a read-only capability check, including before any run exists.
        con = Q.connect(db)
        work = None if url.path == "/api/feature_storylines" else open_work(db)
        try:
            out = ROUTES[url.path](con, work, q)
            if self.share:
                from .redact import redact_obj
                out = redact_obj(json.loads(json.dumps(out, default=str)))
            self.send(200, json.dumps(out, default=str))
        except (Q.QueryError, KeyError, ValueError) as ex:
            self.send(400, json.dumps({"error": str(ex)}))
        finally:
            con.close()
            if work is not None:
                work.close()

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path != "/api/feature_storylines":
            return self.send(404, json.dumps({"error": "not found"}))
        if self.share:
            return self.send(403, json.dumps({"error": "creating storylines is not available on a shared page"}))
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.send(415, json.dumps({"error": "JSON request required"}))
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            return self.send(403, json.dumps({"error": "same-origin request required"}))
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 1024:
                raise ValueError("request too large")
            request = json.loads(self.rfile.read(size) or b"{}")
            if request != {}:
                raise ValueError("this action accepts an empty JSON object")
        except (ValueError, json.JSONDecodeError) as ex:
            return self.send(400, json.dumps({"error": str(ex)}))
        from . import story_jobs
        return self.send(202, json.dumps(story_jobs.start(self.pick(urllib.parse.parse_qs(url.query)))))


def serve(db, port, share=False, also=(), host="127.0.0.1", home=""):
    """also: more datasets as 'NAME=INDEX' (pages switch between them with ?ds=NAME); host: the address to listen on
    (0.0.0.0 in a container); home: the page '/' sends visitors to (empty: the network explorer)"""
    global SHARE
    Handler.db = os.path.abspath(db)
    Handler.dbs = {os.path.splitext(os.path.basename(db))[0]: Handler.db}
    for item in also:
        name, _, path = item.partition("=")
        Handler.dbs[name] = os.path.abspath(path)
    for name, path in Handler.dbs.items():
        if not os.path.exists(path):
            raise SystemExit(f"no index for dataset '{name}': {path}")
    Handler.share = SHARE = share
    Handler.home = home
    srv = http.server.ThreadingHTTPServer((host, port), Handler)
    print(f"SwarmScope: http://{host}:{port}{home}  (index {Handler.db}"
          f"{', share mode: technical detail withheld' if share else ''})", flush=True)
    srv.serve_forever()
