"""Prepare a large window of a dataset for the out-of-sample hypothesis experiment, unattended and resumable.

    python3 eval/run_big.py NAME SRC_DATASET_DIR SINCE UNTIL CUT [--workers 32]

  1 window   events with SINCE <= ts < UNTIL from SRC_DATASET_DIR  →  ../data/NAME/ and index ../data/NAME.sqlite
  2 notes    one LLM note per event (all events: the held-out part's summaries are needed to grade hypotheses)
  3 split    TRAIN = events before CUT → ../data/NAME_oos_train*  (work store copied for train events only)
  4 themes   the theme vocabulary is discovered from TRAIN summaries only, then assigned to every event
  5 split    again, so TRAIN carries its theme tags
  concern flags are skipped (the hypothesis task does not use them)

Every stage skips what is done; progress is written to ../data/NAME.big.log.
"""
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from swarmgraph import structure, tags, themes  # noqa: E402
from swarmgraph.build import build  # noqa: E402
from swarmgraph.llm import install_signal_handlers  # noqa: E402
from swarmgraph.store import open_work  # noqa: E402
import split_dataset  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(HERE)), "data")


def main(name, src, since, until, cut, workers):
    logf = open(os.path.join(DATA, f"{name}.big.log"), "a")

    def log(m):
        line = f"[{time.strftime('%H:%M:%S')}] {m}"
        print(line, file=logf, flush=True)
        print(line, flush=True)

    install_signal_handlers()
    d = os.path.join(DATA, name)
    db = os.path.join(DATA, f"{name}.sqlite")
    if not os.path.exists(os.path.join(d, "events.jsonl")):
        os.makedirs(d, exist_ok=True)
        n = 0
        with open(os.path.join(src, "events.jsonl")) as f, open(os.path.join(d, "events.jsonl"), "w") as o:
            for line in f:
                ts = line[line.index('"ts"') + 7:line.index('"ts"') + 17]  # "ts": "YYYY-MM-DD…
                if since <= ts < until:
                    o.write(line)
                    n += 1
        for x in ("actors.jsonl", "phases.jsonl", "dataset.json"):
            if os.path.exists(os.path.join(src, x)):
                os.system(f'cp "{os.path.join(src, x)}" "{d}"')
        log(f"window: {n:,} events {since}..{until}")
    if not os.path.exists(db):
        build(d, db)
    log("index built")
    # 2 notes (resumable); the second build turns addressees found in notes into relations, as `prepare` does
    work = open_work(db)
    done = work.execute("SELECT count(*) FROM tags").fetchone()[0]
    total = sqlite3.connect(db).execute("SELECT count(*) FROM events").fetchone()[0]
    if done < total:
        log(f"notes: {done:,}/{total:,} done, running with {workers} workers")
        tags.run(db, workers, "low", log=log)
        build(d, db)
    log("notes done")
    # 3 split (train without themes yet)
    tr = os.path.join(DATA, f"{name}_oos")
    if not os.path.exists(tr + "_train.sqlite"):
        split_dataset.split(d, db, tr, cut=cut)
    # 4 themes from TRAIN summaries only
    trdb = tr + "_train.sqlite"
    con = sqlite3.connect(db + ".work")
    if not con.execute("SELECT 1 FROM cards WHERE kind='themes'").fetchone():
        themes.discover(trdb, log=log)
        t = sqlite3.connect(trdb + ".work").execute("SELECT * FROM cards WHERE kind='themes'").fetchone()
        w = open_work(db)
        w.execute("INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,?,?)", t)
        w.commit()
    log("theme vocabulary ready (from train)")
    r = themes.assign(db, workers, log=log)
    log(f"themes assigned: {r}")
    # 5 split again so TRAIN carries its theme tags
    split_dataset.split(d, db, tr, cut=cut)
    log("READY")


if __name__ == "__main__":
    a = sys.argv[1:]
    workers = int(a[a.index("--workers") + 1]) if "--workers" in a else 24
    main(a[0], a[1], a[2], a[3], a[4], workers)
