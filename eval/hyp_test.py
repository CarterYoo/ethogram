"""Do the analysts' behaviour hypotheses HOLD when measured? Graded by code against a baseline, not by a judge.

A condition-blind LLM turns each hypothesis into a test over raw event text without seeing any result (patterns for
A, patterns for B, a scope, a window). Code then measures it on the log:

  after_then  after an A-event, does B follow within the window more often than in equal off-windows?
              scope same_actor: B by the A-actor · target: B by the actors the A-event was aimed at (relations) ·
              others_same_place: B by other actors in the same place. Baseline = the same candidates' own off-windows
              (2-3 widths before/after, only windows where they were present AND active; the window after A must also
              have activity), so "this actor does both things" and "agents only work in sessions" cannot pass: the
              question is how often B is done WHEN ACTIVE.
              Only cases that have such a baseline are compared (one-shot labels have none: counted as `unpaired`).
  spreads     A's first uses: do adopters have contact with an earlier adopter more often than chance?
  co_occurs   do actors who do A also do B more than chance?

Verdicts need an effect over the baseline (gap >= 0.10 and lift >= 1.5) AND replication in both time halves AND a
cluster bootstrap over actors whose one-sided 95% lower bound of the gap is above zero (so one prolific actor, or one
burst, cannot carry it; throwaway labels count normally); too little data is 'insufficient', never a pass. Mismatched A/B pairs from the same log calibrate how often the
test passes by accident.

  python3 eval/hyp_test.py run  OUT_DIR  ARM=DIR:CONDITION ...  [--wiki IDX] [--fresh IDX] [--since TS --until TS]
      out-of-sample: --wiki/--fresh = the index the analysts used (their cited events), --test-wiki/--test-fresh = the
      full index to measure on, --ids-wiki/--ids-fresh = file of the held-out event ids
      e.g. raw=../data/ab_hyp:raw raw=../data/ab_hyp2:raw sg=../data/ab_hyp2:swarmgraph
  python3 eval/hyp_test.py report OUT_DIR
"""
import bisect
import collections
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swarmgraph.build import epoch  # noqa: E402
from swarmgraph.llm import Codex  # noqa: E402


from swarmgraph.claims import Log, measure_after_then, measure_co, measure_spread, run_test, verdict, _window  # noqa: E402,F401

# ---------------------------------------------------------------------------- formalizing (LLM, blind to arm)

FORMALIZE = """You turn behaviour hypotheses about a multi-agent event log into machine-checkable tests. Each test is
run by code over ALL events; patterns are Python regular expressions searched case-insensitively in the event TEXT.
You do not see any result and must not try to make a hypothesis come out true — encode what it says.

Every event has two texts that patterns are searched in, joined as "<summary> || <raw text>": a one-line SUMMARY in
normalised language (written by someone who read the event: "Reports X", "Asks Y for Z") and the raw text. Describe
behaviours with words likely to appear in the summary ("report", "relay", "ask", "correct", "defer", "retry", "qualif",
"verify", ...) as well as literal strings from the raw text; use stems (e.g. "retr(y|ies)", "qualif") rather than
single exact words.

Test shapes:
- after_then: an A-event is followed by a B-event within window_minutes (15-360; use ~90 unless the hypothesis implies
  otherwise), compared with equal off-windows. scope says whose B counts: same_actor (the actor who did A),
  target (the actors the A-event was aimed at/addressed), others_same_place (other actors in the same place).
- spreads: the behaviour A (a_patterns only) moves from actor to actor along contact.
- co_occurs: actors who do A also do B (a_patterns, b_patterns), more than chance.
Many hypotheses chain several stages or add side conditions. Do NOT give up on those: test the CORE regularity the
statement is mostly about (usually its first link: what triggers what) and set partial=true. Set testable=false only
when no single A/B (or A) test captures even the core (e.g. it is about the absence of something, or about one
named actor's one-off behaviour). Put what the test leaves out in `reason`.

Patterns: specific words or short phrases the event text would contain if the event is an A (or B) — 3 to 12
alternatives, no generic words that match most events. Each pattern set must match a modest share of events
(roughly 0.1%-30%). Use the cited events (shown with their text) to see the vocabulary of this log.

Hypotheses (id, statement, shape hint) with cited events (id | text):
{items}
"""
FORM_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["tests"], "properties": {"tests": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
    "required": ["id", "testable", "partial", "shape", "a_patterns", "b_patterns", "scope", "window_minutes", "reason"],
    "properties": {"id": {"type": "string"}, "testable": {"type": "boolean"}, "partial": {"type": "boolean"},
                   "shape": {"type": "string", "enum": ["after_then", "spreads", "co_occurs", "none"]},
                   "a_patterns": {"type": "array", "items": {"type": "string"}},
                   "b_patterns": {"type": "array", "items": {"type": "string"}},
                   "scope": {"type": "string", "enum": ["same_actor", "target", "others_same_place"]},
                   "window_minutes": {"type": "integer"}, "reason": {"type": "string"}}}}}}


