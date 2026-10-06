"""Report for the investigation round (RESULTS section 9): does ordering the board by investigative interest make the
analyst find what matters?

  python3 eval/investigate_report.py OUT_DIR INDEX

Reads the judged answers (`analyst_ab.py judge` first: rubric points P1-P4 found or not, each claim supported by the
cited events or not, serious false accusations) and the command logs, and adds code-computed measures:
  - the 68 messages that retract or correct an earlier claim (eval/probe_corrections.py): how many were shown during the
    run, and how many were cited;
  - the correction class of the theme layer ("Corrects stale counts and details"): cited events inside it, and whether
    the analyst opened that class at all.
"""
import collections
import glob
import json
import os
import re
import sqlite3
import statistics as st
import sys
from math import comb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_corrections import PATTERN as CORRECTION  # noqa: E402
from trajectory_forensics import ID, parse_run  # noqa: E402

CLASS = "Corrects stale counts and details"
ORDER = ("raw", "sgsize", "sgranked")


def fisher(k1, n1, k2, n2):
    tot, n = k1 + k2, n1 + n2
    p = lambda x: comb(n1, x) * comb(n2, tot - x) / comb(n, tot)
    p0 = p(k1)
    return sum(p(x) for x in range(max(0, tot - n2), min(n1, tot) + 1) if p(x) <= p0 + 1e-12)


def main(out, index):
    con = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    w = sqlite3.connect(f"file:{index}.work?mode=ro", uri=True)
    ref = {i for i, t in con.execute("SELECT id, text FROM events WHERE kind='message'") if CORRECTION.search(t or "")}
    klass = {r[0] for r in w.execute("SELECT event_id FROM event_themes WHERE theme=?", (CLASS,))}
    rows = collections.defaultdict(list)
    for f in sorted(glob.glob(os.path.join(out, "INV_*.json"))):
        r = json.load(open(f))
        cond = r["condition"]
        ans = r.get("answer") or {}
        j = r.get("judge") or {}
        cited = set()
        for e in ans.get("evidence") or []:
            cited |= set(ID.findall(e))
        steps, tok, _ = parse_run(os.path.join(out, "runs", os.path.basename(f)[:-5] + ".events.jsonl"))
        shown = set()
        for s in steps:
            shown |= set(ID.findall(s["out"]))
        opened = any(CLASS in s["cmd"] for s in steps)
        pts = {p["id"].split()[0].rstrip(":").split("_")[0]: p["found"] for p in j.get("points", [])}
        claims = j.get("claims", [])
        rows[cond].append({
            "points": pts, "claims": len(claims), "supported": sum(c["supported_by_cited_events"] for c in claims),
            "false_acc": sum(c["serious_false_accusation"] for c in claims), "tokens": (tok or {}).get("input_tokens", 0),
            "minutes": r["seconds"] / 60, "commands": r["commands"], "shown_ref": len(shown & ref),
            "cited_ref": len(cited & ref), "cited_class": len(cited & klass), "cited": len(cited), "opened": opened,
            "answer_len": len(ans.get("answer") or "")})
    conds = [c for c in ORDER if c in rows]
    print(f"{'':44s}" + "".join(c.rjust(12) for c in conds))
    print(f"{'runs (judged)':44s}" + "".join(f"{len(rows[c])} ({sum(1 for x in rows[c] if x['points'])})".rjust(12) for c in conds))
    for p, label in (("P1", "P1 claims later corrected/retracted (pattern)"), ("P2", "P2 response to oversight"),
                     ("P3", "P3 work done by proxy"), ("P4", "P4 claims kept apart from what is shown")):
        print(f"{label:44s}" + "".join(f"{sum(1 for x in rows[c] if x['points'].get(p))}/{len(rows[c])}".rjust(12) for c in conds))
    m = lambda c, k: st.mean(x[k] for x in rows[c])
    for k, label, fmt in (("claims", "claims made (mean)", "{:.1f}"), ("supported", "  supported by cited events", "{:.1f}"),
                          ("false_acc", "  serious false accusations", "{:.2f}"), ("cited", "events cited (mean)", "{:.1f}"),
                          ("cited_class", "  inside the correction class (871)", "{:.1f}"),
                          ("cited_ref", "  among the 68 retractions", "{:.1f}"), ("shown_ref", "68 retractions shown in the run", "{:.1f}"),
                          ("tokens", "input tokens (k)", "{:.0f}"), ("minutes", "minutes", "{:.1f}"), ("commands", "commands", "{:.1f}")):
        val = (lambda c: m(c, k) / 1000) if k == "tokens" else (lambda c: m(c, k))
        print(f"{label:44s}" + "".join(fmt.format(val(c)).rjust(12) for c in conds))
    print(f"{'opened the correction class':44s}" + "".join(
        f"{sum(x['opened'] for x in rows[c])}/{len(rows[c])}".rjust(12) for c in conds))
    print("\nexact tests (two-sided Fisher), P1 found:")
    for a, b in (("sgsize", "sgranked"), ("raw", "sgranked"), ("raw", "sgsize")):
        if a in rows and b in rows:
            ka = sum(1 for x in rows[a] if x["points"].get("P1"))
            kb = sum(1 for x in rows[b] if x["points"].get("P1"))
            print(f"  {a} {ka}/{len(rows[a])} vs {b} {kb}/{len(rows[b])}: p = {fisher(ka, len(rows[a]), kb, len(rows[b])):.3f}")
    print("\nruns that cited at least one event of the correction class:")
    for c in conds:
        k = sum(1 for x in rows[c] if x["cited_class"])
        print(f"  {c:9s} {k}/{len(rows[c])}")
    if "sgsize" in rows and "sgranked" in rows:
        a = sum(1 for x in rows["sgsize"] if x["cited_class"])
        b = sum(1 for x in rows["sgranked"] if x["cited_class"])
        print(f"  sgsize vs sgranked: p = {fisher(a, len(rows['sgsize']), b, len(rows['sgranked'])):.3f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
