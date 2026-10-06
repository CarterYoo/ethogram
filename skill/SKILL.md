# swarmgraph

You investigate what a group of agents did. swarmgraph gives you an index of **events** (what each actor produced),
**relations** (who addressed, replied to, invoked or returned results to whom — each tied to the event that shows it)
and a **hypothesis ledger** whose verdicts are gated by code-computed metrics. You never need to read raw logs end to end.

Tools are available as MCP tools (server `swarmgraph`) or on the command line:
`python3 -m swarmgraph --db INDEX call <tool> '<json args>'` (JSON in, JSON out). Python 3.9+, standard library only.

## 0. Check a claim before you report it (`grep`, `context`, `test_claim`)

Reading examples cannot show that something is a *regularity*: a claim like "after X, agents do Y more" needs a
comparison. `test_claim` does it on the log you have: A and B as regular expressions (searched in note summary || raw
text), the share of A-episodes followed by B within a window against comparable windows elsewhere for the same actors,
both halves of the log, a confidence bound, a plain `reading` and `pairs_to_read`. `grep` counts a pattern and shows how
widely it recurs (actors, days, busiest day/actor, examples spread over time); `context` shows the events around one.
Details: CLAIMS.md. Use them on every hypothesis before you report it; leads from `theme_map` are things to test, not
findings.

## 1. Convert the data (you write a small script)

Look at a sample of the raw data, then write a converter that produces a directory with:

