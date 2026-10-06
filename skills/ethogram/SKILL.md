---
name: ethogram
description: Map how a swarm of AI agents behaves and how its behaviours spread, from any multi-agent log (agent transcripts, message boards, group chats, wiki edits, orchestrator and sub-agent logs). Use when the user asks to analyse what a group of agents did, find their recurring behaviours, tell the story of how their behaviour changed over time, measure which behaviours bring on which, or open the behaviour atlas.
---

# Ethogram

Ethogram turns a multi-agent log into a dictionary of behaviours in natural language, a map of how they rise and fall
over time, and a measure of how much seeing one behaviour changes what other agents do next.

Every command below is `~/.ethogram/bin/ethogram` (or `$ETHOGRAM_HOME/bin/ethogram`). If it is missing, run
`git clone --depth 1 https://github.com/CarterYoo/ethogram ~/.ethogram` first. It needs Python 3.9 or later, and sets
up its own environment for the map stages the first time they run.

## 1 · Convert the log

Write a small script that converts the user's log into a dataset folder in the common format, keeping every event:

- `events.jsonl`: one event per line with `id`, `ts` (ISO time), `actor`, `text`, `kind` (`message`, `call`, `return`,
  `action`, `result`, `read`, `reasoning`, `self_report`), and when known `to`, `reply_to`, `channel`, `url`
- `actors.jsonl`: `id`, `label`, `kind`, `role`, `model`, `parent`
- `dataset.json`: `name`, `description`, `notes`

The full specification is `~/.ethogram/swarmgraph/format.py`, and `~/.ethogram/adapters/` has converters for real
records.

## 2 · Say what it costs, then run

The LLM stages call the user's own agent CLI, Codex or Claude Code (set `SWARMGRAPH_AGENT=codex` or `claude` to
choose; otherwise the one installed is used). A record of tens of thousands of events takes hundreds to thousands of
calls and tens of minutes or more. Tell the user and get a yes before `prepare`. Every stage is resumable, so a stopped
run continues where it left off.

```bash
~/.ethogram/bin/ethogram --db INDEX.sqlite prepare DATASET_FOLDER        # index, delegated reading, storyline
~/.ethogram/bin/ethogram --db INDEX.sqlite features all FEATURES_FOLDER  # dictionary, two judges, blind test, atlas
~/.ethogram/bin/ethogram --db INDEX.sqlite storyline                     # again, with the measured behaviours
~/.ethogram/bin/ethogram --db INDEX.sqlite influence                     # which behaviour brings on which
~/.ethogram/bin/ethogram --db INDEX.sqlite arc --agent                   # phases, turning points, tested hypotheses
```

## 3 · Show it

Run `~/.ethogram/bin/ethogram --db INDEX.sqlite serve --home /features` in the background and give the user
http://localhost:8792. Add `--share` before anything is shown outside the investigation, so methods appear only by
kind.

## 4 · Answer from the numbers

Query instead of reading raw logs: `~/.ethogram/bin/ethogram --db INDEX.sqlite call TOOL '{"json": "arguments"}'`
(`tools` lists them). Start with `storyline` and `flow_overview`, then `flow_shift` (what rose and fell between two
dates), `flow_influence` (what a behaviour brings on, and what brings it on) and `flow_test` (does a claim hold on
held-out data). Cite event ids, and open them with `get_event` or `context`. The full investigation guide is
`~/.ethogram/skill/GUIDE.md`.

Influence numbers are associations net of the agent, the place and the day, checked against a placebo. Say so, and
describe methods only by kind, never the recipe.
