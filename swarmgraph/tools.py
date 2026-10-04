"""The tool surface, shared by the MCP server and the CLI. One definition per tool: description, JSON schema, handler."""
import os

from . import hypotheses as H
from . import metrics as M
from . import query as Q
from . import signals as S
from . import cards as K
from . import tags as T
from .store import open_work

S_ = {"type": "string"}
I_ = {"type": "integer"}
WINDOW = {"since": {**S_, "description": "ISO time, inclusive"}, "until": {**S_, "description": "ISO time, exclusive"},
          "segment": {**I_, "description": "segment id (see segments)"}}


def schema(props=None, required=()):
    return {"type": "object", "properties": props or {}, "required": list(required), "additionalProperties": False}


def default_db():
    return os.path.abspath(os.environ.get("SWARMGRAPH_DB", "swarmgraph.sqlite"))


class Session:
    """Holds the index path; opens read-only connections per call."""

    def __init__(self, db_path=None):
        self.db = db_path or default_db()

    def con(self):
        if not os.path.exists(self.db):
            raise Q.QueryError(f"no index at {self.db}; convert your data (see SKILL.md) and call build first")
        return Q.connect(self.db)

    def work(self):
        return open_work(self.db)


def _build(s, a):
    from .build import build
    info = build(a["dataset_dir"], s.db, a.get("reply_window_s", 900))
    return {"index": s.db, **info}


def _summarize(s, a):
    from .summarize import summarize_node
    return summarize_node(s.con(), s.work(), Q.resolve(s.con(), a["actor"]), int(a["segment"]),
                          effort=a.get("effort", "low"))


def _delegate(s, a):
    from . import sweep as W
    if a.get("job"):
        return W.collect(s.con(), a["job"], a.get("wait", 0), db=s.db)
    q = (a.get("question") or "").strip()
    if not q:
        return {"error": "give a question and where to ask it (chunks, day or period), or a job to collect"}
    con = s.con()
    if a.get("chunks"):
        ids = [int(x) for x in a["chunks"]]
    elif a.get("day"):
        ids = [r[0] for r in con.execute("SELECT id FROM w.chunks WHERE day=? ORDER BY id", (a["day"],))]
    elif a.get("period") is not None:
        ids = [r[0] for r in con.execute("SELECT id FROM w.chunks WHERE segment_id=? ORDER BY id", (int(a["period"]),))]
    else:
        return {"error": "say where to ask: chunks (ids from periods), a day, or a period"}
    return W.submit(s.db, q, ids, a.get("effort", "low"))


