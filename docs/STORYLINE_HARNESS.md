# Reproducible incident storyline harness

The default harness discovers connected incidents in raw events, reads their local
context, and writes a reviewed action sequence. A feature atlas supplies measured
context and verified map membership. It does not determine what the story is.

```
Dataset adapter → frozen raw-event corpus and discovery catalog → scout
    → deterministic source-context expansion → writer → graph validation
    → independent review of scenes AND connections → accepted incident scenes
    → timestamped action playback / optional atlas projection
      ↘ complete three-stage audit → deterministic replay without an agent call
```

The earlier feature-overview harness remains available with `--overview`. Its tours
can accurately describe sampled behaviour without explaining a connected incident.
They are retained as overview artifacts, rather than silently reclassified as stories.

## Incident contract

A story contains a concrete question, a connected set of source actions, supported
connections, and an observed, reported, or unresolved outcome. It has no prescribed
topics, dates, actors, or required number of arcs. A disconnected list of factual
observations cannot qualify as an incident. No discovered or accepted incident is
an explicit result, rather than a reason to invent an arc.

The scout receives bounded discovery leads from prior chunk readings and diverse
source records. Readings are retrieval hints. A deterministic context expansion
fetches both ends and nearby records by actor, shared object, and parent references.
Those fields explain where to read, not that information travelled or a response
occurred. The writer and reviewer receive source text and the dataset's own notes
about identity, timestamps and relation semantics.

Each scene contains timestamped steps with a source event, a plain role and action,
and an `observed`, `reported` or `inferred` status. Each connection names both
endpoint events, its kind and basis, an English caption, and exact supporting source
snippets. Code verifies actual endpoints, quotes, time order, graph connectivity,
and cited feature context. The independent reviewer checks the meaning of each
connection and the explanatory adequacy of the story. Rejecting a necessary link
or scene drops its dependent story and summary.

A source actor label is not a confirmed individual or agent. A revision parent is
not automatically a reply: the adapter preserves the dataset's declared meaning.
A post announcing success proves that the statement was posted; its result remains
a report without independent evidence. Similar content and chronological order can
support an interpretation, but cannot silently become verified information transfer.

## Honest animation

Playback follows exact source action order and shows the role, time, one-line action
and supported connection. The same action remains selected on pause and view changes.
Source records open on click. Timeflow highlights verified source-member stretches;
connections refer to the source record rather than an actor travelling through UMAP.
Behaviour UMAP keeps its feature positions fixed and supplies context beside the
same action sequence.

Raw incident evidence is eligible even if no atlas judge reviewed it. Such an action
remains visible with an explicit unavailable map position. It receives no invented
coordinate or feature mark, and targeted incident retrieval never changes the uniform
sample or prevalence denominators. Feature context is optional and requires actual
cited tagged-stretch support. Stretch membership is not an exact event feature tag.

## Run and replay

```
python -m swarmgraph --db dataset.sqlite feature-stories
python -m swarmgraph --db dataset.sqlite feature-stories --force
python -m swarmgraph --db dataset.sqlite feature-stories --export-input snapshot.json
python -m swarmgraph --db dataset.sqlite feature-stories --export-run run.json
python -m swarmgraph --db dataset.sqlite feature-stories --replay run.json
python -m swarmgraph --db dataset.sqlite feature-stories --overview
```

GET and page loading do not call agents. Explicit Create starts separate scout,
writer and reviewer stages. Default stages use isolated Codex processes; clients
can be injected, including responses from actual collaboration subagents. Distinct
stage instances and their exact inputs, outputs, schemas, configuration and source
snapshot are retained. Replay uses the frozen corpus and decisions, reruns context
expansion and validation, and makes no agent calls. Changed source, map, prompt,
schema or configuration cannot reuse a mismatched result.

`SQLiteAtlasAdapter` and `MemoryAtlasAdapter` expose `load_atlas`, `scan_events`,
and `reading_leads`. The scan returns records, total count and source notes; bounded
scan/catalog/context coverage and truncation are retained. A custom adapter uses
these same fields for another dataset. It must preserve absent relations as unknown.
The incident corpus is separate from the sampled atlas data.

