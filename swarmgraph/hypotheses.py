"""Hypothesis ledger with pre-registration and verdict gates.

propose → (plan is frozen) → measure (code computes the metric) → add evidence (ids verified against the index)
→ verdict (gated: 'supported' needs the metric to pass and ≥1 verified supporting event; a metric that fails its
expectation can never be overruled into 'supported').
"""
import json
import time

from . import metrics
from .query import QueryError

VERDICTS = ("supported", "refuted", "inconclusive")
STANCES = ("supports", "contradicts", "context")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _row(r):
    d = dict(r)
    for k in ("plan", "metric_result", "evidence", "alternatives", "signal", "basis", "verifiers", "replication"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


def propose(con, work, statement, plan, rationale="", confirm_if="", refute_if="", alternatives=None,
            source="host", parent=None, signal=None):
    if not statement or not isinstance(plan, dict):
        raise QueryError("a hypothesis needs a statement and a plan {metric, params, expect}")
    if plan.get("metric") not in metrics.FUNCS:
        raise QueryError(f"plan.metric must be one of {', '.join(metrics.FUNCS)}")
    exp = plan.get("expect") or {}
    if "op" not in exp or "value" not in exp:
        raise QueryError("plan.expect needs op and value, e.g. {\"op\": \"<\", \"value\": \"others_rate\"}")
    metrics.run(con, plan["metric"], plan.get("params") or {})  # fail fast on bad params, result not stored yet
    cur = work.execute("INSERT INTO hypotheses(created, source, parent, signal, statement, rationale, confirm_if, "
                       "refute_if, alternatives, plan, status, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (_now(), source, parent, json.dumps(signal) if signal else None, statement, rationale,
                        confirm_if, refute_if, json.dumps(alternatives or []), json.dumps(plan), "registered", _now()))
    work.commit()
    return get(work, cur.lastrowid)


def get(work, hid):
    r = work.execute("SELECT * FROM hypotheses WHERE id=?", (int(hid),)).fetchone()
    if not r:
        raise QueryError(f"no hypothesis {hid}")
    return _row(r)


def listing(work, status=None, verdict=None, limit=100):
    sql, v = "SELECT * FROM hypotheses WHERE 1", []
    if status:
        sql += " AND status=?"
        v.append(status)
    if verdict:
        sql += " AND verdict=?"
        v.append(verdict)
    return [_row(r) for r in work.execute(sql + " ORDER BY id DESC LIMIT ?", (*v, limit))]


def measure(con, work, hid):
    h = get(work, hid)
    plan = h["plan"]
    result = metrics.run(con, plan["metric"], plan.get("params") or {})
    passed, observed = metrics.check(result, plan["expect"])
    result["observed"] = observed
    work.execute("UPDATE hypotheses SET metric_result=?, metric_pass=?, status=?, updated=? WHERE id=?",
                 (json.dumps(result), None if passed is None else int(passed),
                  "measured" if h["status"] == "registered" else h["status"], _now(), int(hid)))
    work.commit()
    return {"hypothesis": int(hid), "passed": passed, "observed": observed, "expect": plan["expect"], "result": result}


def add_evidence(con, work, hid, items):
    h = get(work, hid)
    have = h["evidence"] or []
    added = []
    for it in items:
        eid = str(it.get("event_id", "")).strip()
        stance = it.get("stance", "context")
        if stance not in STANCES:
            raise QueryError(f"stance must be one of {STANCES}")
        ok = con.execute("SELECT 1 FROM events WHERE id=?", (eid,)).fetchone() is not None
        row = {"event_id": eid, "stance": stance, "note": it.get("note", ""), "verified": ok}
        have.append(row)
        added.append(row)
    work.execute("UPDATE hypotheses SET evidence=?, status=?, updated=? WHERE id=?",
                 (json.dumps(have), "investigated" if h["status"] in ("registered", "measured") else h["status"],
                  _now(), int(hid)))
    work.commit()
    return {"hypothesis": int(hid), "added": added,
            "unverified": [a["event_id"] for a in added if not a["verified"]]}


def verdict(con, work, hid, decision, confidence="medium", notes=""):
    if decision not in VERDICTS:
        raise QueryError(f"verdict must be one of {VERDICTS}")
    h = get(work, hid)
    if h["metric_pass"] is None and h["metric_result"] is None:
        measure(con, work, hid)
        h = get(work, hid)
    gates = []
    supporting = [e for e in (h["evidence"] or []) if e["stance"] == "supports" and e["verified"]]
    final = decision
    if decision == "supported":
        if h["metric_pass"] == 0:
            final = "inconclusive"
            gates.append("pre-registered metric failed its expectation; 'supported' is not allowed")
        elif h["metric_pass"] is None:
            final = "inconclusive"
            gates.append("metric could not be evaluated (no data in window)")
        if not supporting:
            final = "inconclusive" if final == "supported" else final
            gates.append("no verified supporting event cited")
    contradicting = [e for e in (h["evidence"] or []) if e["stance"] == "contradicts" and e["verified"]]
    if decision == "refuted" and h["metric_pass"] != 0 and not contradicting:
        final = "inconclusive"
        gates.append("the metric did not fail and no verified contradicting event was cited; cannot refute")
    work.execute("UPDATE hypotheses SET verdict=?, confidence=?, judge_notes=?, gate_notes=?, status='judged', updated=? "
                 "WHERE id=?", (final, confidence, notes, "; ".join(gates), _now(), int(hid)))
    work.commit()
    return {"hypothesis": int(hid), "requested": decision, "verdict": final, "gates": gates}


def annotate(work, hid, **fields):
    """Store round, people-facing text, basis, verifier outputs or replication for a hypothesis."""
    allowed = {"round", "plain", "plain_note", "plain_reason", "basis", "verifiers", "replication"}
    sets = {k: (json.dumps(v) if k in ("basis", "verifiers", "replication") else v) for k, v in fields.items() if k in allowed}
    if sets:
        work.execute(f"UPDATE hypotheses SET {', '.join(k + '=?' for k in sets)}, updated=? WHERE id=?",
                     (*sets.values(), _now(), int(hid)))
        work.commit()


def replicate(con, hyp):
    """Re-run the frozen plan on each half of its time window (code only). None if the metric has no window."""
    from .build import epoch
    plan = hyp["plan"]
    params = dict(plan.get("params") or {})
    if "since" not in metrics.CATALOG[plan["metric"]]["params"]:
        return None
    lo = params.get("since") or con.execute("SELECT min(ts) FROM events").fetchone()[0]
    hi = params.get("until") or con.execute("SELECT max(ts) FROM events").fetchone()[0]
    a, b = epoch(lo.replace("T", " ")), epoch(hi.replace("T", " "))
    mid = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime((a + b) / 2))
    halves = []
    for s_, u in ((lo, mid), (mid, hi)):
        try:
            r = metrics.run(con, plan["metric"], {**params, "since": s_, "until": u})
            ok, obs = metrics.check(r, plan["expect"])
        except QueryError as ex:
            ok, obs = None, str(ex)[:80]
        halves.append({"since": s_, "until": u, "observed": obs, "passed": ok})
    return {"halves": halves, "replicated": all(h["passed"] for h in halves)}
