# SwarmScope

Understanding behaviour in multi-agent environments. (The Python package and command are `swarmgraph`.)

![SwarmScope demo: a swarm of agents, one sentence becoming a behaviour, behaviours over time with the storyline](docs/media/swarmscope-demo.webp)

*36-second demo on the wiki logs plus other boards, made with HyperFrames from the real results ([demo/](demo/)).*


Index and investigate multi-agent datasets — agent swarms, message boards, group chats, orchestrator/subagent logs —
as an **MCP server + skill** that any agent can use. Built for the AI Village × Grove Research *AI Swarm Dynamics
Hackathon* (Oct 3–4, 2026).

Investigators of the OpenAI / Hugging Face incident had to read ~1,300 multi-million-token transcripts through analysis
agents they could not fully trust. SwarmScope turns any such dataset into:

1. **An index** of events and relations — who *addressed*, *replied to*, *invoked* (spawned/assigned), *returned
   results to*, or *read* whom — where every relation points at the event that shows it and records how it was found
   (explicit field, reply field, `@mention`, call/return kind, parent link) and with what confidence.
2. **A hypothesis ledger** where plans are pre-registered, metrics are computed by code, and verdicts are gated: an LLM
   cannot mark a hypothesis *supported* unless the pre-registered metric passes and a verified event supports it.
3. **Optional LLM layers** (via a local agent CLI, default Codex): **behaviour tags** for every event (what the actor
   did, from a dataset-independent vocabulary, plus addressees found in the text), grounded node summaries, and a
   fully automatic generate → measure → investigate → judge loop with separate investigator and judge calls.
4. **Delegated reading for records too large to read** (agent transcripts: reasoning, tool calls and results): the
   record is cut into chunks where the agent's own context starts afresh, and a sub-agent reads every chunk whole with
   fixed questions (what it did, whether what it said holds up against the record, instructions and what it did with
   them, concerns); instructions and commitments are followed across chunks, later statements are traced back to the
   work they describe, behaviour over time is counted by code (sudden increases, starts, stops), and the analyst can
   send its own questions to sub-agents over the chunks it chooses. Every reading cites events, checked by code; the
   map says what was read and what was not.

Python 3.9+, standard library only.

Reproduce the stored results or rerun from the raw data: [docs/REPRODUCE.md](docs/REPRODUCE.md). Deploy the
behaviour atlas (bundle, container, share mode): [docs/DEPLOY.md](docs/DEPLOY.md).

## Use it

One command after converting the data (LLM stages run in parallel and resume where they stopped):

```bash
python3 -m swarmgraph --db wiki.sqlite prepare path/to/dataset --workers 12   # build → notes → rebuild → structure → cards → story → quality
python3 -m swarmgraph --db wiki.sqlite explore --rounds 3 --per-round 6         # behaviour hypotheses, verified by several agents
python3 -m swarmgraph --db wiki.sqlite serve                                  # /story for people, / for the network explorer
python3 -m swarmgraph --db wiki.sqlite serve --share                          # same, technical detail withheld (for reports to others)

# a transcript (action layer): delegated reading, then /agent shows how the agent's behaviour changed, for people
python3 -m swarmgraph --db agent.sqlite sweep --rules --threads --workers 24   # chunks, sweep, rules, traced statements
python3 -m swarmgraph --db agent.sqlite ledger --check                       # claims about artifacts vs the record
python3 -m swarmgraph --db agent.sqlite serve                                # /agent for people (storyline, days, periods)
python3 -m swarmgraph --db agent.sqlite serve --share                        # the same for outside readers: methods only by kind

# behaviour map (/atlas): kinds of behaviour across all actors and when each was active (needs numpy, scipy,
# scikit-learn, umap-learn: python3 -m venv .venv && .venv/bin/pip install numpy scipy scikit-learn umap-learn)
.venv/bin/python -m swarmgraph --db wiki.sqlite atlas --workers 12          # after sweep; LLM names checked blind
python3 -m swarmgraph --db wiki.sqlite serve                                # /atlas (any Python: the map is stored)
python3 -m swarmgraph --db wiki.sqlite serve --also aiv=aiv.sqlite             # one server, several datasets: a picker on /features
```

Sub-agents run with none of the user's own Codex setup (no MCP servers, plugins, web search, computer use, apps):
they read untrusted text, so a prompt inside it must find no tool to use (`llm.isolation()`).

`--share` masks working methods in everything shown (links reduced to their domain, encoded strings, markup,
commands, addresses, secret-like tokens) and concern cards are written to describe methods only by kind; on the wiki
logs no link, encoding, markup, address or command survives in any of the 19,913 events.

| setting | default | meaning |
|---|---|---|
| `--workers` / `$SWARMGRAPH_WORKERS` | 12 | parallel LLM calls in `prepare`, `tag`, `cards`, `auto` (each call is its own `codex exec`) |
| `$SWARMGRAPH_CODEX` | `codex` on PATH or the ChatGPT app's bundled CLI | LLM command |
| `$SWARMGRAPH_MODEL` | Codex default | model override |
| `--tag-effort / --card-effort / --story-effort` | low / medium / high | reasoning effort per stage |