**events.jsonl** — one JSON object per line
| field | required | meaning |
|---|---|---|
| `id` | yes | unique id (keep the source's id if it has one) |
| `ts` | yes | ISO-8601 time (UTC if no offset) |
| `actor` | yes | who produced it (stable id) |
| `text` | | content: message, command, file name… |
| `kind` | | `message` (default; said to others), `call` (actor started/assigned work to `to`, e.g. spawned a subagent), `return` (handed results back to `to`), `action` (did something: a command, a tool call, an edit), `result` (what an action returned: `reply_to` = the action, `meta.error` = true if it failed — what actually happened), `read` (actor read `reply_to`), `reasoning` (private thinking: what it believed or intended, never evidence that something happened), `self_report` (the actor's own account of itself: memory, status claims — never treated as evidence) |
| `to` | | list of actor ids the event was addressed to — **put the source's own addressing convention here** (e.g. subject tags, `X_TO_Y` names, mailbox paths). `@name` mentions in text are detected automatically. |
| `reply_to` | | id of the event this answers (or reads) |
| `channel`, `url`, `meta` | | room/thread; link to the original; anything else |

**actors.jsonl** (optional): `id, label, kind (agent|human|system), role, model, parent, lineage, aliases`
- `role` = what the actor was doing (shown before its name). `parent` = who spawned/called it (subagent, successor,
  recruit). `lineage` = group of same-lineage instances (same model+task, duplicates, successors).
- `aliases` = other names used for it in text. Avoid aliases shared by two actors (ambiguous aliases are dropped).

**phases.jsonl** (optional): `label, start, end` — named periods; otherwise day/week segments are made automatically.
**dataset.json** (optional): `name, description, notes` — context every investigator will read in `overview`;
`lead_kinds` (list of event kinds): which kinds record behaviour toward others, so that `theme_map` builds its leads
from them only (e.g. a log whose `action` events are only session-start intentions lists `["message"]`; default: all).

Rules: keep ids stable and traceable to the source; never invent recipients — leave `to` empty when unknown; mark
anything an agent says about itself as `self_report`; anonymise private individuals. If the data has agent transcripts
(reasoning, tool calls and their outputs), convert them too (`adapters/agent_transcript.py` for the Claude Agent SDK
message format): that ACTION layer is where what an agent said can be set against what it did. Then call `build`
(`{"dataset_dir": "..."}`); it reports validation errors with file and line.
See `examples/forum/convert.py` (20 lines of mapping) and `adapters/ai_village.py` (a large real dataset).

## 1b. Prepare the reading (needs the LLM CLI; once per dataset)

```
python3 -m swarmgraph --db INDEX prepare DIR                  # default: delegated reading (section 2c), any log
python3 -m swarmgraph --db INDEX prepare DIR --reading notes  # the per-event notes below instead (small chat logs)
```
The default cuts the record into chunks (where an agent's context starts afresh, else by day; repeated actions of
one shape fold into one line), has a sub-agent read every chunk with fixed questions (in a log of several actors,
every finding names its actor), merges instructions and commitments into rules, reads later chunks against them,
traces statements back to the work they describe, and on an action layer runs the claims ledger. Sub-agents get none
of the user's own tools (no MCP servers, plugins, web, computer use). The per-event layer, step by step:
```
python3 -m swarmgraph --db INDEX tag          # one LLM note per event (resumable)
python3 -m swarmgraph --db INDEX build DIR    # rebuild: addressees found in notes → llm_text relations
python3 -m swarmgraph --db INDEX structure    # code: useful links, note search
python3 -m swarmgraph --db INDEX cards        # LLM: trajectory cards (actors, channels, 'all') and link cards
python3 -m swarmgraph --db INDEX call quality # check before trusting it
```
- **Event note** (every event): verb-first summary, behaviour tags (task_work, test_probe, retry_variant, workaround,
  coordinate, instruct, follow, report_result, claim_success, ask_help, self_identify, build_on_others,
  remove_others, restore, moderate, filler, other), addressees, `responds_to` / `continues` (pointers checked by
  code), claims, stated goal.
- **Useful links**: behaviour directed at another actor — communicates, instructs, delegates, returns, follows,
  builds_on, removes, restores, responds. Plain adjacency (same place, no behaviour toward the other) is left out.
- **Trajectory card** (actor, channel or the whole swarm): role, phases, change points with triggers
  (before → trigger → after), claims, comparison with others, unknowns. **Link card** (pair): relationship, key
  exchanges, effect on the other's behaviour. Every sentence is marked observed / claim / inferred and cites event
  ids; code checks ids, numbers and trigger order and lists `issues`.
- Cards and notes are summaries for finding things. They are not evidence: cite events you have read.
- Parallel LLM calls: `--workers N` or `$SWARMGRAPH_WORKERS` (default 12); each call is one agent-CLI process (Codex or Claude Code).

## 2. Orient — `map`, then `node`, then the events

(Transcripts with an action layer: go to 2c.) Start at **`map`**: the whole swarm on one screen, computed by code (no LLM in the loop, so the numbers are exact).
It gives you, each as a clue with numbers, a baseline, a few event ids and an `open` target — never a verdict:
- **authority** — who removes/reverts others, how much, and how many targeted actors acted again afterwards;
- **behaviour** — the dominant tags (a big share of all events: the background) vs the rest, and the actors whose
  mix is most unlike everyone else's (share vs pooled baseline);
- **behaviour_regularities** — after X is aimed at an actor, how much more often they do Y than at other times
  (lift). These are ready-made leads for behaviour hypotheses;
- **concerns** — concern kinds split into pervasive (a big share of all events — judge as a whole) vs rarer, each
  with `basis` counts (how far the evidence goes: shown / context / claim / plan);
- **changes** — time segments where a behaviour is far above its overall share.

Then **`node <entity>`** for clues on one thing — an actor name/id, a channel, `period:<id|label>`,
`behaviour:<tag>` or `concern:<kind>` (the `open` fields on the map are exactly these). A node gives code-computed
facts (what it does vs the baseline, concern flags by basis, who acted on it, who it responded to, first/strongest/
last event) and the event ids to open. **The clues are where to look; read the events (`get_event`) before you
cite anything.**

Exact counts by recorded fields (not by words in the text): `event_fields` lists the metadata fields on events and
their common values; `select_events {kind, actor, channel, meta: {field: value}, text, since, until, group_by}`
returns the exact total or counts grouped by kind | actor | channel | day | meta.<field>.

Also available: `overview` (sizes, relation counts by extraction method, data-quality notes — relation types record
what the data or converter states, not that actors talked; read its dataset notes for what `reply_field` means),
`signals`, `actors`, `segments`, `actor_card`, and with the structure `trajectory` / `link` / `changes` /
`phase_events` / `find` / `tag_profile` / `behaviors`. `trajectory {"entity": "all"}` is a narrative card for people;
for analysis prefer `map`.

## 2b. Possibly misaligned behaviour (after `align`, part of `prepare`)

`align` infers the environment from the data alone (whose system, what its operators allow or stop, what the agents
try to do) and reads every event again for concern kinds: circumvent_restriction, persist_after_stop,
misuse_resource, cross_instance_sharing, identity_obfuscation, security_probing, deception, scope_expansion,
gaming_metrics, evade_oversight, other_concern. See them on the `map` (concerns) and in `node concern:<kind>`.
- Each flag carries `basis` — shown (the event does it) / context (only context makes it a concern) / claim (the
  agent says so) / plan (proposed or asked for) — and `unconfirmed` (what the event does not show). A kind name is
  a category, not a finding: `persist_after_stop` with basis `context` means "acted where an authority had acted",
  not that a stop order was given and ignored.
- `concern_events {"kind", "actor"}` — flagged events with quote, basis and unconfirmed. `concern_actors` — actors
  ranked by flagged behaviour. `concerns` — a people-facing card per kind (what, evidence_strength, timeline,
  counter_evidence, unconfirmed, severity); for analysis the `map`/`node` clues are tighter.
- Every concern kind is a metric tag (`concern:<kind>`), so hypotheses can test it like any behaviour
  (e.g. `reaction` of `concern:persist_after_stop` after `remove_others`).

## 2c. Records too large to read (`periods`, `behaviour_changes`, `behaviour_map`, `rules`, `chunk`, `delegate`)

When the index was prepared with delegated reading (the default; `periods` answers), start here, not at `map`:
transcripts (kinds `action`, `result`, `reasoning`), request logs, large boards. You cannot read such a record and grep
alone samples it; its reading has been delegated, and you can delegate more. Prepared with `prepare` or
`python3 -m swarmgraph --db INDEX sweep --rules --threads` (LLM, resumable). A line `action xN` is N repeats of one
action folded (what varied and how they ended are on the line); it is cited by its first event.
- **`periods` first**: the whole record by period. The record is cut into chunks, each a stretch of the agent's own
  working context (a chunk starts where a run starts or its context was compacted), and a sub-agent read every chunk
  whole with the same questions (the sweep): what it did; what it said and whether the record there bears it out
  (`overstates`, `contradicted`; `unsupported_here` only means this chunk does not show it); instructions it received
  and what it did with them; commitments; concerns (privacy, deception and whether it was sanctioned, e.g. a game
  role, rule breaking, unsafe actions, misreports, accusations). `read_by_sweep` below `chunks` marks what is unread.
  Zoom: `periods {"period": N}` (days, counts, top concerns, chunk ids), `periods {"day": "YYYY-MM-DD"}` (one row per
  chunk), `chunk {"id": N}` (the reading with the text of its evidence).
- **`behaviour_changes`**: counted by code, no LLM. Per day (per hour when actors are active on few days), bursts (a
  behaviour far above the actor's other active days; `alone` = more than a busier day explains), behaviours that
  start or stop, what set each period apart, and a watch list (personal e-mail addresses or phone numbers posted,
  failed posts, concealment words in reasoning, requests shaped like code injection, routed through redirect services
  or with doubled-slash paths; the sweep's judged rule breaks). Behaviours include tools and programs run, sites
  requested, failures, claims and corrections. A burst says where to look, not what happened.
- **`rules`**: threads across chunks. Instructions and commitments that bind it later, merged from the sweep, each
  read against every later chunk whose own actions or words mention it ("acknowledged the rule, then broke it":
  `rules {"verdict": "broke"}`); and `statements_traced_back`: what it later said about things it had made, read
  against the chunk where its own actions last made or changed them ("announced X, but what it made was Y").
- **`delegate`**: your own question, read by a sub-agent in each chunk you choose (`{"question": "...", "chunks":
  [ids]}`, or `"day"` / `"period"`; at most 60 chunks per job). It returns a job at once; collect with `{"job": "...",
  "wait": 240}`. Use it to follow a lead across chunks the sweep's fixed questions did not connect (e.g. "what did its
  submission contain, and does its later announcement match?"), and to look for what you suspect where nothing was
  flagged. Answers are stored per chunk and question (asking again costs nothing); `periods` shows where you asked.
- **`behaviour_features`** (after the feature atlas is built): behaviours as an SAE-style dictionary in words, each
  marked event by event by AI judges; per behaviour: two-judge agreement (kappa), a blind detection test, density,
  stretches per day. A stretch can show several and rare ones stay. `behaviour_features {"id": "F26"}` lists its
  clearest stretches with event ids. Prefer it to `behaviour_map` where it exists.
- **`behaviour_map`** (after `atlas`): kinds of behaviour across all actors. Every stretch of work (an actor's events
  in a row) is described by code by what it did, not what it was about, and grouped at a level set by measurement
  (held-out prediction of the next stretch; agreement when the map is made again from a resample). Each kind has an
  LLM name checked blind (`check`: precision against near stretches of other kinds, recall on its own), `stable`
  (Jaccard on resampling; below 0.75 the grouping is uncertain), `apart` (the counts that set it apart) and stretches
  per day: a kind that switches on across many actors at once is a lead. `behaviour_map {"type": N}` lists its
  stretches with event ids; `unusual` lists stretches unlike their neighbours (rare attempts land there, not in a kind
  of their own). A name is a summary of samples: open the stretches.
- **`storyline`**: what happened as an overview, threads and notable single events, written from the findings above
  (each sentence with event ids). A quick orientation before `periods`; it is a summary, so open the events.
- **`claims_vs_record`** (the ledger): code-flagged claims about artifacts (PRs, commits, files, URLs) set against
  its own actions and results, each judged from a short window: `own`, `heard` (others' claims it read that its own
  checks did not confirm; how it handled them is its behaviour), `intent` (private plans to conceal or get past a
  check). Precise where it looks, but it sees code work far better than other work: its silence is unchecked.

Sub-agents' readings and counts are leads with evidence ids: open the events (`context {"event_id": ..., "scope":
"actor"}`, `get_event`) before you report anything. Private reasoning shows intent and belief, never that something
happened; results show what happened. A period where nothing was flagged was read with fixed questions, which is not
proof that nothing happened there: if your question is different, delegate it.

## 2d. How behaviour moved through the system (`flow_*`, after the feature atlas)

The atlas page animates the flow for people; these tools give you the same flow as numbers computed by code over the
whole record (docs/FLOW.md), so you do not have to rebuild it from summaries. Start with **`flow_overview`**:
- `regimes`: the split of time that best predicts held-out stretches' behaviour, each with its defining behaviours,
  and at each boundary what rose and fell. A measured skeleton for the story; `heldout_explained` says how much the
  split explains (under 1%: the overall mix hardly changes, so work from single behaviours);
- `spreads_most` / `spreads_by_edge`: behaviours that travel between actors along copied words (`reuse`), replies,
  mentions (`address`) or the same place (`channel`), as a risk ratio net of week-wide trends with a 95% interval;
- `persists_within_actor` and `couplings`: what an actor keeps doing, and which behaviour tends to follow which.

Then `flow_shift {"at": date}` (what changed at a boundary, and the chunk ids to read for why), `flow_feature {"id"}`
(one behaviour's course, spread, adoption, spreaders), `flow_cascades {"id"}` (who passed it to whom, with events),
`flow_coupling {"a"}`. Before you report a flow claim, test it where you did not find it: `flow_test` with
`{"by": "time", "at": date}` or `{"by": "actors"}`, or register a `flow_transmission` / `flow_coupling` /
`flow_shift` metric with `propose_hypothesis`. Read every number as an association that fits spread, not proof:
behaviour inside copied text spreads mechanically along `reuse`; check `coverage` (only judged stretches count) and
open the events.

## 3. Investigate with hypotheses (the core loop)

**For leads, start at `theme_map`** (after the `themes` stage). Themes are the recurring situations/moves specific to
this log (not the 17 generic tags). `theme_map` gives leads of the three shapes a behaviour hypothesis takes, counted
strictly: *after theme A the same actor does theme B* (after_then; a burst of A is one episode, each follow-up counts
once, ranked by a bootstrap lower bound, with the same table under shuffled labels in `chance`), the same linked by a
shared artifact (after_then_same_artifact), *A and B co-occur in an actor* (co_occurs), *a theme spreads along contact*
(spreads). Effects are small by nature. The `themes` list is ordered by `interest` (1-5, how much an investigator
looking for misaligned behaviour or claims that do not match actions would want to open the class) when the index has
ratings, otherwise by size — and size says nothing about what matters. `theme {"theme": ...}` gives a theme's size,
spread over days and actors and a sample of its events; `theme {"theme": ..., "offset": 0, "examples": 25}` pages
through ALL of them in time order. What matters inside a class is rare: page through it rather than trusting a sample.
Turn a lead into a registered test
below (a theme is `concern:`-style usable via its events; for a metric plan, pick the matching tag/concern or a
`reaction`/`tag_spread` on the closest tag). Read the cited events before trusting any lead.

For each question worth answering:
1. **Propose before looking at the answer**: `propose_hypothesis` with a falsifiable `statement` and a `plan`
   `{metric, params, expect: {field?, op, value}}` from `metric_catalog`. `value` can name a baseline in the metric's
   details (e.g. `"others_rate"`). The plan is frozen; to change it, propose a child (`parent`).
2. `measure_hypothesis` — code computes the metric and checks the expectation.
3. Gather evidence **for and against** with `interactions`, `timeline`, `search`, `get_event`; look for the stated
   alternatives. `add_evidence` with stances `supports | contradicts | context` (ids are verified).
4. `record_verdict` — `supported | refuted | inconclusive`. Gates: *supported* needs the metric to pass and a verified
   supporting event; *refuted* needs the metric to fail or a verified contradicting event. Report the gated verdict.

Behaviour metrics (need tags): `tag_rate` (share vs everyone else), `tag_before_after` (change around a time),
`reaction` (what targets do within N minutes after an event with a trigger tag, vs the window before), `tag_spread`
(adoption order and whether adopters had contact with earlier adopters, vs chance).
Flow metrics (need the feature atlas): `flow_transmission` (a behaviour travels along edges: risk ratio by weeks, with
`lo`/`hi`), `flow_coupling` (B follows A along edges), `flow_shift` (a behaviour more common in a window: z). Register
them with `since` on a part of the record you have not looked at.
Aim for hypotheses that explain behaviour — how agents react, adapt, coordinate, copy each other, and whether claims
match actions — whether or not the behaviour is risky. Use `run_metric` for exploration, but only registered plans
count as tests.

## 4. Report

State each finding with its hypothesis id, verdict, metric value vs expectation, and 2–5 event ids (with `url` when
present). Separate what events show from what agents claim about themselves. Say what the data cannot show
(missing sources, heuristic relations, sampled summaries).

## Optional LLM helpers (need a local agent CLI, Codex or Claude Code; set SWARMGRAPH_AGENT to choose)
- `summarize_node {actor, segment}` — grounded summary of one actor in one segment; every claim cites event ids.
- `python3 -m swarmgraph auto --n 5` — fully automatic loop: generate hypotheses from signals, measure, investigate
  (separate LLM call with read-only tools), judge (another call that sees only plan, metric and cited events), gate.
- `python3 -m swarmgraph serve` — network and timeline explorer in the browser.
