"""What does an analyst actually do between receiving the log and writing its hypotheses? Reads the Codex trajectories
of an A/B round (commands, outputs, tokens) and the code-graded outcome of each hypothesis.

  python3 eval/trajectory_forensics.py RUN_DIR [HOLD_DIR]

Per run: every shell command is classified (reading the guide, calling our tools, reading our source code or database,
sampling the log, regex probing, looking up given ids, other scripts), with the characters it returned; whether any
script computed a conditional rate against a baseline; which events the analyst saw (distinct ids, actors, days);
and where each cited event first appeared (our tool output vs raw reading).
"""
import collections
import glob
import json
import os
import re
import statistics as st
import sys

ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|save:[^\s\"',\]]+|delete:[^\s\"',\]]+")
TIME = re.compile(r"timedelta|fromisoformat|strptime|datetime|bisect|epoch|\['ts'\]\s*[-<>]|\.ts\s*[-<>]|seconds|minutes|within")
RATE = re.compile(r"rate|share|fraction|ratio|baseline|base_|/\s*len\(|/\s*n\b|percent|\* ?100|lift|prob")
PROBE = re.compile(r"re\.(search|compile|findall|match)")


def classify(cmd):
    c = re.sub(r"^/bin/(ba|z)sh -lc ", "", cmd).strip()
    if "SKILL.md" in c and re.search(r"\b(cat|sed|head)\b", c):
        return "read_guide"
    m = re.search(r"swarmgraph --db \S+ call (\w+)", c)
    if m:
        return "tool:" + m.group(1)
    if re.search(r"swarmgraph/", c) and re.search(r"\b(sed|cat|rg|ls|head|grep)\b", c):
        return "read_our_source"
    if re.search(r"sqlite3|log\.sqlite", c) and "events.jsonl" not in c:
        return "read_our_db"
    if re.match(r"^(ls|pwd|wc|head|tail|cat actors|rg --files)", c) or c.startswith("'ls") or c.startswith("'pwd"):
        return "orient"
    if "events.jsonl" in c or "actors.jsonl" in c:
        if re.search(r"random|\[::|most_common|Counter\(", c) and not PROBE.search(c):
            return "survey_raw"
        if PROBE.search(c):
            return "regex_probe_raw"
        if re.search(r"ids\s*=|in ids|\bid'\]\s*in\b", c):
            return "id_lookup_raw"
        return "other_raw_script"
    return "other"


def is_quantitative(cmd):
    """a script that compares something over time AND computes a rate/share/baseline"""
    return bool(TIME.search(cmd)) and bool(RATE.search(cmd)) and "python" in cmd


def parse_run(path):
    steps, tokens, msgs = [], None, []
    for line in open(path):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        it = e.get("item") or {}
        if e.get("type") == "turn.completed":
            tokens = e["usage"]
        if e.get("type") == "item.completed" and it.get("type") == "command_execution":
            steps.append({"cmd": it["command"], "out": it.get("aggregated_output") or "", "kind": classify(it["command"])})
        if e.get("type") == "item.completed" and it.get("type") == "agent_message":
            msgs.append(it["text"])
    return steps, tokens, msgs


