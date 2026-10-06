"""Read-only queries over an index. Each returns plain JSON-able data; every row carries event ids."""
import collections
import json
import os
import sqlite3


class QueryError(ValueError):
    pass


def connect(db_path):
    """Read-only index connection; the work store (tags, ledger) is attached read-only as `w` when present."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    work = db_path + ".work"
    if os.path.exists(work):
        try:
            con.execute("ATTACH DATABASE ? AS w", (f"file:{work}?mode=ro",))
        except sqlite3.OperationalError:
            # a WAL-mode work store copied without its -shm file cannot be opened read-only: let SQLite create the
            # side files once, or, where the folder is not writable, read it as an unchanging file
            try:
                sqlite3.connect(work).execute("PRAGMA schema_version").fetchone()
                con.execute("ATTACH DATABASE ? AS w", (f"file:{work}?mode=ro",))
            except sqlite3.OperationalError:
                con.execute("ATTACH DATABASE ? AS w", (f"file:{work}?mode=ro&immutable=1",))
    return con


def clip(text, n=280):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n] + " …"


def resolve(con, name):
    """Actor by id, label, alias, or unique substring of a label."""
    if name is None:
        return None
    for sql in ("SELECT id FROM actors WHERE id = ?", "SELECT id FROM actors WHERE lower(label) = lower(?)",
                "SELECT actor_id FROM aliases WHERE alias = lower(?)"):
        row = con.execute(sql, (name,)).fetchone()
        if row:
            return row[0]
    rows = con.execute("SELECT id, label FROM actors WHERE lower(label) LIKE ? OR lower(id) LIKE ?",
                       ("%" + name.lower() + "%", "%" + name.lower() + "%")).fetchall()
    if len(rows) == 1:
        return rows[0][0]
    if rows:
        raise QueryError(f"'{name}' is ambiguous: " + ", ".join(f"{r[1]} ({r[0]})" for r in rows[:10]))
    raise QueryError(f"no actor matches '{name}'")


def window(alias="", since=None, until=None, segment=None, ts="ts"):
    p = alias + "." if alias else ""
    sql, vals = "", []
    if since:
        sql += f" AND {p}{ts} >= ?"
        vals.append(since.replace("T", " "))
    if until:
        sql += f" AND {p}{ts} < ?"
        vals.append(until.replace("T", " "))
    if segment:
        sql += f" AND {p}segment_id = ?"
        vals.append(int(segment))
    return sql, vals


def labels(con):
    return {r["id"]: (f"{r['role']} ({r['label']})" if r["role"] and r["role"] != r["label"] else r["label"])
            for r in con.execute("SELECT id, label, role FROM actors")}


# ---------------------------------------------------------------------------- queries

def overview(con):
    meta = dict(con.execute("SELECT key, value FROM meta"))
    span = con.execute("SELECT min(ts), max(ts), count(*) FROM events").fetchone()
    kinds = dict(con.execute("SELECT kind, count(*) FROM actors GROUP BY kind"))
    ev_kinds = dict(con.execute("SELECT kind, count(*) FROM events GROUP BY kind"))
    top = [dict(r) for r in con.execute(
        "SELECT a.id, a.label, a.role, count(*) events FROM events e JOIN actors a ON a.id = e.actor "
        "GROUP BY a.id ORDER BY events DESC LIMIT 12")]
    lineages = [dict(r) for r in con.execute(
        "SELECT lineage, count(*) members FROM actors WHERE lineage IS NOT NULL GROUP BY lineage "
        "HAVING members > 1 ORDER BY members DESC LIMIT 20")]
    speaks, base = float(meta.get("addressed_speaks_rate", 0)), float(meta.get("addressed_speaks_base_rate", 0))
    return {"dataset": {k: meta.get(k) for k in ("name", "description", "notes", "built_at", "segmenting")},
            "scope_note": "dataset text and actor lists were written by the dataset's author and may cover more than the "
                          "events indexed here (a longer period, actors active elsewhere); time_range, event counts and "
                          "most_active below are computed from the indexed events and are authoritative",
            "time_range": [span[0], span[1]], "events": span[2], "event_kinds": ev_kinds, "actor_kinds": kinds,
            "segments": con.execute("SELECT count(*) FROM segments").fetchone()[0],
            "relations": json.loads(meta.get("relations", "{}")), "relation_methods": json.loads(meta.get("methods", "{}")),
            "most_active": top, "lineages": lineages,
            "event_fields": {k: {"events": v["events"], "common_values": v["common_values"]}
                             for k, v in list(event_fields(con)["fields"].items())[:15]},
            "relation_guide": "Relation types say what the data or the converter records, not that actors talked: "
                              "method 'reply_field' follows the data's own reply_to field (its meaning is in the "
                              "dataset notes above), 'mention' an @name in text, 'llm_text' an addressee an LLM read "
                              "from the text. To select events by what was recorded (e.g. a deletion), use "
                              "select_events with kind / meta, not text search.",
            "quality": {"addressed_actor_speaks_within_window": speaks, "same_at_random_times": base,
                        "note": "'replied' requires an explicit address back; speaking soon after being addressed "
                                "is common by chance (compare the two rates above)."}}


def event_fields(con, sample=None, max_values=8):
    """Fields of event metadata (and event kinds / channels) with how often they occur and their common values, so
    events can be selected by what the data records rather than by words in their text."""
    keys = collections.defaultdict(collections.Counter)
    n = 0
    for (m,) in con.execute("SELECT meta FROM events WHERE meta IS NOT NULL AND meta <> '' LIMIT ?", (sample or -1,)):
        try:
            d = json.loads(m)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        n += 1
        for k, v in d.items():
            keys[k][json.dumps(v)[:60] if not isinstance(v, (dict, list)) else "(object)"] += 1
    out = {}
    for k, c in sorted(keys.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(c.values())
        out[k] = {"events": total, "distinct_values": len(c),
                  "common_values": [json.loads(v) if v != "(object)" else v for v, _ in c.most_common(max_values)]
                  if len(c) <= 50 else "(many distinct values)"}
    return {"events_with_meta": n, "fields": out,
            "use": "select_events {meta: {field: value}} to select, group_by: 'meta.<field>' to count"}


def select_events(con, kind=None, actor=None, channel=None, meta=None, text=None, segment=None, since=None, until=None,
                  group_by=None, limit=50):
    """Events selected by recorded fields (kind, actor, channel, metadata values, plain substring), with the exact
    total; or counts grouped by kind, actor, channel, day or a metadata field. Code only, no interpretation."""
    w, v = window("e", since, until, segment)
    if kind:
        w += " AND e.kind = ?"
        v.append(kind)
    if actor:
        w += " AND e.actor = ?"
        v.append(resolve(con, actor))
    if channel:
        w += " AND e.channel = ?"
        v.append(channel)
    for k, val in (meta or {}).items():
        if not str(k).replace("_", "").replace(".", "").isalnum():
            raise QueryError(f"bad metadata field {k!r}")
        if val is None:
            w += f" AND json_extract(e.meta, '$.{k}') IS NULL"
        else:
            w += f" AND json_extract(e.meta, '$.{k}') = ?"
            v.append(val if not isinstance(val, bool) else int(val))
    if text:
        w += " AND instr(lower(e.text), lower(?)) > 0"
        v.append(text)
    total = con.execute(f"SELECT count(*) FROM events e WHERE 1{w}", v).fetchone()[0]
    if group_by:
        if group_by in ("kind", "actor", "channel"):
            g = f"e.{group_by}"
        elif group_by == "day":
            g = "substr(e.ts, 1, 10)"
        elif group_by.startswith("meta.") and group_by[5:].replace("_", "").isalnum():
            g = f"json_extract(e.meta, '$.{group_by[5:]}')"
        else:
            raise QueryError("group_by: kind | actor | channel | day | meta.<field>")
        rows = con.execute(f"SELECT {g} k, count(*) n, min(e.ts), max(e.ts), count(DISTINCT e.actor) FROM events e "
                           f"WHERE 1{w} GROUP BY k ORDER BY n DESC LIMIT ?", [*v, limit]).fetchall()
        groups = con.execute(f"SELECT count(DISTINCT {g}) FROM events e WHERE 1{w}", v).fetchone()[0]
        return {"total": total, "groups": groups, "shown": len(rows), "by": group_by,
                "note": f"showing the {len(rows)} largest of {groups} groups; raise limit for more",
                "counts": [{"value": r[0], "events": r[1], "first": r[2], "last": r[3], "actors": r[4]} for r in rows]}
    rows = con.execute(f"SELECT e.id, e.ts, e.actor, e.kind, e.channel, e.meta, e.text FROM events e WHERE 1{w} "
                       "ORDER BY e.ts LIMIT ?", [*v, limit]).fetchall()
    return {"total": total, "shown": len(rows),
            "events": [{"id": r[0], "ts": r[1], "actor": r[2], "kind": r[3], "channel": r[4],
                        "meta": json.loads(r[5]) if r[5] else None, "text": clip(r[6], 300)} for r in rows]}


def segments(con):
    return [dict(r) for r in con.execute(
        "SELECT s.id, s.label, s.start, s.end, count(DISTINCT n.actor) actors, coalesce(sum(n.n_events), 0) events "
        "FROM segments s LEFT JOIN nodes n ON n.segment_id = s.id GROUP BY s.id ORDER BY s.id")]


def actors(con, segment=None, since=None, until=None, kind=None, limit=100):
    w, v = window("e", since, until, segment)
    kw = " AND a.kind = ?" if kind else ""
    rows = con.execute(
        "SELECT a.id, a.label, a.role, a.kind, a.model, a.lineage, a.parent, count(e.id) events FROM actors a "
        f"LEFT JOIN events e ON e.actor = a.id{w} WHERE 1{kw} GROUP BY a.id HAVING events > 0 ORDER BY events DESC LIMIT ?",
        [*v, *([kind] if kind else []), limit]).fetchall()
    out = []
    rw, rv = window("", since, until, segment)
    for r in rows:
        o = con.execute(f"SELECT count(*), count(answered_by) FROM relations WHERE src=? AND type='addressed'{rw}",
                        [r["id"], *rv]).fetchone()
        i = con.execute(f"SELECT count(*), count(answered_by) FROM relations WHERE dst=? AND type='addressed'{rw}",
                        [r["id"], *rv]).fetchone()
        out.append({**dict(r), "addressed_out": o[0], "out_answered_rate": round(o[1] / o[0], 3) if o[0] else None,
                    "addressed_in": i[0], "in_answered_rate": round(i[1] / i[0], 3) if i[0] else None})
    return out


def _peers(con, me, direction, rtype, w, v, limit=8):
    mine, other = ("src", "dst") if direction == "out" else ("dst", "src")
    names = labels(con)
    return [{"actor": r[0], "name": names.get(r[0], r[0]), "n": r[1], "answered": r[2]} for r in con.execute(
        f"SELECT {other}, count(*) n, count(answered_by) FROM relations WHERE {mine}=? AND type=?{w} "
        f"GROUP BY {other} ORDER BY n DESC LIMIT ?", [me, rtype, *v, limit])]


def actor_card(con, name, segment=None, since=None, until=None, work=None):
    aid = resolve(con, name)
    a = dict(con.execute("SELECT * FROM actors WHERE id=?", (aid,)).fetchone())
    w, v = window("", since, until, segment)
    names = labels(con)
    children = [{"actor": r[0], "name": names.get(r[0], r[0]), "n": r[1], "method": r[2]} for r in con.execute(
        "SELECT dst, count(*), group_concat(DISTINCT method) FROM relations WHERE src=? AND type='invoked' GROUP BY dst", (aid,))]
    invoked_by = [{"actor": r[0], "name": names.get(r[0], r[0]), "n": r[1], "method": r[2]} for r in con.execute(
        "SELECT src, count(*), group_concat(DISTINCT method) FROM relations WHERE dst=? AND type='invoked' GROUP BY src", (aid,))]
    lineage = [dict(r) for r in con.execute("SELECT id, label, role FROM actors WHERE lineage=? AND id<>?",
                                            (a["lineage"], aid))] if a["lineage"] else []
    nodes = [dict(r) for r in con.execute(
        "SELECT n.segment_id, s.label segment, n.n_events, n.n_out, n.n_in, n.first_ts, n.last_ts FROM nodes n "
        "JOIN segments s ON s.id = n.segment_id WHERE n.actor=? ORDER BY n.segment_id", (aid,))]
    sr = con.execute(f"SELECT id, ts, text FROM events WHERE actor=? AND kind='self_report'{w} ORDER BY ts DESC LIMIT 1",
                     [aid, *v]).fetchone()
    card = {"actor": a, "invoked_by": invoked_by or ([{"actor": a["parent"], "name": names.get(a["parent"])}] if a["parent"] else []),
            "invoked": children, "same_lineage": lineage,
            "addressed_to": _peers(con, aid, "out", "addressed", w, v),
            "addressed_by": _peers(con, aid, "in", "addressed", w, v),
            "nodes": nodes[-40:], "nodes_total": len(nodes),
            "latest_self_report": {"event_id": sr["id"], "ts": sr["ts"], "excerpt": clip(sr["text"], 1200),
                                   "warning": "the actor's own claims — verify against events"} if sr else None}
    from .tags import available, profile
    if available(con):
        p = profile(con, aid, since, until, segment)
        card["behaviour_profile"] = {"tagged_events": p["tagged_events"], "tags": p["tags"],
                                     "free_labels": p["free_labels"][:8]}
    if work is not None and segment:
        row = work.execute("SELECT result, refs, grounded FROM summaries WHERE actor=? AND segment_id=?",
                           (aid, int(segment))).fetchone()
        if row:
            card["llm_summary"] = {"result": json.loads(row[0]), "refs": json.loads(row[1]), "grounded": row[2]}
    return card


def interactions(con, a, b=None, rtype=None, segment=None, since=None, until=None, limit=40, newest=False):
    aid, bid = resolve(con, a), resolve(con, b) if b else None
    w, v = window("r", since, until, segment)
    cond = "(r.src=? AND r.dst=?) OR (r.src=? AND r.dst=?)" if bid else "r.src=? OR r.dst=?"
    args = [aid, bid, bid, aid] if bid else [aid, aid]
    tw = " AND r.type=?" if rtype else ""
    total = con.execute(f"SELECT count(*) FROM relations r WHERE ({cond}){tw}{w}", [*args, *([rtype] if rtype else []), *v]).fetchone()[0]
    names = labels(con)
    rows = con.execute(
        f"SELECT r.type, r.src, r.dst, r.ts, r.event_id, r.method, r.confidence, r.answered_by, r.answers, e.text, e.url "
        f"FROM relations r LEFT JOIN events e ON e.id = r.event_id WHERE ({cond}){tw}{w} "
        f"ORDER BY r.ts {'DESC' if newest else 'ASC'} LIMIT ?", [*args, *([rtype] if rtype else []), *v, limit]).fetchall()
    summary = {}
    for t, s, d, n, ans in con.execute(
            f"SELECT r.type, r.src, r.dst, count(*), count(r.answered_by) FROM relations r WHERE ({cond}){tw}{w} "
            f"GROUP BY r.type, r.src, r.dst", [*args, *([rtype] if rtype else []), *v]):
        summary[f"{t}: {names.get(s, s)} → {names.get(d, d)}"] = {"n": n, "answered": ans}
    return {"total": total, "summary": summary, "rows": [
        {"type": r["type"], "from": names.get(r["src"], r["src"]), "to": names.get(r["dst"], r["dst"]), "ts": r["ts"],
         "event_id": r["event_id"], "method": r["method"], "confidence": r["confidence"],
         "answered_by": r["answered_by"], "answers": r["answers"], "text": clip(r["text"]), "url": r["url"]} for r in rows]}


def timeline(con, since, until, actor=None, rtype="addressed", limit=200):
    if not (since and until):
        raise QueryError("timeline needs since and until (ISO timestamps); keep windows small")
    w, v = window("r", since, until)
    aw, av = ("", []) if not actor else (" AND (r.src=? OR r.dst=?)", [resolve(con, actor)] * 2)
    names = labels(con)
    rows = con.execute(
        f"SELECT r.ts, r.src, r.dst, r.type, r.event_id, r.answered_by, e.text, e.url FROM relations r "
        f"LEFT JOIN events e ON e.id = r.event_id WHERE r.type=?{w}{aw} ORDER BY r.ts LIMIT ?",
        [rtype, *v, *av, limit]).fetchall()
    return [{"ts": r["ts"], "from": names.get(r["src"], r["src"]), "to": names.get(r["dst"], r["dst"]), "type": r["type"],
             "event_id": r["event_id"], "answered_by": r["answered_by"], "text": clip(r["text"], 200), "url": r["url"]}
            for r in rows]


def search(con, query, actor=None, kind=None, segment=None, since=None, until=None, limit=30):
    w, v = window("e", since, until, segment)
    if actor:
        w += " AND e.actor = ?"
        v.append(resolve(con, actor))
    if kind:
        w += " AND e.kind = ?"
        v.append(kind)
    names = labels(con)
    try:
        rows = con.execute(
            "SELECT e.id, e.ts, e.actor, e.kind, e.channel, e.url, snippet(events_fts, 0, '«', '»', ' … ', 32) hit "
            f"FROM events_fts JOIN events e ON e.rid = events_fts.rowid WHERE events_fts MATCH ?{w} ORDER BY e.ts LIMIT ?",
            [query, *v, limit]).fetchall()
    except sqlite3.OperationalError as ex:
        raise QueryError(f"bad search query ({ex}); quote phrases like '\"ghost PR\"'")
    return [{"event_id": r["id"], "ts": r["ts"], "actor": names.get(r["actor"], r["actor"]), "kind": r["kind"],
             "channel": r["channel"], "text": " ".join(r["hit"].split()), "url": r["url"]} for r in rows]


def get_event(con, event_id, max_chars=6000):
    e = con.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not e:
        raise QueryError(f"no event {event_id}")
    e = dict(e)
    names = labels(con)
    e["actor_name"] = names.get(e["actor"], e["actor"])
    e["text"] = e["text"][:max_chars] + (" …" if len(e["text"]) > max_chars else "")
    e["meta"] = json.loads(e["meta"]) if e["meta"] else None
    e.pop("rid", None)
    e["relations"] = [{"type": r[0], "from": names.get(r[1], r[1]), "to": names.get(r[2], r[2]), "method": r[3],
                       "answered_by": r[4]} for r in con.execute(
        "SELECT type, src, dst, method, answered_by FROM relations WHERE event_id=?", (event_id,))]
    e["replies"] = [r[0] for r in con.execute("SELECT id FROM events WHERE reply_to=? ORDER BY ts LIMIT 20", (event_id,))]
    e["behaviour"] = tag_of(con, event_id)
    return e


def tag_of(con, event_id):
    """LLM behaviour tag of one event (an interpretation of the text, not evidence by itself), or None."""
    try:
        r = con.execute("SELECT tags, other, summary, addressed_to, signed_as, confidence, responds_to, continues, "
                        "claims, stated_goal FROM w.tags WHERE event_id=?", (event_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return {"tags": json.loads(r[0]), "other": r[1], "summary": r[2], "addressed_to": json.loads(r[3]),
            "signed_as": r[4], "confidence": r[5], "responds_to": r[6] or None, "continues": r[7] or None,
            "claims": json.loads(r[8]) if r[8] else [], "stated_goal": r[9] or None} if r else None

