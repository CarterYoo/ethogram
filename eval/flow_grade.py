"""Grade analysts' hypotheses at the level of behaviours (RESULTS section 17), next to hyp_test.py's grading over event
text. A condition-blind LLM states each hypothesis as one flow claim over the behaviour dictionary (or says it cannot:
not every hypothesis is about behaviour travelling, following or persisting); code measures the claim on the full
record's atlas with targets from CUT on only (the held-out month), and on the months before CUT for comparison.

  holds         the risk ratio's 95% lower bound is above 1 (with at least 5 exposed cases)
  not_holds     5 or more exposed cases and the lower bound at or below 1
  insufficient  fewer than 5 exposed cases among the held-out targets
  not expressible  the formalizer found no flow claim in the dictionary's terms

Calibration: each expressible claim with its behaviours swapped for random others (same kind and edge) shows how often
a claim passes by accident.

    python3 eval/flow_grade.py run OUT_DIR FULL_INDEX CUT ARM=DIR:CONDITION ...
    python3 eval/flow_grade.py report OUT_DIR
"""
import collections
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from swarmgraph import flow as FL, query as Q  # noqa: E402
from swarmgraph.llm import Agent  # noqa: E402
import hyp_test  # noqa: E402

FORM = """You restate behaviour hypotheses about a multi-agent log as claims that code can measure over a dictionary of
behaviours. AI judges marked these behaviours on stretches of work (one actor's events in a row). Stretches are linked
by contact: reuse (a stretch reuses words another wrote first), reply, address (a mention, then the addressee's next
stretch), channel (the same place within the hour), next (the same actor's next stretch). You do not see any result
and must not try to make a hypothesis come out true: encode what it says.

Claim kinds:
- transmission: behaviour `feature` passes from actor to actor along contact (`edge`: any, reuse, reply, address,
  channel);
- adoption: actors show `feature` for the first time after contact with a stretch that showed it;
- coupling: behaviour `b` follows behaviour `a` along contact (`edge` as above), or within one actor (`edge`: next).
Pick the behaviours whose sentences best match what the hypothesis describes; use only ids from the dictionary. Mark a
hypothesis not expressible when it is not about a behaviour travelling, following another or persisting, or when no
behaviour in the dictionary matches what it describes. Put what the claim leaves out in `reason`.

DICTIONARY
{dictionary}

HYPOTHESES
{items}
"""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["claims"], "properties": {"claims": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
    "required": ["id", "expressible", "kind", "feature", "a", "b", "edge", "reason"],
    "properties": {"id": {"type": "string"}, "expressible": {"type": "boolean"},
                   "kind": {"type": "string", "enum": ["transmission", "adoption", "coupling", "none"]},
                   "feature": {"type": "string"}, "a": {"type": "string"}, "b": {"type": "string"},
                   "edge": {"type": "string", "enum": ["any", "reuse", "reply", "address", "channel", "next"]},
                   "reason": {"type": "string"}}}}}}


def measure(m, c, **window):
    if c["kind"] == "transmission":
        r = m.transmission(c["feature"], c["edge"], **window)
    elif c["kind"] == "adoption":
        r = m.adoption(c["feature"], **window)
    else:
        r = m.coupling(c["a"], c["b"], c["edge"], **window)
    r.pop("examples", None)
    if r["exposed_with"] < FL.MIN_CASES or r["lo"] is None:
        v = "insufficient"
    else:
        v = "holds" if r["lo"] > 1 else "not_holds"
    return {"verdict": v, **{k: r.get(k) for k in ("rr", "lo", "hi", "exposed", "exposed_with")}}