def main(run_dir, hold_dir=None):
    outcome = {}
    if hold_dir:
        d = json.load(open(os.path.join(hold_dir, "results.json")))
        for h in d["hypotheses"]:
            outcome[h["hid"]] = (h["arm"], d["results"][h["hid"]], h)
    arms = collections.defaultdict(list)
    for f in sorted(glob.glob(os.path.join(run_dir, "runs", "*.events.jsonl"))):
        name = os.path.basename(f).split(".")[0]
        q, cond, rep = name.split("_")
        steps, tok, msgs = parse_run(f)
        ans = json.load(open(os.path.join(run_dir, name + ".json")))
        hyps = (ans.get("answer") or {}).get("hypotheses", [])
        seen_tool, seen_raw, order = set(), set(), []
        for s in steps:
            ids = set(ID.findall(s["out"]))
            tool = s["kind"].startswith("tool:")
            for i in ids:
                if i not in seen_tool and i not in seen_raw:
                    (seen_tool if tool else seen_raw).add(i)
        cited = [x for h in hyps for x in h["evidence"]]
        by = collections.defaultdict(lambda: [0, 0])
        for s in steps:
            by[s["kind"]][0] += 1
            by[s["kind"]][1] += len(s["out"])
        pre = 0  # characters returned before the first command that looks at event content
        for s in steps:
            if s["kind"] in ("regex_probe_raw", "id_lookup_raw", "survey_raw", "other_raw_script") or s["kind"] == "tool:theme":
                break
            pre += len(s["out"])
        arms[cond].append({
            "name": name, "steps": len(steps), "by": by, "tokens": tok["input_tokens"] if tok else None,
            "out_tokens": tok["output_tokens"] if tok else None, "seconds": ans["seconds"],
            "quant": sum(is_quantitative(s["cmd"]) for s in steps),
            "scripts": sum(1 for s in steps if "python" in s["cmd"] and "events.jsonl" in s["cmd"]),
            "seen": len(seen_tool | seen_raw), "cited": len(set(cited)),
            "cited_from_tool": len(set(cited) & seen_tool), "pre_chars": pre, "hyps": hyps,
            "raw_chars": sum(len(s["out"]) for s in steps if s["kind"].endswith("_raw") or s["kind"] == "other_raw_script"),
        })
    print(f"{'':28s} {'raw':>10s} {'swarmgraph':>12s}")
    rows = {c: arms.get(c, []) for c in ("raw", "swarmgraph")}
    avg = lambda c, f: (st.mean(f(r) for r in rows[c]) if rows[c] else float("nan"))
    def line(label, f, fmt="{:10.1f}"):
        print(f"{label:28s} " + " ".join(fmt.format(avg(c, f)).rjust(12) for c in ("raw", "swarmgraph")))
    print(f"{'runs':28s} " + " ".join(str(len(rows[c])).rjust(12) for c in ("raw", "swarmgraph")))
    line("commands", lambda r: r["steps"])
    line("input tokens (k)", lambda r: r["tokens"] / 1000, "{:10.0f}")
    line("seconds", lambda r: r["seconds"], "{:10.0f}")
    line("chars read before first look at event content (k)", lambda r: r["pre_chars"] / 1000, "{:10.0f}")
    line("chars of raw-event output read (k)", lambda r: r["raw_chars"] / 1000, "{:10.0f}")
    line("distinct event ids shown", lambda r: r["seen"], "{:10.0f}")
    line("distinct ids cited", lambda r: r["cited"], "{:10.0f}")
    line("  of which first shown by our tools", lambda r: r["cited_from_tool"], "{:10.1f}")
    line("scripts over events.jsonl", lambda r: r["scripts"])
    line("scripts computing a rate over time", lambda r: r["quant"], "{:10.2f}")
    print(f"{'runs with >=1 rate-over-time script':28s} " + " ".join(
        f"{sum(1 for r in rows[c] if r['quant'])}/{len(rows[c])}".rjust(12) for c in ("raw", "swarmgraph")))
    kinds = sorted({k for c in rows for r in rows[c] for k in r["by"]})
    print("\ncommands per run (mean) and chars returned (k) by kind")
    for k in kinds:
        cells = []
        for c in ("raw", "swarmgraph"):
            n = avg(c, lambda r: r["by"][k][0]) if rows[c] else 0
            ch = avg(c, lambda r: r["by"][k][1] / 1000) if rows[c] else 0
            cells.append(f"{n:5.1f} / {ch:5.0f}k".rjust(12))
        print(f"  {k:26s} " + " ".join(cells))
    if outcome:
        print("\nhypothesis outcome vs where the evidence came from (structure arm)")
        tab = collections.defaultdict(collections.Counter)
        for r in rows["swarmgraph"]:
            seen_tool = set()
            steps, _, _ = parse_run(os.path.join(run_dir, "runs", r["name"] + ".events.jsonl"))
            for s in steps:
                if s["kind"].startswith("tool:"):
                    seen_tool |= set(ID.findall(s["out"]))
            for k, h in enumerate(r["hyps"]):
                hid = f"{os.path.basename(run_dir.rstrip('/'))}.{r['name']}.h{k + 1}"
                if hid not in outcome:
                    continue
                v = outcome[hid][1]["verdict"]
                used = any(x in seen_tool for x in h["evidence"])
                tab["uses a tool-shown event" if used else "raw reading only"][v] += 1
        for k, c in tab.items():
            dec = c["holds"] + c["not_holds"]
            print(f"  {k:26s} n={sum(c.values()):3d} holds {c['holds']:2d} not {c['not_holds']:2d} insufficient {c['insufficient']:2d} "
                  f"untestable {c['untestable']:2d}  holds/all {c['holds'] / max(1, sum(c.values())):.2f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)