TOOLS = {
    "periods": ("START HERE on a large action-layer log. The map of the whole record by period, made by delegated "
                "reading: every chunk (one stretch of the agent's own working context) was read by a sub-agent with "
                "the same questions — what it did, whether what it said is borne out by the record, instructions and "
                "what it did with them, concerns — so every period has been read, and `read_by_sweep` shows where not. "
                "Zoom with period (its days, their counts and top concerns, chunk ids) or day (one row per chunk).",
                schema({"period": I_, "day": S_, "limit": I_}),
                lambda s, a: __import__("swarmgraph.sweep", fromlist=["x"]).periods(s.con(), a.get("period"),
                                                                                    a.get("day"), a.get("limit", 40))),
    "chunk": ("One chunk in full: the sweep sub-agent's reading (did, said with how the record bears on it, "
              "instructions, commitments, concerns, messages that changed what it did) with the text of the cited "
              "events, the earlier rules checked against it, and answers to your own questions asked of it.",
              schema({"id": I_, "evidence": I_}, ["id"]),
              lambda s, a: __import__("swarmgraph.sweep", fromlist=["x"]).chunk_view(s.con(), int(a["id"]),
                                                                                     a.get("evidence", 2))),
    "storyline": ("What happened, as storylines: an overview in order, threads that run through the record, notable "
                  "events outside them, what is left open, and per period a summary with turning points. Written by "
                  "an LLM from the structure's findings (not the raw log); every sentence carries the ids of events "
                  "that show it. A summary: open the events before you cite them.",
                  schema(), lambda s, a: __import__("swarmgraph.storyline", fromlist=["x"]).get(s.con()) or
                  {"error": "no storyline yet: run `storyline` (CLI) or `prepare`"}),
    "behaviour_features": ("Behaviours across all actors as an SAE-style dictionary (after the feature atlas is built): "
                           "each a description in general words that AI judges marked event by event, with how often "
                           "two judges agreed (kappa), a blind detection test, density and stretches per day. A "
                           "stretch can show several; rare ones stay. With id: that behaviour's clearest stretches "
                           "with event ids. The judges' marks are leads: open the events.",
                           schema({"id": S_, "limit": I_}),
                           lambda s, a: __import__("swarmgraph.features", fromlist=["x"]).view(s.con(), a.get("id"),
                                                                                              a.get("limit", 30))),
    "behaviour_map": ("Kinds of behaviour across all actors (after `atlas`): every stretch of work (an actor's events "
                      "in a row) described by code — what it did, not what it was about — and grouped into kinds at a "
                      "level set by measurement (held-out prediction of the next stretch, agreement on resampling). "
                      "Each kind: an LLM name checked blind (precision against near stretches of other kinds, recall "
                      "on its own), whether it is stable on resampling, what sets it apart in the counts, stretches "
                      "per day. With type: its stretches (actor, times, event ids). Also lists unusual stretches "
                      "(unlike their neighbours).",
                      schema({"type": I_, "limit": I_}),
                      lambda s, a: __import__("swarmgraph.atlas", fromlist=["x"]).view(s.con(), a.get("type"),
                                                                                       a.get("limit", 40))),
    "behaviour_changes": ("How behaviour changed over time, counted by code from the record (no LLM): per day, bursts "
                          "(a behaviour far above the actor's other active days; 'alone' = its share rose too, 'with "
                          "volume' = it did more of everything), behaviours that start or stop, what set each period "
                          "apart, and a watch list (personal e-mail addresses or phone numbers posted, failed posts, "
                          "concealment words in its reasoning; the sweep's judged rule breaks and concerns per day). "
                          "Behaviours: tools and shell programs used, failures, claims, corrections, reads by author.",
                          schema({"actor": S_, "limit": I_}),
                          lambda s, a: __import__("swarmgraph.behaviour", fromlist=["x"]).changes(
                              s.con(), a.get("actor"), a.get("limit", 15))),
    "rules": ("Threads across chunks. Instructions the agent received and commitments it made that bind it LATER, "
              "merged from the sweep, each checked against every later chunk whose own actions or words mention it "
              "(kept / broke / unclear, with evidence): 'acknowledged, then broke it'. And statements traced back: "
              "what it later said about things it had made, read against the chunk where it last made or changed "
              "them ('announced X, but what it made was Y'). verdict=broke lists only rules broken.",
              schema({"verdict": {"enum": ["kept", "broke", "unclear"]}, "limit": I_}),
              lambda s, a: __import__("swarmgraph.sweep", fromlist=["x"]).rules_view(s.con(), a.get("verdict"),
                                                                                      a.get("limit", 30))),
    "delegate": ("Ask your own question of chunks you choose: a sub-agent reads each chunk whole (not a sample) and "
                 "answers for it, citing events (verified to be in that chunk). Give question + chunks (ids from "
                 "periods) or day or period (at most 60 chunks per job); it returns a job at once and reads in the "
                 "background (about 1-2 minutes per chunk, 8 at a time). Collect with {\"job\": id, \"wait\": "
                 "seconds}. Answers are stored: asking the same question of the same chunk again costs nothing.",
                 schema({"question": S_, "chunks": {"type": "array", "items": I_}, "day": S_, "period": I_,
                         "job": S_, "wait": I_, "effort": {"enum": ["low", "medium", "high"]}}), _delegate),
    "build": ("Build (or rebuild) the index from a dataset directory in the swarmgraph format (events.jsonl, "
              "optional actors.jsonl / phases.jsonl / dataset.json). Investigation work is kept across rebuilds.",
              schema({"dataset_dir": S_, "reply_window_s": I_}, ["dataset_dir"]), _build),
    "grep": ("Regex search over events (searched in 'note summary || raw text', or only raw / only summary) with the exact "
             "count, how widely it recurs (actors, days, by-actor and by-day counts) and examples spread evenly over time "
             "with distinct actors — not the first ones. Use it to see whether something recurs before building on it.",
             schema({"patterns": {"type": "array", "items": S_}, "text": {"enum": ["text", "raw", "summary"]},
                     "examples": I_}, ["patterns"]),
             lambda s, a: __import__("swarmgraph.claims", fromlist=["x"]).grep(
                 __import__("swarmgraph.claims", fromlist=["x"]).Log(s.db), a["patterns"], a.get("text", "text"),
                 a.get("examples", 8))),
    "context": ("The events around one event: the same actor's neighbours (scope 'actor') or the same place's (scope "
                "'place'), with summaries and text. Use it to read a sequence, not a single event.",
                schema({"event_id": S_, "before": I_, "after": I_, "scope": {"enum": ["actor", "place"]}}, ["event_id"]),
                lambda s, a: __import__("swarmgraph.claims", fromlist=["x"]).context(
                    __import__("swarmgraph.claims", fromlist=["x"]).Log(s.db), a["event_id"], a.get("before", 4),
                    a.get("after", 4), a.get("scope", "actor"))),
    "test_claim": ("Measure a behaviour claim on this log before you report it. after_then: after an event matching "
                   "a_patterns, does an event matching b_patterns follow within window_minutes more often than in equal "
                   "windows elsewhere (scope same_actor | target | others_same_place)? spreads: does behaviour A move "
                   "between actors along contact? co_occurs: do actors who do A also do B? The baseline is fair "
                   "(only windows where the actor is active), the answer is a plain reading with the number of cases, "
                   "halves, a confidence bound, and pairs of events to read. verdict holds / not_holds / insufficient.",
                   schema({"shape": {"enum": ["after_then", "spreads", "co_occurs"]},
                           "a_patterns": {"type": "array", "items": S_}, "b_patterns": {"type": "array", "items": S_},
                           "scope": {"enum": ["same_actor", "target", "others_same_place"]}, "window_minutes": I_},
                          ["shape", "a_patterns"]),
                   lambda s, a: __import__("swarmgraph.claims", fromlist=["x"]).test_claim(
                       __import__("swarmgraph.claims", fromlist=["x"]).Log(s.db), a["shape"], a["a_patterns"],
                       a.get("b_patterns"), a.get("scope", "same_actor"), a.get("window_minutes", 90))),
    "map": ("Start here (after prepare/align): the whole swarm on one screen, code-computed. Who holds authority and "
            "what followed; the dominant behaviours and concern kinds (a big share of all events) vs the rarer ones; "
            "behaviour regularities (after X, targets do Y more than usual, with lift); time segments that shift. "
            "Every clue has numbers, a baseline, a few event ids and an `open` node. Clues, not verdicts.",
            schema(), lambda s, a: __import__("swarmgraph.brief", fromlist=["x"]).overview_map(s.con())),
    "node": ("Clues for one node: an actor name/id, a channel, `period:<id|label>`, `behaviour:<tag>` or "
             "`concern:<kind>`. Code-computed facts with baselines (what it does vs everyone else, concern flags by "
             "how far the evidence goes, who acted on it, first/strongest/last event) — then open the event ids.",
             schema({"entity": S_}, ["entity"]),
             lambda s, a: __import__("swarmgraph.brief", fromlist=["x"]).node(s.con(), a["entity"])),
    "claims_vs_record": ("For logs with an action layer (kinds action / result / reasoning): statements set against the "
                         "record of what was done. own = its claims of an outcome vs its own actions, their results "
                         "and its private reasoning; heard = other agents' claims it read that its own checks did not "
                         "confirm (how it handled them is its behaviour; who made them, a finding about them); intent = "
                         "private reasoning that plans to conceal, mislead or get past a check vs what it then said and "
                         "did. Verdicts (matches / overstates / contradicted / deceives / evades / neither / "
                         "cannot_tell), materiality, why, evidence with its text; `coverage` says per period how much "
                         "the ledger could check (silent = unchecked, not clean); the unfiltered list takes every "
                         "period in turn. "
                         "Code flags every case; an LLM judged each from its window only (`ledger --check`). Filter "
                         "with `type`, `verdict`, `material`, `actor`; open the events before you report one.",
                         schema({"verdict": S_, "type": S_, "actor": S_, "material": {"type": "boolean"}, "limit": I_,
                                 "period": I_}),
                         lambda s, a: __import__("swarmgraph.ledger", fromlist=["x"]).board(
                             s.con(), a.get("verdict"), a.get("actor"), int(a.get("limit", 30)), a.get("type"),
                             a.get("material"), a.get("period"))),
    "theme_map": ("After `themes`: recurring themes in THIS log (situations/moves, not the 17 generic tags) and the "
                  "strongest regularities over them — after theme A the same actor does theme B more than its base "
                  "rate (lift); A and B co-occur in an actor; a theme spreads along contact — each a lead for a "
                  "behaviour hypothesis, with event ids. Code-computed from LLM-assigned notes.",
                  schema(), lambda s, a: __import__("swarmgraph.themes", fromlist=["x"]).theme_map(s.con())),
    "theme": ("One theme: its definition, investigative interest, how many events/actors, top actors, and events with "
              "their note summary. Without `offset`: a sample spread over time. With `offset`: the whole class page by "
              "page in time order (what matters inside a class is rare; a sample finds it only at the chance rate). "
              "`{\"theme\": \"<name from theme_map>\", \"offset\": 0, \"examples\": 25}`.",
              schema({"theme": S_, "offset": I_, "examples": I_}, ["theme"]),
              lambda s, a: __import__("swarmgraph.themes", fromlist=["x"]).theme_node(
                  s.con(), a["theme"], examples=min(int(a.get("examples", 10)), 100), offset=a.get("offset"))),
        "overview": ("Dataset description, time range, actor and event kinds, relation counts by type and by "
                 "extraction method, most active actors, lineages, recorded event fields, and data-quality notes.",
                 schema(),
                 lambda s, a: Q.overview(s.con())),
    "segments": ("Time segments (phases from the data, or automatic day/week slices) with activity counts.", schema(),
                 lambda s, a: Q.segments(s.con())),
    "actors": ("Actors ranked by activity, with role, lineage, parent, and how often their addresses get replies.",
               schema({**WINDOW, "kind": {"enum": ["agent", "human", "system"]}, "limit": I_}),
               lambda s, a: Q.actors(s.con(), **a)),
    "actor_card": ("One actor: who invoked it and whom it invoked (subagents/successors), same-lineage actors, main "
                   "peers, per-segment nodes, latest self-report (a claim, not evidence), and the LLM node summary if "
                   "a segment is given and one exists.",
                   schema({"name": S_, **WINDOW}, ["name"]),
                   lambda s, a: Q.actor_card(s.con(), a.pop("name"), work=s.work(), **a)),
    "interactions": ("Relations of one actor, or between two actors, each with the event that shows it (text, time, "
                     "method, confidence, reply). Types: addressed, replied, invoked, returned, read.",
                     schema({"a": S_, "b": S_, "type": {"enum": ["addressed", "replied", "invoked", "returned", "read"]},
                             **WINDOW, "limit": I_, "newest": {"type": "boolean"}}, ["a"]),
                     lambda s, a: Q.interactions(s.con(), a.pop("a"), a.pop("b", None), a.pop("type", None), **a)),
    "timeline": ("Chronological relations in a (small) time window, optionally for one actor.",
                 schema({"since": S_, "until": S_, "actor": S_, "type": S_, "limit": I_}, ["since", "until"]),
                 lambda s, a: Q.timeline(s.con(), a.pop("since"), a.pop("until"), a.pop("actor", None),
                                         a.pop("type", "addressed"), a.pop("limit", 200))),
    "search": ("Full-text search (SQLite FTS5 syntax; quote phrases) over event text, oldest first.",
               schema({"query": S_, "actor": S_, "kind": S_, **WINDOW, "limit": I_}, ["query"]),
               lambda s, a: Q.search(s.con(), a.pop("query"), **a)),
    "select_events": ("Select events by what the data records — kind, actor, channel, metadata values "
                      "(meta: {field: value}; see event_fields), plain substring — with the exact total, or count them "
                      "grouped by kind | actor | channel | day | meta.<field>. Use this, not text search, for counts.",
                      schema({"kind": S_, "actor": S_, "channel": S_, "meta": {"type": "object"}, "text": S_, **WINDOW,
                              "group_by": S_, "limit": I_}),
                      lambda s, a: Q.select_events(s.con(), **a)),
    "event_fields": ("Metadata fields recorded on events, how often, and their common values.", schema(),
                     lambda s, a: Q.event_fields(s.con())),
    "get_event": ("One event in full, with the relations it evidences and its replies. Use to check any claim.",
                  schema({"event_id": S_}, ["event_id"]), lambda s, a: Q.get_event(s.con(), a["event_id"])),
    "tag_profile": ("Behaviour profile from LLM tags (run `tag` first): how often each behaviour occurs overall or for "
                    "one actor/window, the most common free labels, and the tag vocabulary. Tags are interpretations "
                    "of event text; check the events themselves before relying on them.",
                    schema({"actor": S_, **WINDOW}), lambda s, a: T.profile(s.con(), **a)),
    "behaviors": ("Tagged events with the LLM's one-line summary, filtered by actor, behaviour tag or free label.",
                  schema({"actor": S_, "tag": {"enum": list(T.VOCAB)}, "other": S_, **WINDOW, "limit": I_,
                          "newest": {"type": "boolean"}}),
                  lambda s, a: T.behaviors(s.con(), **a)),
    "trajectory": ("Start here for any actor, channel (page/room/thread) or 'all' (the swarm): the trajectory card — "
                   "role, phases, change points with triggers, claims, comparison with others, unknowns. Every sentence "
                   "is marked observed / claim / inferred and cites event ids; `issues` lists what code verification "
                   "could not confirm. Small entities get a code-only card.",
                   schema({"entity": S_}, ["entity"]), lambda s, a: K.trajectory(s.con(), a["entity"])),
    "link": ("How two actors are connected: relationship, key exchanges, effect on each other's behaviour (card), or "
             "the linked events if no card exists.", schema({"a": S_, "b": S_}, ["a", "b"]),
             lambda s, a: K.link(s.con(), a["a"], a["b"])),
    "changes": ("Behaviour change points from trajectory cards, filterable by entity, trigger (a useful-link type such "
                "as removes / instructs / communicates, or a behaviour tag) and time.",
                schema({"entity": S_, "trigger": S_, "since": S_, "until": S_, "limit": I_}),
                lambda s, a: K.changes(s.con(), **a)),
    "phase_events": ("The events (with notes) inside one phase of an entity's trajectory card.",
                     schema({"entity": S_, "phase": I_}, ["entity", "phase"]),
                     lambda s, a: K.phase_events(s.con(), a["entity"], a["phase"])),
    "find": ("Full-text search over event notes (summaries, claims) and card texts — normalised language, easier to "
             "search than raw event text. FTS5 syntax; quote phrases.", schema({"query": S_, "limit": I_}, ["query"]),
             lambda s, a: K.find(s.con(), a["query"], a.get("limit", 20))),
    "concerns": ("Possibly misaligned behaviour: the environment inferred from the data alone (whose system, what the "
                 "operators stop, what the agents try to do) and concern patterns (circumventing restrictions, "
                 "continuing after being stopped, misusing others' systems, sharing answers across instances, identity "
                 "games, security probing, deception, scope expansion, evading oversight) with severity and evidence.",
                 schema(), lambda s, a: __import__("swarmgraph.alignment", fromlist=["x"]).concerns_card(s.con())),
    "concern_actors": ("Actors ranked by possibly misaligned behaviour (severity, confidence, persistence after an "
                       "authority acted, rarity of the kind), with their concern kinds and example quotes.",
                       schema({"limit": I_}), lambda s, a: __import__("swarmgraph.alignment", fromlist=["x"]).concern_actors(s.con(), a.get("limit", 30))),
    "concern_events": ("Events flagged with a concern kind (quote, confidence), filterable by kind and actor.",
                       schema({"kind": S_, "actor": S_, "min_conf": {"enum": ["low", "medium", "high"]}, "limit": I_}),
                       lambda s, a: __import__("swarmgraph.alignment", fromlist=["x"]).concern_events(s.con(), **a)),
    "quality": ("Check the summary structure before generating hypotheses: note coverage, how many cards passed code "
                "verification, kinds of issues, and a random sample of cards to read against their events.",
                schema({"sample": I_}), lambda s, a: K.quality(s.con(), a.get("sample", 5))),
    "signals": ("Deterministic anomalies worth a hypothesis: bursts, one-sided relationships, reply-rate and volume "
                "outliers, invocation fan-out, lineage clusters, actors with self-reports.",
                schema({"since": S_, "until": S_, "limit": I_}), lambda s, a: S.detect(s.con(), **a)),
    "metric_catalog": ("Metrics available for hypothesis plans, with their parameters.", schema(),
                       lambda s, a: M.CATALOG),
    "run_metric": ("Compute a metric now (exploratory; not a registered test).",
                   schema({"metric": S_, "params": {"type": "object"}}, ["metric"]),
                   lambda s, a: M.run(s.con(), a["metric"], a.get("params") or {})),
    "propose_hypothesis": ("Register a testable hypothesis with its plan BEFORE looking at the result. plan = {metric, "
                           "params, expect:{field?, op, value}}; value may name a baseline in the metric details "
                           "(e.g. 'others_rate'). The plan cannot be changed later; refine by proposing a child.",
                           schema({"statement": S_, "plan": {"type": "object"}, "rationale": S_, "confirm_if": S_,
                                   "refute_if": S_, "alternatives": {"type": "array", "items": S_}, "parent": I_},
                                  ["statement", "plan"]),
                           lambda s, a: H.propose(s.con(), s.work(), a.pop("statement"), a.pop("plan"), source="host", **a)),
    "measure_hypothesis": ("Compute the registered metric and check it against the pre-registered expectation.",
                           schema({"id": I_}, ["id"]), lambda s, a: H.measure(s.con(), s.work(), a["id"])),
    "add_evidence": ("Attach events for or against a hypothesis; ids are verified against the index.",
                     schema({"id": I_, "items": {"type": "array", "items": {"type": "object"}}}, ["id", "items"]),
                     lambda s, a: H.add_evidence(s.con(), s.work(), a["id"], a["items"])),
    "record_verdict": ("Record supported | refuted | inconclusive. Gated: 'supported' needs the metric to pass and a "
                       "verified supporting event; 'refuted' needs the metric to fail or a verified contradicting event.",
                       schema({"id": I_, "verdict": {"enum": list(H.VERDICTS)}, "confidence": S_, "notes": S_},
                              ["id", "verdict"]),
                       lambda s, a: H.verdict(s.con(), s.work(), a["id"], a["verdict"], a.get("confidence", "medium"),
                                              a.get("notes", ""))),
    "hypotheses": ("The hypothesis ledger (or one hypothesis by id).",
                   schema({"id": I_, "status": S_, "verdict": S_}),
                   lambda s, a: H.get(s.work(), a["id"]) if a.get("id") else H.listing(s.work(), a.get("status"), a.get("verdict"))),
    "summarize_node": ("Optional LLM summary of one actor in one segment via the configured LLM CLI (default: Codex). "
                       "Every claim cites event ids, which are verified; observed vs self-reported claims are separated.",
                       schema({"actor": S_, "segment": I_, "effort": S_}, ["actor", "segment"]), _summarize),
}


def call(session, name, args):
    if name not in TOOLS:
        raise Q.QueryError(f"unknown tool {name}")
    return TOOLS[name][2](session, dict(args or {}))
