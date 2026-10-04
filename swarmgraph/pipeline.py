"""One command from a converted dataset to the prepared structure, with the LLM stages run in parallel:

    python3 -m swarmgraph --db INDEX prepare DATASET_DIR [--workers 12] [--no-llm] [--reading sweep|notes|both]

Delegated reading (`--reading sweep`, the default):
  1 build      index (code)
  2 structure  useful links (code)
  3 chunks     the record cut where an actor's context starts afresh, else by day (code)
  4 sweep      a sub-agent reads every chunk whole with fixed questions          parallel, resumable
  5 rules      instructions and commitments merged into standing rules
  6 threads    later chunks read against the rules in force                      parallel, resumable
  7 trace      statements read against the earlier work they describe            parallel, resumable
  8 storyline  per period summary and turning points, then storylines for people (from the findings above)
  9 ledger     on an action layer: claims about artifacts set against the record  parallel, resumable
Per-event notes (`--reading notes`, the earlier layer, measured to add nothing on chat-scale logs; RESULTS 8-9):
  2 notes, 3 build again (addressees from notes), 4 structure, 5 cards, 5b align, 6 story, 7 quality

Every stage skips work that is already done, so an interrupted run continues where it stopped. The report is written
to <INDEX>.prepare.json. Parallelism: --workers, or $SWARMGRAPH_WORKERS (default 12).
"""
import json
import sys
import time

from .llm import default_workers


def prepare(dataset_dir, db, workers=None, llm=True, tag_effort="low", card_effort="medium", story_effort="high",
            log=lambda m: print(m, file=sys.stderr, flush=True), reading="sweep"):
    from . import cards, query, structure, tags
    from .build import build
    workers = workers or default_workers()
    report = {"dataset_dir": dataset_dir, "index": db, "workers": workers, "llm": llm, "stages": []}

    def stage(name, fn):
        log(f"== {name}")
        t = time.time()
        out = fn()
        report["stages"].append({"stage": name, "seconds": round(time.time() - t, 1), "result": out})
        with open(db + ".prepare.json", "w") as f:
            json.dump(report, f, indent=1, default=str)
        return out

    stage("build", lambda: {k: v for k, v in build(dataset_dir, db).items() if k in ("relations", "methods")})
    if llm and reading in ("sweep", "both"):
        from . import chunks as CH, ledger, sweep
        from .store import open_work
        con = query.connect(db)
        stage("structure", lambda: structure.run(db, log=log))
        stage("chunks", lambda: CH.save(con, open_work(db), CH.make(con)) if not CH.load(open_work(db)) else
              {"chunks": len(CH.load(open_work(db)))})
        stage("sweep", lambda: sweep.run(db, workers=workers, log=log))
        stage("rules", lambda: sweep.rules(db, workers=workers, log=log))
        stage("threads", lambda: sweep.threads(db, workers=workers, log=log))
        stage("trace", lambda: sweep.trace(db, workers=workers, log=log))
        from . import storyline
        stage("storyline", lambda: storyline.run(db, workers, log=log))
        if con.execute("SELECT 1 FROM events WHERE kind='result' LIMIT 1").fetchone():
            stage("ledger", lambda: ledger.check(db, workers, log=log))
        if reading == "sweep":
            log(f"prepared {db} in {sum(x['seconds'] for x in report['stages']) / 60:.1f} min; report {db}.prepare.json")
            return report
    if llm:
        stage("notes", lambda: tags.run(db, workers, tag_effort, log=log))
        stage("rebuild", lambda: {k: v for k, v in build(dataset_dir, db).items() if k in ("relations", "methods")})
    stage("structure", lambda: structure.run(db, log=log))
    if llm:
        stage("cards", lambda: cards.run(db, workers, card_effort, log=log))
        from . import alignment
        stage("align", lambda: alignment.run(db, workers, log=log))
        stage("story", lambda: cards.write_story(db, story_effort, log=log))
        stage("quality", lambda: cards.quality(query.connect(db)))
    log(f"prepared {db} in {sum(s['seconds'] for s in report['stages']) / 60:.1f} min; report {db}.prepare.json")
    return report
