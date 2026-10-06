"""Are the hypotheses that hold also worth having? A hypothesis can hold because it is shallow ("after consent is
mentioned to an agent, it talks about consent": the conversation stays on topic). A blind judge reads only the
regularity itself (method and numbers cut out, so it cannot tell which condition wrote it) and says what kind of
regularity it is and how much it tells about the agents.

  python3 eval/hyp_depth.py judge OUT.json RUN_DIR[:RUN_DIR2...]      # 20 hypotheses per Codex call, shuffled across arms
  python3 eval/hyp_depth.py report OUT.json [GRADED_DIR [GRADED_DIR_SEEN]]

kind: topic_echo (after a subject is raised or put to an agent, the same subject comes back: conversation stays on topic,
a request is answered), workflow_step (steps of the work itself: request -> completion report, failure -> workaround),
agent_trait (what one named model/agent does), social_dynamic (influence, adoption, norms, authority, conflict,
claims versus actions). insight 1-5: 1 = obvious to anyone who knows agents are collaborating on tasks, 5 = a
non-obvious regularity about how agents adapt, coordinate, influence or misreport.
"""
import collections
import concurrent.futures
import glob
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swarmgraph.llm import Agent  # noqa: E402

PROMPT = """You rate behaviour hypotheses that analysts wrote about a log of AI agents working on tasks together
(project work, chat, publishing, coordination). You see only the regularity each analyst stated. Do not run commands.

For each hypothesis return:
- kind: topic_echo | workflow_step | agent_trait | social_dynamic
    topic_echo      after a subject is raised, mentioned or put to an agent, the same subject comes back in what
                    that agent says next (it stays on topic, acknowledges, repeats, answers a request in kind)
    workflow_step   a step of the work process: request -> completion report, failure -> workaround, check -> fix
    agent_trait     what one named model or agent characteristically does
    social_dynamic  influence, adoption of others' practices, norms, authority, conflict, or claims versus actions
- insight: 1-5. 1 = obvious to anyone who knows that agents collaborate on tasks; 3 = a plausible regularity worth
  knowing; 5 = a non-obvious regularity about how agents adapt, coordinate, influence each other or misreport.
- obvious: true if a reader would predict it without looking at any data.

Hypotheses:
{items}
"""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["n", "kind", "insight", "obvious"],
    "properties": {"n": {"type": "integer"}, "kind": {"type": "string", "enum": ["topic_echo", "workflow_step", "agent_trait", "social_dynamic"]},
                   "insight": {"type": "integer"}, "obvious": {"type": "boolean"}}}}}}


def clean(statement):
    """the regularity only: cut the analyst's report of its own test (it names the tool and gives numbers)"""
    out = []
    statement = re.sub(r"^(Tentative |Candidate )?[Hh]ypothesis,?( not supported by test_claim)?:\s*", "", statement.strip())
    statement = re.sub(r",? (more often )?than (in|during|at) (comparable|other|baseline)[\w ]*windows", "", statement)
    for s in re.split(r"(?<=[.!?])\s+", statement):
        if re.search(r"test_claim|Raw-text|\d+(\.\d+)?%|baseline|comparable (cases|windows)", s, re.I):
            continue
        out.append(s)
    return " ".join(out) or statement.split(". ")[0]


def collect(run_dirs):
    hs = []
    for run_dir in run_dirs:
        tag = os.path.basename(run_dir.rstrip("/"))
        for f in sorted(glob.glob(os.path.join(run_dir, "AH_*.json"))):
            name = os.path.basename(f)[:-5]
            _, cond, rep = name.split("_")
            for k, h in enumerate(((json.load(open(f)).get("answer") or {}).get("hypotheses") or [])):
                hs.append({"hid": f"{tag}.{name}.h{k + 1}", "cond": cond, "text": clean(h["statement"]), "pattern": h.get("pattern")})
    return hs


def judge(out, run_dirs, workers=6, batch=20):
    hs = collect(run_dirs)
    done = json.load(open(out)) if os.path.exists(out) else {}
    todo = [h for h in hs if h["hid"] not in done]
    random.Random(int(os.environ.get("HYP_DEPTH_SEED", "7"))).shuffle(todo)  # arms mixed inside every call
    groups = [todo[i:i + batch] for i in range(0, len(todo), batch)]

    def run(g):
        items = "\n".join(f"{i + 1}. {h['text']}" for i, h in enumerate(g))
        res, _ = Agent(effort="medium", timeout=1500, retries=1).run(PROMPT.format(items=items), SCHEMA)
        return g, res

    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        for g, res in pool.map(run, groups):
            for r in res["items"]:
                if 1 <= r["n"] <= len(g):
                    done[g[r["n"] - 1]["hid"]] = {k: r[k] for k in ("kind", "insight", "obvious")}
            json.dump(done, open(out, "w"), indent=1)
            print(f"judged {len(done)}/{len(hs)}", flush=True)
    return done


def report(out, graded=None):
    from math import comb
    done = json.load(open(out))
    cond_of = {}
    for hid in done:
        cond_of[hid] = hid.split(".")[1].split("_")[1]
    order = [c for c in ("raw", "swarmgraph", "rawtool", "sgfixed") if c in set(cond_of.values())]
    print(f"{'':12s}{'n':>5s}{'topic_echo':>12s}{'workflow':>10s}{'agent_trait':>13s}{'social':>8s}{'obvious':>9s}{'insight':>9s}")
    for c in order:
        ids = [h for h in done if cond_of[h] == c]
        kinds = collections.Counter(done[h]["kind"] for h in ids)
        n = len(ids)
        print(f"{c:12s}{n:5d}" + "".join(f"{kinds[k] / n:12.0%}"[:12].rjust(w) for k, w in
              (("topic_echo", 12), ("workflow_step", 10), ("agent_trait", 13), ("social_dynamic", 8)))
              + f"{sum(done[h]['obvious'] for h in ids) / n:9.0%}{sum(done[h]['insight'] for h in ids) / n:9.2f}")
    if not graded:
        return
    res = json.load(open(os.path.join(graded, "results.json")))["results"]
    print("\nheld-out outcome by kind (holds / all)")
    for c in order:
        row = []
        for k in ("topic_echo", "workflow_step", "agent_trait", "social_dynamic"):
            ids = [h for h in done if cond_of[h] == c and done[h]["kind"] == k and h in res]
            row.append(f"{k} {sum(res[h]['verdict'] == 'holds' for h in ids)}/{len(ids)}")
        non = [h for h in done if cond_of[h] == c and done[h]["kind"] != "topic_echo" and h in res]
        row.append(f"NOT topic_echo {sum(res[h]['verdict'] == 'holds' for h in non)}/{len(non)}")
        hi = [h for h in done if cond_of[h] == c and done[h]["insight"] >= 3 and h in res]
        row.append(f"insight>=3 {sum(res[h]['verdict'] == 'holds' for h in hi)}/{len(hi)}")
        print(f"  {c:11s} " + "  ".join(row))


if __name__ == "__main__":
    if sys.argv[1] == "judge":
        judge(sys.argv[2], sys.argv[3].split(":"))
    else:
        report(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
