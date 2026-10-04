"""Prepare the July / August flow experiment (RESULTS section 17): a copy of the July-only index that RESULTS
section 7 built (aiv-julaug_oos_train: events before August, notes, tags and themes from July only) with a behaviour
atlas restricted to July, taken from the full record's judged stretches.

Judgments are made stretch by stretch, so a July stretch's marks do not depend on August. What does come from both
months: the dictionary's wording (proposed from samples of both), the two-judge agreement and blind-test scores shown
with each behaviour, and the judges' "removed later" fact (a window of 30 days) for stretches late in July.

    python3 eval/flow_split.py FULL_INDEX TRAIN_INDEX OUT_INDEX CUT
    e.g. python3 eval/flow_split.py ../data/aiv-julaug.sqlite ../data/aiv-julaug_oos_train.sqlite \
             ../data/aiv-julaug_flowtrain.sqlite 2026-08-01
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swarmgraph import features as FE, flow as FL, query as Q  # noqa: E402
from swarmgraph.store import open_work  # noqa: E402


def copy(src, dst):
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    a.backup(b)
    a.close()
    b.close()


def main(full, train, out, cut):
    for s, d in ((train, out), (train + ".work", out + ".work")):
        copy(s, d)
    d = FE.get(Q.connect(full))
    a = d["atlas"]
    con = Q.connect(out)
    _, us = FE.units(con)
    by_key = {(u["actor"], u["start"]): u for u in us}
    rows, missed = [], 0
    for u in a["units"]:
        if u["start"] >= cut:
            continue
        v = by_key.get((u["actor"], u["start"]))
        if v is None:  # a stretch the window cut (it ran past midnight on the last day)
            missed += 1
            continue
        rows.append({**u, "id": v["id"], "end": v["end"], "n": len(v["ids"]), "ids": v["ids"][:40]})
    uniform = {r["id"]: {fid: x for fid, x in r["f"].items()} for r in rows if r.get("uniform", True)}
    bins, prof, n_bin = FE.profiles(us, uniform, a["features"])
    feats = []
    for f in a["features"]:
        on = [r for r in rows if r["f"].get(f["id"])]
        if len(on) < 3:
            continue
        feats.append({**f, "units": len(on), "actors": len({r["actor"] for r in on}),
                      "density": round(sum(1 for r in on if r["id"] in uniform) / max(1, len(uniform)), 4)})
    kept = {f["id"] for f in feats}
    atlas = {**a, "features": feats, "bins": bins, "n_bin": n_bin, "units": rows,
             "profiles": {k: v for k, v in prof.items() if k in kept},
             "fpos": {k: v for k, v in a["fpos"].items() if k in kept},
             "coverage": f"{len(rows):,} of {len(us):,} stretches before {cut}; timeline from a uniform sample of "
                         f"{len(uniform):,}"}
    work = open_work(out)
    for k in ("dictionary", "checks", "short_names"):
        if k in d:
            FE.save(work, k, d[k])
    FE.save(work, "atlas", atlas)
    for p in (out, out + ".work"):  # whole files: the experiment copies them with shutil
        c = sqlite3.connect(p)
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
    m = FL.load(Q.connect(out))
    last = max(m.u[i]["start"] for i in m.has)
    print({"judged": len(rows), "uniform": len(uniform), "missed_at_cut": missed, "features": len(feats),
           "last_judged": last, "regimes": m.regimes()["k"]})
    assert last < cut


if __name__ == "__main__":
    main(*sys.argv[1:5])