def evidence_text(log_index, ids, per=3, clip=420):
    con = sqlite3.connect(f"file:{log_index}?mode=ro", uri=True)
    try:
        w = sqlite3.connect(f"file:{log_index}.work?mode=ro", uri=True)
    except sqlite3.OperationalError:
        w = None
    out = []
    for i in ids[:per]:
        r = con.execute("SELECT text FROM events WHERE id=?", (i,)).fetchone()
        if r:
            sm = w.execute("SELECT summary FROM tags WHERE event_id=?", (i,)).fetchone() if w else None
            out.append(f"    {i} | summary: {(sm[0] if sm else '') or '-'} | text: {' '.join((r[0] or '').split())[:clip]}")
    return "\n".join(out)


def formalize(index, hyps, effort="medium", feedback=None):
    items = []
    for h in hyps:
        fb = f"\n    NOTE from validation: {feedback[h['hid']]}" if feedback and h["hid"] in feedback else ""
        items.append(f"[{h['hid']}] {h['statement']}  (hint: {h['pattern']}){fb}\n" + evidence_text(index, h["evidence"]))
    out, _ = Codex(effort=effort, timeout=1500, retries=1).run(FORMALIZE.format(items="\n".join(items)), FORM_SCHEMA)
    return {t["id"]: t for t in out["tests"]}


def valid_spec(log, t, lo=0.0003, hi=0.30):
    """patterns must compile and match a modest share of events; returns (ok, message)"""
    if not t["testable"] or t["shape"] == "none":
        return False, "not testable: " + t["reason"][:100]
    need = ["a_patterns"] + ([] if t["shape"] == "spreads" else ["b_patterns"])
    for k in need:
        if not t[k]:
            return False, f"{k} empty"
        try:
            m = sum(log.match(t[k]))
        except re.error as ex:
            return False, f"{k} invalid regex ({ex})"
        if m < max(8, lo * log.n) or m > hi * log.n:
            return False, f"{k} matches {m} of {log.n} events (need roughly {int(lo * log.n)}-{int(hi * log.n)})"
    return True, ""


# ---------------------------------------------------------------------------- run / report

def collect(arms):
    """arms: {arm: ["DIR:CONDITION", ...]} → list of hypothesis dicts"""
    out = []
    for arm, specs_ in arms.items():
        for spec in specs_:
            d, cond = spec.rsplit(":", 1)
            for f in sorted(os.listdir(d)):
                m = re.match(r"^(WH|FH|AH)_(raw|swarmgraph|rawtool|sgfixed|sgfeat|sgflow)_(\d+)\.json$", f)
                if not m or m.group(2) != cond:
                    continue
                r = json.load(open(os.path.join(d, f)))
                for k, h in enumerate((r.get("answer") or {}).get("hypotheses", [])):
                    out.append({"hid": f"{os.path.basename(d.rstrip('/'))}.{f[:-5]}.h{k + 1}", "arm": arm,
                                "dataset": {"WH": "wiki", "FH": "fresh", "AH": "big"}[m.group(1)], "run": f"{d}/{f[:-5]}",
                                "statement": h["statement"], "pattern": h["pattern"], "evidence": h["evidence"]})
    return out