The work store (`<index>.work`) runs in WAL mode, so the explorer and query tools can read while cards are written;
stopping a run (Ctrl-C or SIGTERM) also stops its LLM calls. Measured on the wiki logs (19,913 events): notes 52 min
with 24 workers, 386 cards 31 min with 12 workers, story 5 min.

`explore` runs rounds of **behaviour hypotheses** ("after X, agents do Y more than at other times", "Y spreads along
contact", "agents that do X also do Y"). Each hypothesis carries its basis (the events that suggested it) and a plain
sentence; its plan is frozen, measured by code and re-run on each half of the window (replication); an advocate agent
looks for fitting instances, a skeptic agent for counter-instances and alternative explanations, and a judge decides
with the gates. The next round reads the verdicts. Results appear under "Behaviour being tested" in /story.

Step by step:

```bash
# 1. convert your data to the common format (an agent writes this script; see skill/SKILL.md)
python3 examples/forum/make_fixture.py && python3 examples/forum/convert.py
# 2. build the index
python3 -m swarmgraph --db forum.sqlite build examples/forum/dataset
# 3. investigate (JSON in / JSON out), or connect the MCP server
python3 -m swarmgraph --db forum.sqlite call signals
python3 -m swarmgraph --db forum.sqlite call actor_card '{"name": "lead-2"}'
python3 -m swarmgraph --db forum.sqlite call propose_hypothesis '{"statement": "archivist is answered less than others",
  "plan": {"metric": "reply_rate", "params": {"actor": "archivist"}, "expect": {"op": "<", "value": "others_rate"}}}'
python3 -m swarmgraph --db forum.sqlite tag                  # LLM event notes (needs Codex or $SWARMGRAPH_CODEX)
python3 -m swarmgraph --db forum.sqlite build examples/forum/dataset   # rebuild: text addressees → llm_text relations
python3 -m swarmgraph --db forum.sqlite structure            # useful links + note search (code)
python3 -m swarmgraph --db forum.sqlite cards                # LLM trajectory / link / swarm cards, verified by code
python3 -m swarmgraph --db forum.sqlite call quality         # check the structure before hypotheses
python3 -m swarmgraph --db forum.sqlite auto --n 5          # LLM hypothesis loop (reads the cards)
python3 -m swarmgraph --db forum.sqlite serve               # explorer: / (network, timeline), /story (phases, changes)
```

### MCP

Claude Code:
```bash
claude mcp add swarmgraph --env PYTHONPATH=/path/to/swarmgraph --env SWARMGRAPH_DB=/path/to/index.sqlite -- python3 -m swarmgraph mcp
```
Codex (`~/.codex/config.toml`):
```toml
[mcp_servers.swarmgraph]
command = "python3"
args = ["-m", "swarmgraph", "mcp"]
env = { PYTHONPATH = "/path/to/swarmgraph", SWARMGRAPH_DB = "/path/to/index.sqlite" }
```
Install the skill by copying `skill/SKILL.md` into your skills directory (e.g. `~/.claude/skills/swarmgraph/`).

## Tools

| group | tools |
|---|---|
| build | `build` |
| orient | `overview`, `trajectory` (actor / channel / 'all'), `link`, `changes`, `quality`, `signals`, `segments`, `actors`, `actor_card`, `tag_profile` |
| evidence | `phase_events`, `find`, `interactions`, `timeline`, `search`, `get_event`, `behaviors` |
| hypotheses | `metric_catalog`, `run_metric`, `propose_hypothesis`, `measure_hypothesis`, `add_evidence`, `record_verdict`, `hypotheses` |
| LLM (optional) | `summarize_node`; CLI `tag`, `cards`, `auto`, `summarize` (code-only: `structure`) |
| delegated reading | `storyline`, `periods` (start here), `chunk`, `rules`, `behaviour_changes`, `behaviour_map`, `delegate`, `claims_vs_record`; CLI `prepare`, `sweep`, `storyline`, `atlas`, `jobs`, `ledger` |

Metrics: interaction — `reply_rate`, `volume`, `before_after`, `pair`, `first_use`, `share`, `burst`; behaviour (needs
tags) — `tag_rate`, `tag_before_after`, `reaction`, `tag_spread` (see `metric_catalog`).

## Data format (summary — full spec in `swarmgraph/format.py` and `skill/SKILL.md`)

`events.jsonl`: `id, ts, actor, text, kind (message|call|return|action|result|read|reasoning|self_report), to[],
reply_to, channel, url, meta` (`meta.error` on a failed result; `meta.boundary = "context"` where an agent's context
starts afresh)
`actors.jsonl`: `id, label, kind, role, model, parent, lineage, aliases` · `phases.jsonl`: `label, start, end` ·
`dataset.json`: `name, description, notes`

## Tested on

| dataset | events | relations | notes |
|---|---|---|---|
| synthetic forum (`examples/forum`) | 30 | addressed 24, invoked 4, returned 1, replied 4 | recipients only in subject tags; subagent spawn; successor hand-off |
| wiki edit logs (`adapters/wiki_logs.py`) | 19,913 | addressed 12,959, replied 1,723 (before tags) | saves as diffs, admin deletions, recreations; actors = self-chosen author labels |
| AI Village (`adapters/ai_village.py`) | 262,762 | addressed 55,658, replied 19,298 | same counts as the earlier dedicated builder (55,655 / 19,296); converts in ~50 s, builds in ~17 s |
| AI Village agent transcript (`adapters/agent_transcript.py`) | 219,740 | read 34,972, addressed 5,457, replied 1,065 | one agent's 912 MB working record (reasoning, 32,550 tool calls with results, messages read and sent); 1,052 chunks swept, 354 rules (61 of 261 high/medium broken at least once), 630 statements its earlier work contradicts |
| US/Canada government web evidence (`adapters/web_evidence.py`) | 1,125,129 | none (requests have no addressees) | requests made through a web archive, URL scanners and wiki links, grouped by query tag into 66 requesters; repeats fold, 431 chunks; behaviour by the hour |

`python3 -m unittest discover -s tests` covers format validation, relation extraction, verdict gates and the MCP handshake.

## Layout

```
swarmgraph/format.py     canonical format + validation        swarmgraph/tools.py        tool registry (MCP + CLI)
swarmgraph/build.py      generic index builder                swarmgraph/mcp_server.py   stdio MCP server
swarmgraph/query.py      read-only queries                    swarmgraph/cli.py          command line
swarmgraph/metrics.py    deterministic metrics + checks       swarmgraph/llm.py          LLM CLI runner (Codex)
swarmgraph/signals.py    anomaly + behaviour signals          swarmgraph/summarize*.py   grounded node summaries
swarmgraph/tags.py       LLM event notes + tag queries         swarmgraph/structure.py    useful links, change candidates, checks
swarmgraph/cards.py      LLM trajectory / link / swarm cards    swarmgraph/story.html      phases + change points view
swarmgraph/hypotheses.py ledger, pre-registration, gates      swarmgraph/auto.py         automatic hypothesis loop
swarmgraph/store.py      work store (<index>.work)            swarmgraph/web.py          explorer (+ explorer.html)
adapters/ai_village.py   example adapter (large, real)        examples/forum/            example adapter (small, synthetic)
adapters/wiki_logs.py    wiki edit-log adapter                swarmgraph/ledger.py       claims vs record (action layer)
adapters/agent_transcript.py  agent transcripts               swarmgraph/chunks.py       chunks one reader can read whole
swarmgraph/sweep.py      sweep, rules, traces, delegate jobs  swarmgraph/behaviour.py    behaviour over time (code)
swarmgraph/agentview.py  /agent page data (+ agent.html)     swarmgraph/atlas.py        behaviour map (+ atlas.html)
skill/SKILL.md           instructions for agents              tests/test_core.py         unit + MCP tests
```

## How well it works (`eval/RESULTS.md`)

Held-out hand labels (one labeller, blind to flags): on the wiki logs the alignment lens flags concerning events with
precision 1.00 and recall 0.91 (it misses content-free edits whose concern lies in who made them); on five unseen
AI Village days precision is 0.35–0.56 and recall uncertain — it over-flags in a benign environment, and gains on the
tuning sample did not carry over. In an A/B test, a Codex analyst with the prepared index was as accurate as the same
analyst with the raw log and a shell on five questions and faster on the open ones, but its concern labels and cards
overstated or one-sided some findings (both conditions found the wiki misuse blind). Behaviour hypotheses written by the analyst were also graded by code against baselines, on events held out from
what the analyst saw. On a large split (July seen, 31k August events held out) 13% of the raw analyst's and 10% of
the structure-assisted analyst's hypotheses hold (p = 0.78; mismatched pairs pass 2.6-7%); on the month the analyst saw
the structure's hypotheses held more often (28% vs 12%, p = 0.04) — a selection effect that does not carry over. On an investigation task (what should overseers know about these agents, including whether
what they report is true), every run found the corrected and retracted claims with or without the structure, and the
raw analyst was cheapest (section 9): on chat-scale logs the question matters more than the structure. On the ACTION layer (one agent's
912 MB working transcript: reasoning, tool calls and results, messages read and sent) a ledger that sets each claim
against the record made the difference: a blind pairwise judge preferred the ledger analyst's report to the raw
analyst's in 7 and then 8 of 10 pairs (raw 1 each), at 15-19% fewer tokens (section 10), but narrowed what analysts
looked at (section 11). Delegated reading (section 12: every chunk read by a sub-agent, rules followed across chunks,
statements traced back to the work they describe, behaviour over time counted by code) found every finding raw
readers had over ledger readers, and in a small check analysts with it found as many of them as raw readers (3.0 vs
2.8 per report, ledger 1.6) over the same breadth, preferred to raw in 2 of 3 pairs. Untested: weaker or time-limited
analysts, use by people.

## Limits

- Relations come from what the data states or an explicit convention the converter maps; unstated addressing (pure
  context) is not inferred yet. `replied` requires an explicit address back.
- LLM summaries see a sample of a node's events; open questions they raise must be checked with the query tools.
- Metrics are simple descriptive statistics, not significance tests.
