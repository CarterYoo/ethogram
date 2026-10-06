"""Summarise every busy node in a segment in parallel (LLM calls in threads, cache/writes on the main thread)."""
import concurrent.futures
import json
import sys
import time

from . import query as Q
from .llm import Agent
from .summarize import PROMPT, SCHEMA, evidence_pack, ground


def run(session, segment, actors=None, min_events=20, workers=4, effort="low"):
    con, work = session.con(), session.work()
    dataset = dict(con.execute("SELECT key, value FROM meta")).get("name", "dataset")
    ids = [Q.resolve(con, a) for a in actors] if actors else [r[0] for r in con.execute(
        "SELECT actor FROM nodes WHERE segment_id=? AND n_events>=? ORDER BY n_events DESC", (segment, min_events))]
    agent = Agent(effort=effort)
    jobs = []
    for aid in ids:
        pack, refs, name, seg = evidence_pack(con, aid, segment)
        prompt = PROMPT.format(dataset=dataset, actor=name, segment=seg, pack=pack)
        key = agent.key(prompt, SCHEMA)
        hit = work.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        jobs.append((aid, name, refs, prompt, key, json.loads(hit[0]) if hit else None))
    print(f"{len(jobs)} nodes in segment {segment}", file=sys.stderr, flush=True)

    def work_one(job):
        if job[5] is not None:
            return job, job[5], 0.0, None
        try:
            r, s = agent.run(job[3], SCHEMA)
            return job, r, s, None
        except Exception as ex:
            return job, None, 0.0, str(ex)

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for job, result, secs, err in pool.map(work_one, jobs):
            aid, name, refs, _p, key, _c = job
            if err:
                print(f"FAILED {name}: {err[:200]}", file=sys.stderr, flush=True)
                continue
            if secs:
                work.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?,?)",
                             (key, "default", effort, time.strftime("%Y-%m-%d %H:%M:%S"), secs, json.dumps(result)))
            g = ground(result, refs)
            work.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?,?,?,?,?)",
                         (aid, segment, time.strftime("%Y-%m-%d %H:%M:%S"), "default", json.dumps(refs), json.dumps(result), g))
            work.commit()
            done += 1
            print(f"[{done}/{len(jobs)}] {name}: {secs:.0f}s, grounded {g:.0%} — {result['role'][:70]}", file=sys.stderr, flush=True)
    print(json.dumps({"segment": segment, "summarized": done, "failed": len(jobs) - done}))
