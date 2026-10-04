"""Check that the stored results reproduce: no LLM calls, a few minutes.

    MAPS_PY=.venv/bin/python python3 scripts/verify.py [DATA_DIR]

For each served dataset, copies the index and its work store to a temporary folder, rebuilds the behaviour atlas there
from the stored LLM outputs (the judges' marks in the feature folder: deterministic code, seeded UMAP) and compares it
with the stored atlas: behaviours, judged stretches, agreement between the two calibration judges, coverage, the
layout and the time profiles. Also checks that the stories the page needs (storyline, arc) and the short names are
stored. Exits non-zero on any difference. Run the unit tests separately: python3 -m unittest discover -s tests
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from swarmgraph import features as FE, query as Q  # noqa: E402

# dataset -> (feature folder, judge folders: the first two are the calibration pair)
DATASETS = {"wiki_all": ("wiki_all_features", "judge1,judge2,judge_new"),
            "aiv-julaug": ("aiv_features", "judge1,judge2,judge_more")}


def copy(src, dst):
    """a consistent copy of an SQLite file (whatever is still in its write-ahead log included)"""
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    a.backup(b)
    a.close()
    b.close()


def summary(db):
    d = FE.get(Q.connect(db))
    a = d["atlas"]
    return {"features": sorted(f["id"] for f in a["features"]), "units": len(a["units"]), "coverage": a["coverage"],
            "fpos": a["fpos"], "unit_xy": {u["id"]: (u["x"], u["y"]) for u in a["units"]},
            "profiles": a["profiles"], "checks": d.get("checks"), "kappa": median_kappa(a["features"])}


def median_kappa(fs):
    """as build() reports it: the median two-judge kappa over the judged behaviours kept on the map"""
    ks = [f["reliability"]["kappa"] for f in fs if f.get("source") == "judged" and (f.get("reliability") or {}).get("kappa") is not None]
    return round(sorted(ks)[len(ks) // 2], 3) if ks else None


def main(data):
    maps_py = os.environ.get("MAPS_PY", os.path.join(ROOT, ".venv", "bin", "python"))
    bad = 0
    for name, (folder, judges) in DATASETS.items():
        db = os.path.join(data, f"{name}.sqlite")
        if not os.path.exists(db):
            print(f"{name}: no index at {db}, skipped")
            continue
        stories = {k for k, in sqlite3.connect(db + ".work").execute("SELECT kind FROM stories")}
        page = FE.page(Q.connect(db))
        with tempfile.TemporaryDirectory() as tmp:
            t = os.path.join(tmp, f"{name}.sqlite")
            copy(db, t)
            copy(db + ".work", t + ".work")
            r = subprocess.run([maps_py, "-m", "swarmgraph", "--db", t, "features", "build", os.path.join(data, folder),
                                "--judges", judges], cwd=ROOT, capture_output=True, text=True)
            if r.returncode:
                print(f"{name}: rebuild failed\n{r.stderr[-1500:]}")
                bad += 1
                continue
            old, new = summary(db), summary(t)
        diff = [k for k in old if old[k] != new[k]]
        missing = [k for k in ("storyline", "arc") if k not in stories]
        named = len(page.get("short", {}))
        ok = not diff and not missing and named == len(old["features"])
        bad += not ok
        print(f"{name}: {'OK ' if ok else 'DIFF'} {len(old['features'])} behaviours, {old['units']} judged stretches, "
              f"median kappa {old['kappa']}, {named} short names, stories {sorted(stories)}"
              + (f" | differs: {diff}" if diff else "") + (f" | missing: {missing}" if missing else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(ROOT), "data"))