def run(out_dir, arms, indexes, since=None, until=None, effort="medium", only_ids=None, test_indexes=None):
    os.makedirs(out_dir, exist_ok=True)
    hyps = collect(arms)
    # indexes: where the analysts' cited events live (formalizer reads them); test_indexes: what is measured on
    ids = {ds: (set(open(p).read().split()) if p else None) for ds, p in (only_ids or {}).items()}
    logs = {ds: Log((test_indexes or indexes)[ds], since, until, ids.get(ds)) for ds in indexes}
    path = os.path.join(out_dir, "tests.json")
    specs = json.load(open(path)) if os.path.exists(path) else {}
    todo_runs = collections.defaultdict(list)
    for h in hyps:
        if h["hid"] not in specs:
            todo_runs[(h["dataset"], h["run"])].append(h)
    # 1. formalize per run, in parallel (blind: the prompt never names the arm), validate, retry once with match counts
    import concurrent.futures

    def form_group(item):
        (ds, _), hs = item
        got = formalize(indexes[ds], hs, effort)
        bad, good, last = {}, {}, dict(got)
        for h in hs:
            t = got.get(h["hid"])
            ok, msg = valid_spec(logs[ds], t) if t else (False, "missing")
            if ok:
                good[h["hid"]] = {**t, "valid": True}
            else:
                bad[h["hid"]] = msg
        retry = [h for h in hs if h["hid"] in bad and not bad[h["hid"]].startswith("not testable")]
        if retry:
            got2 = formalize(indexes[ds], retry, effort, feedback=bad)
            last.update(got2)
            for h in retry:
                t = got2.get(h["hid"])
                ok, msg = valid_spec(logs[ds], t) if t else (False, "missing")
                if ok:
                    good[h["hid"]] = {**t, "valid": True}
                    bad.pop(h["hid"])
                else:
                    bad[h["hid"]] = msg
        for hid, msg in bad.items():
            keep = {k: last[hid][k] for k in ("shape", "a_patterns", "b_patterns", "scope", "window_minutes")
                    if hid in last and k in last[hid]}
            good[hid] = {"valid": False, "why": msg, **keep}
        return ds, len(hs), len(bad), good

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for ds, n, nbad, good in pool.map(form_group, list(todo_runs.items())):
            specs.update(good)
            json.dump(specs, open(path, "w"), indent=1)
            print(f"formalized {n} for {ds} ({nbad} untestable)", flush=True)
    # 2. measure
    train_logs = {ds: Log(ix) for ds, ix in indexes.items()} if test_indexes else {}
    results = {}
    for h in hyps:
        s = specs[h["hid"]]
        if not s.get("valid"):
            results[h["hid"]] = {"verdict": "untestable", "why": s.get("why")}
        else:
            try:
                results[h["hid"]] = {**run_test(logs[h["dataset"]], s), "partial": bool(s.get("partial"))}
            except re.error as ex:  # a pattern too slow on the log it is measured on
                results[h["hid"]] = {"verdict": "untestable", "why": str(ex)[:160]}
    # 3. calibration: A of one hypothesis with B of another (unrelated), same scope and window
    rnd = random.Random(11)
    null = collections.defaultdict(list)
    for ds in logs:
        pool = [(h["hid"], specs[h["hid"]]) for h in hyps if h["dataset"] == ds and specs[h["hid"]].get("valid")
                and specs[h["hid"]]["shape"] == "after_then"]
        for hid, s in pool:
            for _ in range(3):
                other = rnd.choice([p for p in pool if p[0] != hid])[1]
                spec = {"shape": "after_then", "a_patterns": s["a_patterns"], "b_patterns": other["b_patterns"],
                        "scope": s["scope"], "window_minutes": s["window_minutes"]}
                try:
                    null[ds].append(run_test(logs[ds], spec)["verdict"])
                except re.error:
                    null[ds].append("untestable")
    for h in hyps:  # recurrence: do the described events occur in the held-out part at all?
        sp = specs[h["hid"]]
        if train_logs and sp.get("a_patterns"):
            try:
                tr = train_logs[h["dataset"]]
                te = logs[h["dataset"]]
                rec = {"a_train": sum(tr.match(sp["a_patterns"])), "a_test": sum(te.match(sp["a_patterns"]))}
                if sp.get("shape") != "spreads" and sp.get("b_patterns"):
                    rec.update(b_train=sum(tr.match(sp["b_patterns"])), b_test=sum(te.match(sp["b_patterns"])))
                results[h["hid"]]["recurrence"] = rec
            except re.error:
                pass
    json.dump({"hypotheses": hyps, "results": results, "null": null, "window": [since, until],
               "only_ids": {k: v for k, v in (only_ids or {}).items()}},
              open(os.path.join(out_dir, "results.json"), "w"), indent=1)
    report(out_dir)


def fisher_two_sided(a, b, c, d):
    """exact two-sided p for the 2x2 table [[a, b], [c, d]] (stdlib)"""
    n1, n2, k, n = a + b, c + d, a + c, a + b + c + d
    lo, hi = max(0, k - n2), min(k, n1)
    pmf = lambda x: math.comb(n1, x) * math.comb(n2, k - x) / math.comb(n, k)
    p0 = pmf(a)
    return min(1.0, sum(pmf(x) for x in range(lo, hi + 1) if pmf(x) <= p0 * (1 + 1e-9)))


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    dn = 1 + z * z / n
    c = (p + z * z / (2 * n)) / dn
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / dn
    return [round(c - h, 2), round(c + h, 2)]


