"""Split a prepared dataset into a TRAIN part the analyst may see and a TEST part hypotheses are graded on.

The train index is rebuilt from the train events only, and the work store (notes, tags, concern flags, themes) is
copied only for train events, so nothing the analyst's tools show was computed from test events. Dropped on purpose:
cards written from the whole log (trajectory/link/swarm, concerns, environment). Kept: the theme vocabulary (35 names
and definitions discovered from a sample of the whole log — a small leak of vocabulary, not of counts or events).

    python3 eval/split_dataset.py DATASET_DIR FULL_INDEX OUT_PREFIX --cut TS | --per-day-half

writes OUT_PREFIX_train/ (dataset dir), OUT_PREFIX_train.sqlite (+ .work) and OUT_PREFIX_test_ids.txt
"""
import collections
import json
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swarmgraph.build import build  # noqa: E402
from swarmgraph.store import open_work  # noqa: E402


def split(dataset_dir, full_index, prefix, cut=None, per_day_half=False):
    ev = [json.loads(l) for l in open(os.path.join(dataset_dir, "events.jsonl"))]
    ev.sort(key=lambda e: e["ts"])
    if per_day_half:
        by_day = collections.defaultdict(list)
        for e in ev:
            by_day[e["ts"][:10]].append(e)
        train = []
        for day, es in by_day.items():
            train += es[:len(es) // 2]
    else:
        train = [e for e in ev if e["ts"] < cut]
    train_ids = {e["id"] for e in train}
    test_ids = [e["id"] for e in ev if e["id"] not in train_ids]
    tdir = prefix + "_train"
    shutil.rmtree(tdir, ignore_errors=True)
    os.makedirs(tdir)
    with open(os.path.join(tdir, "events.jsonl"), "w") as f:
        f.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in train)
    shutil.copy(os.path.join(dataset_dir, "phases.jsonl"), tdir) if os.path.exists(os.path.join(dataset_dir, "phases.jsonl")) else None
    # metadata scoped to what the analyst may see: the window's period, only the actors active in it, and no role/goal
    # fields (they can describe later periods)
    first_ts, last_ts = train[0]["ts"][:10], train[-1]["ts"][:10]
    active = {e["actor"] for e in train}
    with open(os.path.join(tdir, "actors.jsonl"), "w") as o:
        for line in open(os.path.join(dataset_dir, "actors.jsonl")):
            a = json.loads(line)
            if a["id"] in active:
                a.pop("role", None)
                o.write(json.dumps(a, ensure_ascii=False) + "\n")
    meta = json.load(open(os.path.join(dataset_dir, "dataset.json")))
    meta["description"] = (f"[This index holds only events from {first_ts} to {last_ts} and the {len(active)} actors active "
                           f"in them; statements below about other periods do not apply to it.] " + meta.get("description", ""))
    meta["notes"] = (meta.get("notes") or "").replace("role = latest individual goal.", "roles are not given in this window.")
    json.dump(meta, open(os.path.join(tdir, "dataset.json"), "w"), ensure_ascii=False)
    db = prefix + "_train.sqlite"
    for suffix in ("", ".work", ".work-wal", ".work-shm"):
        if os.path.exists(db + suffix):
            os.remove(db + suffix)
    build(tdir, db)
    # work store: copy rows for train events only
    work = open_work(db)
    work.execute("ATTACH DATABASE ? AS old", (f"file:{full_index}.work?mode=ro",))
    work.execute("CREATE TABLE IF NOT EXISTS event_themes(event_id TEXT, theme TEXT, PRIMARY KEY(event_id, theme))")
    work.execute("CREATE TABLE IF NOT EXISTS theme_done(event_id TEXT PRIMARY KEY)")
    work.execute("CREATE TEMP TABLE keep(id TEXT PRIMARY KEY)")
    work.executemany("INSERT INTO keep VALUES (?)", [(i,) for i in train_ids])
    for table in ("tags", "event_tags", "concerns", "concern_done", "event_themes", "theme_done"):
        try:
            cols = [r[1] for r in work.execute(f"PRAGMA main.table_info({table})")]
            ocols = {r[1] for r in work.execute(f"PRAGMA old.table_info({table})")}
            use = ", ".join(c for c in cols if c in ocols)
            work.execute(f"INSERT OR REPLACE INTO main.{table}({use}) SELECT {use} FROM old.{table} "
                         f"WHERE event_id IN (SELECT id FROM keep)")
        except sqlite3.OperationalError as ex:
            print(f"  skipped {table}: {ex}")
    work.execute("INSERT OR REPLACE INTO main.cards SELECT * FROM old.cards WHERE kind='themes'")
    work.commit()
    work.execute("DETACH DATABASE old")
    work.close()
    from swarmgraph import structure
    structure.run(db, log=lambda m: None)  # links and note search from the train notes only
    with open(prefix + "_test_ids.txt", "w") as f:
        f.write("\n".join(test_ids))
    w = sqlite3.connect(db + ".work")
    counts = {t: w.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("tags", "concerns", "event_themes")}
    print(f"train {len(train):,} events, test {len(test_ids):,}; train work rows {counts}")
    return db


if __name__ == "__main__":
    d, idx, prefix = sys.argv[1:4]
    if "--per-day-half" in sys.argv:
        split(d, idx, prefix, per_day_half=True)
    else:
        split(d, idx, prefix, cut=sys.argv[sys.argv.index("--cut") + 1])

