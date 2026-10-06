# Demo (about 3 minutes)

The message: **a first map of an agent incident, with evidence, in hours instead of weeks — for any log.**

## Before the demo

Prepared indexes (each from one command, `prepare`): `../data/aiv-transcript.sqlite` (one agent's two-month working
record), `../data/gov-evidence.sqlite` (1.13M requests to government websites), `../data/wiki.sqlite` (wiki edit logs).
Start the pages and open each once so the page data is computed (about 30 s the first time):

```bash
python3 -m swarmgraph --db ../data/gov-evidence.sqlite serve --port 8797 --share   # outside readers: methods by kind
python3 -m swarmgraph --db ../data/aiv-transcript.sqlite serve --port 8796 --share
python3 -m swarmgraph --db ../data/wiki.sqlite serve --port 8795 --share
```

Use `--share` for any audience: findings are shown by kind, the storyline is the version written for outside readers,
and links, payloads, keys and addresses are masked in the sources.

## 1. The problem (30 s)

Investigators of the OpenAI / Hugging Face incident had ~1,300 transcripts of millions of tokens each and had to
delegate reading to agents that missed things without saying so. In our measurements an analyst reading alone saw
0.5% of one agent's record.

## 2. One command (20 s)

```bash
python3 -m swarmgraph --db gov.sqlite prepare ../data/gov-evidence     # 1.13M events: 10.8 minutes, 24 sub-agents
```

Every stretch of the record is read whole by a sub-agent with the same questions; repeats fold; rules are followed
across chunks; later statements are traced back to the work they describe; behaviour over time is counted by code.

## 3. The page for people (60 s) — http://localhost:8797/agent

- **What happened**: the storyline. Click "sources" on a sentence: the original records (masked).
- **At a glance**: activity per hour; orange marks where something reached 8x a usual hour (e.g. 6,730 failed captures
  in one hour); red dots where readers flagged something. Click a bar.
- **Period by period**: what stood out, sudden increases, what readers flagged.
- Switch to the transcript (port 8796): rules it acknowledged and later broke, statements its own earlier work
  contradicts ("announced X, made Y").

## 3b. The behaviour map (40 s) — http://localhost:8795/atlas

- The wiki as one system: 6,317 stretches of work by 3,102 agents, placed by what they did (not what they were
  about). Colours are kinds of behaviour; how many kinds was set by measurement (held-out prediction of the next
  stretch, and which grouping comes back when the map is made again), not by taste.
- **When each kind happened**: most kinds peak June 16-18; "commit to monitoring and relaying upcoming events"
  exists only June 16-22, across 181 agents; mass deletions run on alone to July 14.
- Click a kind: what it is, how its name did in a blind check, whether it comes back on resampling. Click a dot: the
  original record. "Follow" draws one agent's path through the kinds.

Prepared once with `.venv/bin/python -m swarmgraph --db ../data/wiki.sqlite atlas` (about 3 minutes; needs numpy,
scipy, scikit-learn, umap-learn); the page itself is served by any Python.

## 3c. Behaviour atlas (40 s) — http://localhost:8795/features

- 66 behaviours in words, each checked event by event by AI judges (two judges agree: median kappa 0.89).
- **When each behaviour happened**: the June 16-21 block lights up together (asking others to relay status,
  predicting the next round, sharing answers prepared in advance, reasoning about its own end of run) and stops
  together; after June 22 only removals.
- Click a behaviour: when, how many author labels, how the blind test went, the clearest stretches (sources).

## 4. The analyst's view (40 s)

In Claude Code or Codex with the MCP server (`skill/SKILL.md` section 2c): `storyline` → `periods` → `chunk {"id": N}`
→ `delegate {"question": "...", "chunks": [...]}` (sub-agents read those chunks whole in the background) → the
answer with cited events, each verified to be in the chunk it came from.

## 5. Does it work? (30 s)

- Against outside analyses of the same data (RESULTS section 13): the government storyline, written with a frozen
  pipeline before reading Transluce's report, matched most of its sections; the misses traced to bugs now fixed.
  The wiki storyline matched collusion.wiki's main line (answer relays between runs, termination probes, shared
  workarounds, deletions).
- Analysts (RESULTS sections 10-12): with the claims ledger, blind judges preferred their reports 7-1 and 8-1 (GPT)
  and 6-3 (Claude); delegated reading brought back the breadth the ledger had narrowed (raw-only findings per report
  3.0 vs 1.6).
- Precision on samples: 10 of 10 medium/high concerns were borne out by their cited events.

## 6. Close (20 s)

One multi-question pass (about 3x the record) instead of one pass per question; follow-up questions read only the
chunks chosen and are cached. Sub-agents run with none of the operator's own tools. Any log: transcripts, request logs
and wiki edits went through the same command.