def report(out_dir):
    d = json.load(open(os.path.join(out_dir, "results.json")))
    hyps, res = d["hypotheses"], d["results"]
    cnt = collections.defaultdict(collections.Counter)
    per_run = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for h in hyps:
        v = res[h["hid"]]["verdict"]
        cnt[("all", h["arm"])][v] += 1
        cnt[(h["dataset"], h["arm"])][v] += 1
        per_run[h["arm"]][h["hid"].rsplit(".h", 1)[0]][v] += 1
    print(f"window {d['window']}")
    print(f"{'dataset':7s} {'arm':16s} {'n':>3s} {'holds':>6s} {'not':>5s} {'insuff':>6s} {'untest':>6s}   holds/decided (95% CI)      holds/all")
    summary = {}
    for (ds, arm), c in sorted(cnt.items()):
        n = sum(c.values())
        dec = c["holds"] + c["not_holds"]
        ci = wilson(c["holds"], dec)
        summary[f"{ds}/{arm}"] = {"n": n, **c, "holds_of_decided": round(c["holds"] / dec, 2) if dec else None,
                                  "holds_of_all": round(c["holds"] / n, 2)}
        print(f"{ds:7s} {arm:16s} {n:3d} {c['holds']:6d} {c['not_holds']:5d} {c['insufficient']:6d} {c['untestable']:6d}   "
              + (f"{c['holds']:2d}/{dec:2d} = {c['holds'] / dec:.2f} {ci}" if dec else "-").ljust(34)
              + f" {c['holds']}/{n} = {c['holds'] / n:.2f}")
    arms = sorted({a for (ds, a) in cnt if ds == "all"})
    print("\nper run (5 hypotheses each): mean number that HOLD, mean number decided")
    for arm in arms:
        runs = per_run[arm]
        hs = [r["holds"] for r in runs.values()]
        ds_ = [r["holds"] + r["not_holds"] for r in runs.values()]
        print(f"  {arm:16s} {len(runs):2d} runs   holds {sum(hs) / len(hs):.2f}   decided {sum(ds_) / len(ds_):.2f}")
    print("\nexact test (two-sided Fisher) between arms")
    for i, a1 in enumerate(arms):
        for a2 in arms[i + 1:]:
            c1, c2 = cnt[("all", a1)], cnt[("all", a2)]
            n1, n2 = sum(c1.values()), sum(c2.values())
            p_all = fisher_two_sided(c1["holds"], n1 - c1["holds"], c2["holds"], n2 - c2["holds"])
            d1, d2 = c1["holds"] + c1["not_holds"], c2["holds"] + c2["not_holds"]
            p_dec = fisher_two_sided(c1["holds"], c1["not_holds"], c2["holds"], c2["not_holds"]) if d1 and d2 else None
            print(f"  {a1} vs {a2}: holds/all p={p_all:.3f}" + (f"  holds/decided p={p_dec:.3f}" if p_dec is not None else ""))
    for ds, v in d["null"].items():
        c = collections.Counter(v)
        dec = c["holds"] + c["not_holds"]
        print(f"null (mismatched A/B pairs) {ds}: {len(v)} pairs, holds {c['holds']}, not {c['not_holds']}, "
              f"insufficient {c['insufficient']} → accidental pass rate {c['holds'] / len(v):.3f} of all pairs"
              + (f", {c['holds'] / dec:.2f} of decided" if dec else ""))
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w"), indent=1)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "report":
        report(sys.argv[2])
    else:
        out_dir, args = sys.argv[2], sys.argv[3:]
        arms, idx, since, until = collections.defaultdict(list), {}, None, None
        tidx, ids = {}, {}
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--since":
                since = args[i + 1]; i += 2
            elif a == "--until":
                until = args[i + 1]; i += 2
            elif a.startswith("--test-"):      # --test-wiki / --test-fresh / --test-big INDEX_TO_MEASURE_ON
                tidx[a[7:]] = args[i + 1]; i += 2
            elif a.startswith("--ids-"):       # --ids-wiki / --ids-fresh / --ids-big FILE_OF_HELD_OUT_EVENT_IDS
                ids[a[6:]] = args[i + 1]; i += 2
            elif a.startswith("--"):           # --wiki / --fresh / --big INDEX (the one the analysts used)
                idx[a[2:]] = args[i + 1]; i += 2
            else:
                arm, d = a.split("=", 1)
                arms[arm].append(d); i += 1
        run(out_dir, arms, idx, since, until, only_ids=ids, test_indexes=tidx or None)
