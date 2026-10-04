"""swarmgraph command line. Every tool is also available as `call <tool> '<json>'` (JSON output), which is what
agents without MCP use."""
import argparse
import collections
import json
import os
from pathlib import Path
import sys

from .llm import default_workers, install_signal_handlers
from .tools import TOOLS, Session, call


def main(argv=None):
    ap = argparse.ArgumentParser(prog="swarmgraph", description="Index and investigate multi-agent datasets.")
    ap.add_argument("--db", help="index path (default: $SWARMGRAPH_DB or ./swarmgraph.sqlite)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the index from a dataset directory (swarmgraph format)")
    b.add_argument("dataset_dir")
    c = sub.add_parser("call", help="call a tool with JSON arguments, print JSON")
    c.add_argument("tool", choices=sorted(TOOLS))
    c.add_argument("args", nargs="?", default="{}")
    sub.add_parser("tools", help="list tools and their input schemas")
    sub.add_parser("mcp", help="run the MCP server on stdio")
    a = sub.add_parser("auto", help="LLM pipeline: generate hypotheses, measure, investigate, judge")
    a.add_argument("--n", type=int, default=5)
    a.add_argument("--effort", default="medium")
    a.add_argument("--workers", type=int, default=None, help="parallel LLM calls (default $SWARMGRAPH_WORKERS or 12)")
    x = sub.add_parser("explore", help="rounds of behaviour hypotheses, each verified by advocate + skeptic agents "
                                       "and an independent judge (parallel)")
    x.add_argument("--rounds", type=int, default=3)
    x.add_argument("--per-round", type=int, default=6)
    x.add_argument("--effort", default="medium")
    x.add_argument("--workers", type=int, default=None, help="parallel LLM calls (default $SWARMGRAPH_WORKERS or 12)")
    al = sub.add_parser("align", help="alignment lens: infer the environment blind, flag concerning behaviour per "
                                      "event (parallel), write concern cards")
    al.add_argument("--workers", type=int, default=None, help="parallel LLM calls (default $SWARMGRAPH_WORKERS or 12)")
    al.add_argument("--flag-effort", default="low")
    al.add_argument("--limit", type=int, help="only this many batches (trial)")
    al.add_argument("--env-only", action="store_true")
    sc = sub.add_parser("screen", help="train the small concern screener on the LLM's flags; held-out report, "
                                       "scores and disagreement queue")
    sc.add_argument("--kinds", nargs="*")
    th = sub.add_parser("themes", help="LLM discovers recurring themes from event notes and assigns them, then "
                                       "code computes regularities over them (resumable)")
    th.add_argument("--workers", type=int, default=None)
    th.add_argument("--discover-effort", default="medium")
    th.add_argument("--assign-effort", default="low")
    lg = sub.add_parser("ledger", help="claims set against the record (action layer): code flags the cases; with "
                                       "--check an LLM judges each from its window (resumable)")
    lg.add_argument("--check", action="store_true")
    lg.add_argument("--workers", type=int, default=None)
    lg.add_argument("--effort", default="medium")
    sw = sub.add_parser("sweep", help="delegated reading (action layer): cut the record into chunks one reader can "
                                       "read whole, have a sub-agent read every chunk with fixed questions, merge "
                                       "instructions and commitments into standing rules (--rules) and read later "
                                       "chunks against them (--threads); resumable")
    sw.add_argument("--workers", type=int, default=None)
    sw.add_argument("--effort", default="low")
    sw.add_argument("--rules", action="store_true")
    sw.add_argument("--threads", action="store_true")
    sw.add_argument("--chunks-only", action="store_true")
    ac = sub.add_parser("arc", help="the arc of the whole record: phases, turning points and hypotheses that explain "
                                    "the change, from a code skeleton and the storyline (after storyline); reviewed")
    ac.add_argument("--effort", default="high")
    sl = sub.add_parser("storyline", help="storylines for people written from the delegated reading (after sweep)")
    sl.add_argument("--workers", type=int, default=None)
    fs = sub.add_parser("feature-stories", help="agent-discovered, independently reviewed incident stories with atlas context")
    fs.add_argument("--effort", default="medium")
    fs.add_argument("--force", action="store_true", help="regenerate a stored tour")
    fs.add_argument("--overview", action="store_true", help="use the previous feature overview harness")
    fm = fs.add_mutually_exclusive_group()
    fm.add_argument("--export-input", metavar="FILE", help="export a frozen evidence snapshot without calling agents")
    fm.add_argument("--export-run", metavar="FILE", help="export the latest complete audit record without calling agents")
    fm.add_argument("--replay", metavar="FILE", help="validate and publish an exported run for the matching dataset; no agent calls")
    at = sub.add_parser("atlas", help="behaviour map (page /atlas): stretches of work grouped into kinds of behaviour "
                                      "at a level set by held-out prediction and resampling, named by an LLM and "
                                      "checked blind; needs numpy, scipy, scikit-learn and umap-learn")
    at.add_argument("--workers", type=int, default=None)
    at.add_argument("--effort", default="low")
    at.add_argument("--no-names", action="store_true", help="kinds without LLM names (no LLM calls)")
    fe = sub.add_parser("features", help="behaviour features (docs/BEHAVIOUR_FEATURES.md): 'all' runs the whole stage "
                                         "with isolated Codex readers (induce, merge, judge, detect, build; resumable); "
                                         "'build' the atlas from a folder; 'codex' reads a folder's batches")
    fe.add_argument("action", choices=["all", "build", "codex", "names"])
    fe.add_argument("folder", nargs="?", default="", help="the feature folder (not needed for names)")
    fe.add_argument("--record", default="", help="one line saying what the record is (for 'all')")
    fe.add_argument("--more", type=int, default=1600, help="uniform stretches judged beyond calibration (for 'all')")
    fe.add_argument("--workers", type=int, default=None)
    fe.add_argument("--judges", default="", help="judge folders for 'build', comma-separated; the first two are the "
                    "calibration pair (default judge1,judge2,judge_more, those present)")
    jb = sub.add_parser("jobs", help="run delegated questions: --run JOB (one job) or --watch (broker: run every "
                                     "queued job, for analysts that cannot reach the LLM themselves)")
    jb.add_argument("--run")
    jb.add_argument("--watch", action="store_true")
    t = sub.add_parser("tag", help="LLM event notes for every event (resumable; kept across rebuilds)")
    t.add_argument("--workers", type=int, default=None, help="parallel LLM calls (default $SWARMGRAPH_WORKERS or 12)")
    t.add_argument("--effort", default="low")
    t.add_argument("--limit", type=int, help="only this many batches (for a trial)")
    t.add_argument("--actor", nargs="*", help="only these actors' events")
    sub.add_parser("structure", help="useful links + note search (code only; run after tag, before cards)")
    k = sub.add_parser("cards", help="LLM trajectory/link/swarm cards from notes (resumable)")
    k.add_argument("--workers", type=int, default=None, help="parallel LLM calls (default $SWARMGRAPH_WORKERS or 12)")
    k.add_argument("--effort", default="medium")
    k.add_argument("--kinds", nargs="*", choices=["all", "actor", "channel", "link"])
    k.add_argument("--limit", type=int)
    k.add_argument("--force", action="store_true")
    pr = sub.add_parser("prepare", help="whole structure in one resumable run: build, then delegated reading (chunks, "
                                        "sweep, rules, threads, traces, ledger) or per-event notes (notes, cards, "
                                        "story, quality); LLM stages in parallel")
    pr.add_argument("--reading", choices=("sweep", "notes", "both"), default="sweep",
                    help="sweep: sub-agents read whole chunks (default); notes: one LLM note per event + cards")
    pr.add_argument("dataset_dir")
    pr.add_argument("--workers", type=int, default=None, help="parallel LLM calls (default $SWARMGRAPH_WORKERS or 12)")
    pr.add_argument("--no-llm", action="store_true", help="code-only stages (build, structure)")
    pr.add_argument("--tag-effort", default="low")
    pr.add_argument("--card-effort", default="medium")
    pr.add_argument("--story-effort", default="high")
    y = sub.add_parser("story", help="LLM story for people: overview, storylines, captions of key incidents (after cards)")
    y.add_argument("--effort", default="high")
    y.add_argument("--min-level", type=int, default=2)
    s = sub.add_parser("summarize", help="LLM node summaries for one segment (parallel)")
    s.add_argument("--segment", type=int, required=True)
    s.add_argument("--actor", nargs="*")
    s.add_argument("--min-events", type=int, default=20)
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--effort", default="low")
    v = sub.add_parser("serve", help="graph explorer in the browser")
    v.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8792")), help="default $PORT or 8792")
    v.add_argument("--host", default=os.environ.get("SWARMGRAPH_HOST", "127.0.0.1"),
                   help="address to listen on (0.0.0.0 to accept outside connections, e.g. in a container)")
    v.add_argument("--home", default="", help="the page '/' sends visitors to, e.g. /features (default: the explorer)")
    v.add_argument("--also", action="append", default=[], metavar="NAME=INDEX",
                   help="another dataset to offer on the same pages (repeatable)")
    v.add_argument("--share", action="store_true", help="withhold technical detail (payloads, crafted URLs, "
                   "markup, commands, addresses, secrets) from everything shown, for reports to others")
    args = ap.parse_args(argv)
    session = Session(args.db)
    if getattr(args, "workers", None) is None and hasattr(args, "workers"):
        args.workers = default_workers()
    if args.cmd in ("prepare", "tag", "cards", "story", "auto", "explore", "summarize", "align", "themes", "sweep",
                    "jobs", "ledger", "storyline", "feature-stories", "atlas", "features"):
        install_signal_handlers()  # a stopped run must not leave LLM calls running

    if args.cmd == "tools":
        return print(json.dumps({n: {"description": d, "input": sch} for n, (d, sch, _) in TOOLS.items()}, indent=1))
    if args.cmd == "mcp":
        from .mcp_server import serve
        return serve(session.db)
    if args.cmd == "serve":
        from .web import serve
        return serve(session.db, args.port, args.share, args.also, args.host, args.home)
    if args.cmd == "auto":
        from .auto import run
        hyps, rejected = run(session.db, args.n, args.effort, args.workers)
        return print(json.dumps({"hypotheses": [{k: h[k] for k in ("id", "statement", "metric_pass", "verdict",
                                                                    "confidence", "gate_notes")} for h in hyps],
                                 "rejected": rejected}, indent=1, ensure_ascii=False))
    if args.cmd == "align":
        from . import alignment as A
        if args.env_only:
            env = A.infer_environment(session.db, log=lambda m: print(m, file=sys.stderr, flush=True))
            return print(A.environment_text(env))
        if args.limit:
            return print(json.dumps(A.flag_concerns(session.db, args.workers, args.flag_effort, args.limit,
                                                    log=lambda m: print(m, file=sys.stderr, flush=True))))
        return print(json.dumps(A.run(session.db, args.workers, flag_effort=args.flag_effort), ensure_ascii=False))
    if args.cmd == "ledger":
        from . import ledger as LG
        if args.check:
            return print(json.dumps(LG.check(session.db, args.workers, effort=args.effort,
                                             log=lambda m: print(m, file=sys.stderr, flush=True))))
        cs = LG.cases(session.con())
        return print(json.dumps({"cases": len(cs), "by_type": dict(collections.Counter(c["type"] for c in cs)),
                                 "flags": dict(collections.Counter(f for c in cs for f in c["flags"]))}))
    if args.cmd == "sweep":
        from . import chunks as CH, sweep as SW
        from .store import open_work
        log = lambda m: print(m, file=sys.stderr, flush=True)
        work = open_work(session.db)
        if not CH.load(work):
            log(json.dumps(CH.save(session.con(), work, CH.make(session.con()))))
        if args.chunks_only:
            return print(json.dumps({"chunks": len(CH.load(work))}))
        out = {"sweep": SW.run(session.db, effort=args.effort, workers=args.workers, log=log)}
        if args.rules:
            out["rules"] = SW.rules(session.db, workers=args.workers, log=log)
        if args.threads:
            out["threads"] = SW.threads(session.db, effort=args.effort, workers=args.workers, log=log)
            out["trace"] = SW.trace(session.db, effort=args.effort, workers=args.workers, log=log)
        return print(json.dumps(out))
    if args.cmd == "arc":
        from . import arc
        return print(json.dumps(arc.run(session.db, args.effort, log=lambda m: print(m, file=sys.stderr, flush=True))))
    if args.cmd == "storyline":
        from . import storyline as SL
        return print(json.dumps(SL.run(session.db, args.workers, log=lambda m: print(m, file=sys.stderr, flush=True))))
    if args.cmd == "feature-stories":
        if args.overview:
            from . import feature_storylines as FS
        else:
            from . import incident_storylines as FS
        if args.export_input or args.export_run:
            con = session.con()
            try:
                artifact = FS.build_snapshot(con) if args.export_input else FS.latest_run(con)
            finally:
                con.close()
            if not artifact:
                raise ValueError("no storyline run has been stored")
            target = Path(args.export_input or args.export_run)
            target.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return print(json.dumps({"exported": str(target), "fingerprint": artifact.get("fingerprint")}))
        if args.replay:
            artifact = json.loads(Path(args.replay).read_text(encoding="utf-8"))
            return print(json.dumps(FS.save_replay(session.db, artifact), ensure_ascii=False))
        return print(json.dumps(FS.run(session.db, effort=args.effort, force=args.force,
                                       log=lambda m: print(m, file=sys.stderr, flush=True)), ensure_ascii=False))
    if args.cmd == "atlas":
        from . import atlas
        r = atlas.run(session.db, args.workers, args.effort, not args.no_names,
                      log=lambda m: print(m, file=sys.stderr, flush=True))
        return print(json.dumps({k: v for k, v in r.items() if k != "levels"}))
    if args.cmd == "features":
        from . import features as FE
        log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
        if args.action == "codex":
            return print(json.dumps({"read": FE.run_codex(args.folder, args.workers, log=log)}))
        if args.action == "names":
            return print(json.dumps(FE.short_names(session.db, log=log), ensure_ascii=False))
        if args.action == "all":
            name = args.record or (session.con().execute("SELECT value FROM meta WHERE key='name'").fetchone() or ["a log"])[0]
            return print(json.dumps(FE.run_all(session.db, args.folder, name, args.workers, args.more, log=log)))
        judges = tuple(args.judges.split(",")) if args.judges else ("judge1", "judge2", "judge_more")
        return print(json.dumps(FE.build(session.db, args.folder, judges=judges, log=log)))
    if args.cmd == "jobs":
        from . import sweep as SW
        log = lambda m: print(m, file=sys.stderr, flush=True)
        if args.watch:
            return SW.broker(session.db, log=log)
        return SW.run_job(session.db, args.run, log=log)
    if args.cmd == "themes":
        from . import themes as TH
        return print(json.dumps(TH.run(session.db, args.workers, args.discover_effort, args.assign_effort),
                                ensure_ascii=False))
    if args.cmd == "screen":
        from . import screener
        r = screener.run(session.db, tuple(args.kinds) if args.kinds else screener.DEFAULT_KINDS)
        return print(json.dumps({k: {x: v[x] for x in ("auc", "precision_at_k", "precision", "recall", "positives_test",
                                                         "top_features")} for k, v in r.items()}, indent=1))
    if args.cmd == "explore":
        from .auto import explore
        hyps, rejected = explore(session.db, args.rounds, args.per_round, args.effort, args.workers)
        return print(json.dumps({"hypotheses": [{k: h.get(k) for k in ("id", "round", "plain", "verdict", "confidence",
                                                                        "plain_note")} for h in hyps],
                                 "rejected": rejected}, indent=1, ensure_ascii=False))
    if args.cmd == "tag":
        from .tags import run
        return print(json.dumps(run(session.db, args.workers, args.effort, args.limit, actors=args.actor)))
    if args.cmd == "structure":
        from .structure import run
        return print(json.dumps(run(session.db)))
    if args.cmd == "cards":
        from .cards import run
        return print(json.dumps(run(session.db, args.workers, args.effort, args.kinds, args.limit, args.force)))
    if args.cmd == "prepare":
        from .pipeline import prepare
        r = prepare(args.dataset_dir, session.db, args.workers, not args.no_llm, args.tag_effort, args.card_effort,
                    args.story_effort, reading=args.reading)
        return print(json.dumps([{k: s[k] for k in ("stage", "seconds")} for s in r["stages"]]))
    if args.cmd == "story":
        from .cards import write_story
        return print(json.dumps(write_story(session.db, args.effort, args.min_level), ensure_ascii=False))
    if args.cmd == "summarize":
        from .summarize_batch import run
        return run(session, args.segment, args.actor, args.min_events, args.workers, args.effort)
    try:
        out = call(session, "build", {"dataset_dir": args.dataset_dir}) if args.cmd == "build" else \
            call(session, args.tool, json.loads(args.args))
    except Exception as ex:
        print(json.dumps({"error": str(ex)}), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