def run(out_dir, full, cut, arms, effort="medium"):
    os.makedirs(out_dir, exist_ok=True)
    hyps = hyp_test.collect(arms)
    m = FL.load(Q.connect(full))
    dictionary = "\n".join(f"{f}: {m.text[f]}" for f in m.fids)
    path = os.path.join(out_dir, "claims.json")
    claims = json.load(open(path)) if os.path.exists(path) else {}
    by_run = collections.defaultdict(list)
    for h in hyps:
        if h["hid"] not in claims:
            by_run[h["run"]].append(h)
    import concurrent.futures

    def form(hs):
        items = "\n".join(f"[{h['hid']}] {h['statement']}" for h in hs)
        out, _ = Agent(effort=effort, timeout=1500, retries=1).run(FORM.format(dictionary=dictionary, items=items),
                                                                   SCHEMA)
        return {c["id"]: c for c in out["claims"]}
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for got in pool.map(form, by_run.values()):
            claims.update(got)
            json.dump(claims, open(path, "w"), indent=1)
    results = {}
    for h in hyps:
        c = claims.get(h["hid"])
        ok = c and c["expressible"] and c["kind"] != "none" and all(
            c[k] in m.label for k in (("feature",) if c["kind"] != "coupling" else ("a", "b")))
        if not ok:
            results[h["hid"]] = {"verdict": "not expressible", "why": (c or {}).get("reason", "missing")}
            continue
        results[h["hid"]] = {"claim": c, "test": measure(m, c, since=cut), "train": measure(m, c, until=cut)}
    rnd = random.Random(17)
    null = []
    for hid, r in results.items():
        if "claim" not in r:
            continue
        for _ in range(3):
            c = dict(r["claim"])
            for k in (("feature",) if c["kind"] != "coupling" else ("a", "b")):
                c[k] = rnd.choice(m.fids)
            null.append(measure(m, c, since=cut)["verdict"])
    json.dump({"hypotheses": hyps, "results": results, "null": null, "cut": cut},
              open(os.path.join(out_dir, "results.json"), "w"), indent=1)
    report(out_dir)


def report(out_dir):
    d = json.load(open(os.path.join(out_dir, "results.json")))
    cnt, train = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    for h in d["hypotheses"]:
        r = d["results"][h["hid"]]
        cnt[h["arm"]]["n"] += 1
        if "test" not in r:
            cnt[h["arm"]]["not expressible"] += 1
            continue
        cnt[h["arm"]][r["test"]["verdict"]] += 1
        train[h["arm"]][r["train"]["verdict"]] += 1
    print(f"held out: targets from {d['cut']} on")
    print(f"{'arm':8s} {'n':>3s} {'expr':>5s} {'holds':>6s} {'not':>5s} {'insuff':>6s}   holds/decided   holds/all   "
          f"before the cut: holds/decided")
    for arm, c in sorted(cnt.items()):
        dec = c["holds"] + c["not_holds"]
        t = train[arm]
        tdec = t["holds"] + t["not_holds"]
        print(f"{arm:8s} {c['n']:3d} {c['n'] - c['not expressible']:5d} {c['holds']:6d} {c['not_holds']:5d} "
              f"{c['insufficient']:6d}   {c['holds']}/{dec} = {c['holds'] / dec if dec else 0:.2f} "
              f"{hyp_test.wilson(c['holds'], dec)}   {c['holds']}/{c['n']} = {c['holds'] / c['n']:.2f}   "
              f"{t['holds']}/{tdec} = {t['holds'] / tdec if tdec else 0:.2f}")
    arms = sorted(cnt)
    for i, a1 in enumerate(arms):
        for a2 in arms[i + 1:]:
            c1, c2 = cnt[a1], cnt[a2]
            p = hyp_test.fisher_two_sided(c1["holds"], c1["n"] - c1["holds"], c2["holds"], c2["n"] - c2["holds"])
            print(f"exact test {a1} vs {a2}, holds/all: p = {p:.3f}")
    n = collections.Counter(d["null"])
    dec = n["holds"] + n["not_holds"]
    print(f"null (behaviours swapped at random): {len(d['null'])} claims, holds {n['holds']}, decided {dec} -> "
          f"{n['holds'] / max(1, dec):.2f} of decided")


if __name__ == "__main__":
    if sys.argv[1] == "report":
        report(sys.argv[2])
    else:
        out_dir, full, cut, rest = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]
        arms = collections.defaultdict(list)
        for a in rest:
            name, spec = a.split("=", 1)
            arms[name].append(spec)
        run(out_dir, full, cut, arms)
