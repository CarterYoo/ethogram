"""Where did each analyst's evidence come from, and did it matter? Works on any set of conditions (raw, swarmgraph,
rawtool, sgfixed), unlike trajectory_forensics.py (two conditions, command-line tools only).

  python3 eval/arm_forensics.py RUN_DIR[:RUN_DIR2...] [--hold GRADED_DIR] [--hold-seen GRADED_DIR_FOR_THE_SEEN_PART]

Every shell command of a run is given a source by what it uses: `structure` (our boards and lookups, as a command-line
tool or a Python import), `claims` (grep / context / test_claim, either way), `raw` (the log files or the index read
directly), `source` (reading our code or docs, no events). An event id counts as first seen by the source of the first
command whose output contains it. A final hypothesis is `structure-led` if any of its cited events was first seen
through a structure command, `claims-led` if none was but one was first seen through a claims command, `raw-led`
otherwise. With --hold the code-graded outcome (events the analyst never saw) is tabulated against that.
"""
import collections
import glob
import json
import os
import re
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trajectory_forensics import ID, parse_run  # noqa: E402

STRUCTURE_TOOLS = ("map|node|theme_map|theme|overview|select_events|event_fields|actors|segments|signals|trajectory|link|"
                   "changes|find|phase_events|tag_profile|behaviors|concerns|concern_events|concern_actors|quality|"
                   "actor_card|interactions|timeline|search|get_event")
STRUCTURE = re.compile(rf"swarmgraph --db \S+ call ({STRUCTURE_TOOLS})\b|swarmgraph\.(query|brief|themes|tools)\b")
CLAIMS = re.compile(r"swarmgraph --db \S+ call (grep|context|test_claim)\b|swarmgraph\.claims|from swarmgraph import claims")
READ_SOURCE = re.compile(r"\b(sed|cat|rg|head|ls|grep)\b.*(swarmgraph/|SKILL\.md|CLAIMS\.md)")
RAW = re.compile(r"events\.jsonl|actors\.jsonl|sqlite3|log\.sqlite|\bsqlite3\b")


def source_of(cmd):
    if STRUCTURE.search(cmd):
        return "structure"
    if CLAIMS.search(cmd):
        return "claims"
    if READ_SOURCE.search(cmd) and "python" not in cmd:
        return "source"
    if RAW.search(cmd):
        return "raw"
    return "other"


def lead_kind(first_src):
    if "structure" in first_src:
        return "structure-led"
    if "claims" in first_src:
        return "claims-led"
    return "raw-led"


def load(run_dirs, holds):
    arms = collections.defaultdict(list)
    for run_dir in run_dirs:
        tag = os.path.basename(run_dir.rstrip("/"))
        for f in sorted(glob.glob(os.path.join(run_dir, "runs", "*.events.jsonl"))):
            name = os.path.basename(f).split(".")[0]
            _, cond, _ = name.split("_")
            steps, tok, _ = parse_run(f)
            ans = json.load(open(os.path.join(run_dir, name + ".json")))
            hyps = (ans.get("answer") or {}).get("hypotheses", [])
            first = {}
            per_src = collections.Counter()
            chars = collections.Counter()
            for s in steps:
                src = source_of(s["cmd"])
                per_src[src] += 1
                chars[src] += len(s["out"])
                for i in set(ID.findall(s["out"])):
                    first.setdefault(i, src)
            row = {"name": name, "tag": tag, "tokens": (tok or {}).get("input_tokens"), "seconds": ans.get("seconds"),
                   "steps": len(steps), "per_src": per_src, "chars": chars,
                   "shown": collections.Counter(first.values()), "hyps": []}
            for k, h in enumerate(hyps):
                srcs = [first.get(x, "unseen") for x in h.get("evidence", [])]
                hid = f"{tag}.{name}.h{k + 1}"
                row["hyps"].append({"hid": hid, "srcs": srcs, "lead": lead_kind(srcs),
                                    "outcome": {lbl: res[hid]["verdict"] for lbl, res in holds.items() if hid in res}})
            arms[cond].append(row)
    return arms


def main():
    args = sys.argv[1:]
    run_dirs = args[0].split(":")
    holds = {}
    for flag, label in (("--hold", "held-out"), ("--hold-seen", "seen")):
        if flag in args:
            d = json.load(open(os.path.join(args[args.index(flag) + 1], "results.json")))
            holds[label] = d["results"]
    arms = load(run_dirs, holds)
    order = [c for c in ("raw", "swarmgraph", "rawtool", "sgfixed") if c in arms]
    mean = lambda c, f: st.mean(f(r) for r in arms[c])
    print(f"{'':44s}" + "".join(c.rjust(12) for c in order))
    print(f"{'runs':44s}" + "".join(str(len(arms[c])).rjust(12) for c in order))
    for label, f, fmt in (("input tokens (k)", lambda r: (r['tokens'] or 0) / 1000, "{:.0f}"),
                          ("minutes", lambda r: (r['seconds'] or 0) / 60, "{:.1f}"),
                          ("commands", lambda r: r['steps'], "{:.1f}")):
        print(f"{label:44s}" + "".join(fmt.format(mean(c, f)).rjust(12) for c in order))
    print("\ncommands per run by source, and characters returned (k)")
    for src in ("structure", "claims", "raw", "source", "other"):
        cells = []
        for c in order:
            n = mean(c, lambda r: r["per_src"][src])
            ch = mean(c, lambda r: r["chars"][src] / 1000)
            cells.append(f"{n:4.1f}/{ch:4.0f}k".rjust(12))
        print(f"  {src:42s}" + "".join(cells))
    print("\ndistinct event ids per run, by the source that first showed them")
    for src in ("structure", "claims", "raw", "other"):
        print(f"  {src:42s}" + "".join(f"{mean(c, lambda r: r['shown'][src]):.0f}".rjust(12) for c in order))
    print("\nfinal hypotheses by how their cited events were first seen")
    for c in order:
        cnt = collections.Counter(h["lead"] for r in arms[c] for h in r["hyps"])
        print(f"  {c:12s} " + "  ".join(f"{k} {cnt[k]}" for k in ("structure-led", "claims-led", "raw-led")))
    for label in holds:
        print(f"\ncode-graded outcome on the {label} events, by how the evidence was first seen")
        for c in order:
            tab = collections.defaultdict(collections.Counter)
            for r in arms[c]:
                for h in r["hyps"]:
                    v = h["outcome"].get(label)
                    if v:
                        tab[h["lead"]][v] += 1
            for lead in ("structure-led", "claims-led", "raw-led"):
                if lead in tab:
                    t = tab[lead]
                    n = sum(t.values())
                    print(f"  {c:11s} {lead:14s} n={n:3d}  holds {t['holds']:2d}  not {t['not_holds']:2d}  "
                          f"insufficient {t['insufficient']:2d}  untestable {t['untestable']:2d}  holds/all {t['holds'] / n:.2f}")


if __name__ == "__main__":
    main()