| Adapter method | Incident input |
| --- | --- |
| `load_atlas()` | Saved features, chronological bins, reviewed source-member stretches and fixed coordinates |
| `scan_events(limit)` | `{events, total, notes}`; each event has `id`, `ts`, `actor`, `kind`, `text`, and optional `channel`, `reply_to`, `meta` |
| `reading_leads()` | Optional `{event, summary, category}` retrieval leads; an empty list is valid |

Declare whether relations represent replies, revisions or something else in `notes`.
Unknown relations remain unknown. A source event may have no reviewed atlas member.

```python
from swarmgraph import incident_storylines as harness
from swarmgraph.storyline_adapters import MemoryAtlasAdapter

adapter = MemoryAtlasAdapter(
    atlas, events,
    notes="Actor labels are unverified; reply_to identifies page revisions.",
    reading_leads=optional_leads,
)
snapshot = harness.snapshot_from_adapter(adapter)
artifact = harness.execute(snapshot, scout_client, writer_client, reviewer_client)
assert harness.replay(artifact) == artifact["result"]
```

The three clients implement `run(prompt, schema) -> (structured_json, seconds)`
or are distinct callables returning structured JSON. Frozen file responses from
actual collaboration subagents use the same boundary. An invocation must retain the
exact prepared inputs, stage identity/configuration and original responses; copying a
hand-written narrative into storage does not constitute a harness run.

Public narration uses plain English and describes technical methods only by kind.
Original identifiers and source snippets stay in the evidence and audit layers.

---

The previous overview contract is documented below for existing artifacts.

# Reproducible storyline harness

The harness turns measured behaviour data and source excerpts into a reviewed scene
plan. Time flow and Behaviour UMAP render that plan. It does not require an existing
storyline, a particular dataset, or Wiki feature identifiers.

```
Dataset adapter → frozen evidence snapshot → writer → structural validation
    → independent reviewer → accepted scene plan → Time flow / Behaviour UMAP
                            ↘ complete audit record → verified replay
```

## Run, export, and replay

An indexed dataset needs a saved behaviour atlas first; see BEHAVIOUR_FEATURES.md.
Viewing `/features` or polling `/api/feature_storylines` never calls an agent.
The **Create storylines** button explicitly starts a background run. Concurrent
clicks on the same dataset reuse that job; different datasets have separate jobs.

```sh
python -m swarmgraph --db dataset.sqlite feature-stories --overview
python -m swarmgraph --db dataset.sqlite feature-stories --overview --force
python -m swarmgraph --db dataset.sqlite feature-stories --overview --export-input evidence.json
python -m swarmgraph --db dataset.sqlite feature-stories --overview --export-run run.json
python -m swarmgraph --db dataset.sqlite feature-stories --overview --replay run.json
```

Export and replay make no model calls. Replay checks the saved protocol, inputs,
decisions, and hashes before publishing the plan to the matching dataset. Changed
atlas data or selected source evidence makes a saved tour stale. The page withholds
it until a matching run is available.

The default stages are separate isolated local Codex calls through the existing
`llm.Codex` client. `SWARMGRAPH_MODEL` and `--effort` select their configuration.
The harness accepts injected clients or callables, so another provider can implement
`run(prompt, schema) -> (structured_json, elapsed_seconds)` without changing the
evidence, validation, storage, or animation stages. Writer and reviewer must be
separate instances. Review is a fresh stage with the evidence and candidate plan.

## Dataset boundary

`storyline_adapters.py` provides `SQLiteAtlasAdapter` and `MemoryAtlasAdapter`.
A custom adapter implements three methods:

| Method | Return value |
| --- | --- |
| `load_atlas()` | An atlas in the shared format below |
| `fetch_events(ids)` | Matching event dictionaries; omit missing IDs |
| `load_prior_story()` | Optional context dictionary, or `{}` |

The atlas contains `features` (unique IDs and descriptions), canonical chronological
`bins` (`YYYY-MM-DD` for `unit="day"`, `YYYY-MM-DDTHH` for `unit="hour"`),
`n_bin` (uniform sample sizes), `profiles` (per-feature `rate` and `units` arrays),
and reviewed `units`. Each reviewed unit has an ID, actor, start/end timestamps,
event `ids`, fixed `x`/`y` coordinates, and `f` activation shares in `(0, 1]`.
`fpos` contains fixed behaviour UMAP coordinates. The web renderer also uses themes,
actor labels, feature counts, and descriptive metadata supplied by `features.page`.

