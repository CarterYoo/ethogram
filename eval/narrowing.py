"""Does the ledger narrow what analysts look at, and do they lean on it without checking? (RESULTS section 11)

  python3 eval/narrowing.py OUT_DIR INDEX DATASET_DIR [--conds raw,sgaction,sgaction2]

For every run of the action-layer round (trajectories in OUT_DIR/runs, answers in OUT_DIR/TR_*.json):
  reliance      share of the answer's cited events that are a ledger case or its evidence
  checked       of those, the share the analyst also saw outside the ledger's own output (it opened the event:
                context, get_event, a search, a script over events.jsonl) - leaning on the ledger without looking
                is citing what only the ledger showed
  own search    commands that are not ledger calls, and the characters they returned
  span          weeks (village-goal phases, 9 in all) and days the cited events fall in
  phase share   where the cited events fall, by phase, pooled per condition
"""
import collections
import glob
import json
import os
import re
import sqlite3
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trajectory_forensics import parse_run  # noqa: E402
from transcript_report import cited_ids  # noqa: E402

LEDGER_CALL = re.compile(r"claims_vs_record")


def main(out, index, dataset_dir, conds=("raw", "sgaction", "sgaction2")):
    con = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    ids = {r[0] for r in con.execute("SELECT id FROM events")}
    ts_of = dict(con.execute("SELECT id, ts FROM events"))
    w = sqlite3.connect(f"file:{index}.work?mode=ro", uri=True)
    ledger_ids = set()
    for eid, ev in w.execute("SELECT event_id, evidence FROM claim_checks"):
        ledger_ids.add(eid)
        ledger_ids.update(json.loads(ev))
    phases = [json.loads(l) for l in open(os.path.join(dataset_dir, "phases.jsonl"))]
    starts = [(p["start"].replace("T", " ").rstrip("Z"), p["label"]) for p in phases]

    def phase(ts):
        lab = None
        for s, l in starts:
            if ts >= s:
                lab = l
        return lab or "before"

    rows = collections.defaultdict(list)
    pooled = collections.defaultdict(collections.Counter)
    for cond in conds:
        for f in sorted(glob.glob(os.path.join(out, f"TR_{cond}_*.json"))):
            name = os.path.basename(f)[:-5]
            r = json.load(open(f))
            cited = cited_ids(r.get("answer"), ids)
            steps, tok, _ = parse_run(os.path.join(out, "runs", name + ".events.jsonl"))
            seen_ledger, seen_other = set(), set()
            own_cmds = own_chars = 0
            for s in steps:
                found = set(re.findall(r"[\w\-:.]{8,80}", s["out"])) & ids
                if LEDGER_CALL.search(s["cmd"]):
                    seen_ledger |= found
                else:
                    seen_other |= found
                    own_cmds += 1
                    own_chars += len(s["out"])
            from_ledger = cited & ledger_ids
            checked = {x for x in from_ledger if x in seen_other}
            weeks = {phase(ts_of[x]) for x in cited if x in ts_of}
            days = {ts_of[x][:10] for x in cited if x in ts_of}
            for x in cited:
                if x in ts_of:
                    pooled[cond][phase(ts_of[x])] += 1
            rows[cond].append({"cited": len(cited), "reliance": len(from_ledger) / max(1, len(cited)),
                               "checked": (len(checked) / len(from_ledger)) if from_ledger else None,
                               "only_ledger": len(from_ledger - seen_other), "own_cmds": own_cmds,
                               "own_kchars": own_chars / 1000, "weeks": len(weeks), "days": len(days)})
    order = [c for c in conds if c in rows]
    print(f"{'':52s}" + "".join(c.rjust(12) for c in order))
    m = lambda c, k: st.mean(x[k] for x in rows[c] if x[k] is not None) if any(x[k] is not None for x in rows[c]) else float("nan")
    for k, label, fmt in (("cited", "events cited", "{:.1f}"),
                          ("reliance", "share of cited events that are ledger cases/evidence", "{:.0%}"),
                          ("checked", "  of those, also opened outside the ledger output", "{:.0%}"),
                          ("only_ledger", "  cited events seen ONLY in ledger output (count)", "{:.1f}"),
                          ("own_cmds", "commands that are not ledger calls", "{:.1f}"),
                          ("own_kchars", "  characters they returned (k)", "{:.0f}"),
                          ("weeks", "goal-weeks the cited events cover (of 9)", "{:.1f}"),
                          ("days", "days the cited events cover", "{:.1f}")):
        print(f"{label:52s}" + "".join(fmt.format(m(c, k)).rjust(12) for c in order))
    print("\nwhere the cited events fall (pooled share by village goal):")
    labs = [l for _, l in starts]
    for lab in labs:
        print(f"  {lab[:48]:50s}" + "".join(
            f"{pooled[c][lab] / max(1, sum(pooled[c].values())):.0%}".rjust(12) for c in order))


if __name__ == "__main__":
    a = sys.argv[1:]
    conds = tuple(a[a.index("--conds") + 1].split(",")) if "--conds" in a else ("raw", "sgaction", "sgaction2")
    main(a[0], a[1], a[2], conds)