Events have `id`, `ts`, `actor`, `kind`, and `text`. The adapter verifies that cited
events really belong to the referenced actor and time span. It redacts and bounds
excerpts before supplying them to agents. Previously written summaries are optional
context, never independent source support.

```python
from swarmgraph import feature_storylines as harness
from swarmgraph.storyline_adapters import MemoryAtlasAdapter

snapshot = harness.snapshot_from_adapter(MemoryAtlasAdapter(atlas, events))
artifact = harness.execute(snapshot, writer_client, reviewer_client)
plan = harness.replay(artifact)  # deterministic, no model or database needed
```

This boundary supports a file loader, another database, or a connector. Convert its
measurements and events to the shared format; keep dataset-specific logic in the
adapter. `execute` and `replay` do not depend on SQLite.

## Agent and animation contracts

The versioned writer schema returns a title, summary, and up to four stories, each
with up to five scenes. A scene contains `title`, a short `caption`, exact `start`
and `end` bins, up to five `features`, actual source `events`, and a `caveat`.
Code assigns story and scene IDs. Structural validation rejects unknown features,
invalid dates, nonchronological scenes, missing citations, and citations outside
the scene span. Every highlighted feature needs a cited tagged stretch in that span.

The independent reviewer accepts or rejects each scene and story, plus the overall
title and summary. Missing or duplicate verdicts fail closed. Rejected scenes are
omitted; repair suggestions stay in the audit record. No automatic repair fabricates
additional evidence. Structural checks and agent review reduce unsupported claims;
they do not prove that a model's interpretation is correct.

The renderer highlights the selected features, labels them, shows a one-line
caption, and walks through the scene's bins. It preserves scene position on pause
and view changes. Sources open on click. Manual scrubbing or feature exploration
exits the tour. Time flow connects only consecutive observed calendar bins;
unsupported intervals fade without an invented path. Behaviour UMAP keeps positions
fixed and animates prevalence. Measured endpoint notes are computed from the data,
separately from agent narration.

## Measurements and limits

Uniform `rate` is the mean marked-event share across sampled stretches. Uniform
`units / n_bin` is the share of sampled stretches showing a feature. These are
different measurements. Missing uniform samples are unknown, not zero activity.
Time-flow centres use activation-weighted locations across all reviewed stretches,
including deliberately selected unusual examples. Centroid movement does not show
individual trajectories, changing feature meaning, or causation.

The saved atlas retains stretch activations rather than exact event annotations.
A source event within a tagged stretch supplies contextual evidence; it does not
establish that this exact event was tagged. The prompts and reviewer enforce that
distinction. Evidence is bounded to 120 excerpts and about 88,000 JSON characters.
The input manifest records the actual selected evidence; it does not hash every
uncited event in the dataset. An oversized atlas must be adapted to a smaller window.

## Audit and reproducibility

Each artifact stores the complete snapshot, protocol version, prompt and schema
hashes, model configuration, exact stage prompts, structured outputs, elapsed times,
validation errors, reviewer decisions, accepted plan, and acceptance counts.
`feature_storyline_runs` in the work database retains runs; `stories` points to the
latest one. Rejected runs are recorded too; use `--force` to try new generation.
Failed agent calls leave an error state and do not publish a partial tour.

Replay recomputes validation and review filtering from those saved outputs and
requires the same accepted plan. Integrity hashes detect alteration but are not
cryptographic signatures. A new model call can produce a different story even with
the same configuration; exact reproducibility comes from replaying the audit record.
Protocol changes require a compatible replay implementation or a new run.

Tests use synthetic daily and hourly datasets and injected stages. They cover
dataset adapters, evidence membership, missing observations, rejected and malformed
plans, independent review, caching, stale inputs, export/replay integrity, and explicit
HTTP generation. No production analysis is needed to verify the harness.

```sh
python -m unittest discover -s tests -p 'test_feature_storylines.py'
python -m unittest discover -s tests -p 'test_story_jobs.py'
node tests/check_story_playback.cjs
```
