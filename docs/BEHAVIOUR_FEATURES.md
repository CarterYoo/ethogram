# Behaviour features: an SAE-style atlas for multi-agent records

Status: built (`swarmgraph/features.py`, page `/features`, tool `behaviour_features`, CLI `features`) and run once on
the wiki logs; results in `eval/RESULTS.md` section 15. The clustering map it replaces (`swarmgraph/atlas.py`, page
`/atlas`) is measured in section 14; the numbers in section 2 come from that run. In this first run the reading
agents (inducers, merger, judges, detection) were Claude agents given batch files; `features codex FOLDER` reads the
same files with isolated Codex calls; `features all FOLDER` runs the whole stage with Codex, and `features names` gives
each behaviour a name of one to four words (one Codex call, kept per behaviour sentence) that the `/features` map shows
on the behaviours where words are being reused at the moment, then on the largest.

## 1. Goal

Treat a multi-agent environment as one system and answer, with evidence:

- which kinds of behaviour it contains,
- when each kind happens, how often, and across how many actors,
- how kinds rise, fall, spread and follow one another over time,
- which concrete events show each kind.

The model is a sparse-autoencoder (SAE) feature atlas: features with names, activations per input, names checked by
automated interpretability (autointerp), temporal profiles per feature, and a UMAP layout. Here the features are
behaviours, the inputs are events in their context, and the time axis is days or hours of the record.

## 2. What we tried first, and why it failed

`atlas.py` (version 1) made one point per **stretch** (one actor's events in a row, ended by a 15-minute pause or after
one hour). Code described each stretch by its form (kinds of events, flags and sizes, repeats, pace, links to others,
copied text), by the sweep readers' findings on its events, and by its words with subject words removed and the main
actors and pages erased linearly (LEACE). The stretches were partitioned into kinds (Ward tree in a UMAP space; the
level chosen by held-out prediction and resampling), and an LLM named each kind, checked blind.

On the wiki (6,317 stretches, 3,102 author labels) it ran and its checks passed (12 kinds, 9 reproducible, 10 of 12
names passed), but it did not show what matters:

| Observation | Number |
|---|---|
| Stretches that are a single event (one page save) | 66% |
| Stretches in the 4 largest kinds | 84% |
| Stretches carrying a reader's medium/high concern that fell into the vaguest kind | 83 of 188 |
| Author labels with 3 or more stretches (labels are self-chosen, not agents) | 691 of 3,102 |
| Held-out prediction of the next stretch across levels (4 to 128 kinds) | 0.099 to 0.115, SE about 0.010 |

Causes, in order of weight:

1. **The representation held form, not function.** The same kind of post is a different behaviour depending on what it
   answered (trigger), whom it reached (target) and what followed (outcome). The features held almost none of the
   three, and removing subject words removed much of what remained.
2. **A partition is not a dictionary.** Clustering gives every point exactly one kind, and kinds are sized by how much
   of the record they explain. Mixed behaviour (a stretch that both coordinates and copies) and rare behaviour (a
   handful of events that matter) are absorbed into the large kinds of routine work. An SAE is different: each input
   activates a few of many features, and a feature that fires on three inputs is still a feature.
3. **The unit was too small for the representation.** Two thirds of the points were single posts, so kinds became
   post types.
4. **Author labels are not agents** on the wiki, so per-agent paths and counts of "how many agents" are unreliable.
5. **The prediction target was weak.** The next stretch's form is explained mostly by routine continuing, so it could
   not tell levels apart.

Free-form LLM descriptions as coordinates (summarise each unit, embed the summaries, UMAP them; the design of Anthropic's
Clio) were considered and set aside as the main design. Their coordinates would be the describer's words, not the
behaviour, and the sweep readers' existing descriptions show the risk: 912 "did" lines, median 15 words, mostly what
was done and to which subject, rarely for whom or with what effect. Embedded, they would group by subject again.

## 3. The method in one paragraph

An LLM proposes a **dictionary of behaviour features**, each a short predicate in general words that can be decided
event by event ("copies another agent's text", "addresses a named agent", "promises to report something later"). A
judge LLM reads every unit **with its context** and marks, for each feature, the events in which it is present, citing
event ids that code verifies. A unit's **activation** of a feature is the share of its events marked, with a
confidence from judge agreement; features that code can measure exactly are measured by code instead. Features are
**checked the way autointerp explanations are**: agreement between independent judges, a blind detection test,
density, redundancy, and how much of the record no feature explains. The record is then shown as an **atlas**:
features placed by their temporal profiles (as in an SAE feature atlas), units placed by their activation vectors, a
feature-by-time heatmap with spread across actors, and the source events one click away.

## 4. SAE terms and their counterparts here

| SAE | Here |
|---|---|
| input token in its context | event in its unit, shown with the unit's context |
| feature (a learned dictionary direction) | behaviour feature (a natural-language predicate) |
| encoder activation on a token | judge's mark on an event (0 or 1; 0.5 when two judges disagree) |
| activation summary over a context | unit activation: share of the unit's events marked |
| L0 (features active per token) | features active per unit |
| dead and dense features | features that fire on almost nothing, or on most units |
| reconstruction error | share of events no feature marks ("unexplained") |
| dictionary size, feature splitting | number of features; a feature split into narrower ones |
| autointerp explanation | the feature text itself |
| detection / simulation scoring | blind detection test on top, near and random units |
| temporal profile (e.g. over diffusion steps) | activation per day or hour of the record |
| feature atlas (UMAP of profiles) | features placed by UMAP of their temporal profiles |

Two differences matter. The features are written by an LLM before measurement rather than learned from activations,
so the dictionary can miss behaviours nobody proposed (section 8 measures this as unexplained events and feeds them
back). And activations are judgments, not dot products, so their reliability has to be measured (section 8).

## 5. Units: events in context

**Events** are the atoms the index already holds (a page save, a message, a tool call and its result, a request). A
feature is marked on events.

**Units** are what a judge reads at once. Two shapes, chosen by the record:

- **Boards, wikis, chats** (many actors, short contributions): one unit per actor stretch (as in `atlas.py`: a run of
  the actor's events with no pause over 15 minutes, at most one hour), shown with its context:
  - *before*: the other actors' events in the same channel in the preceding hour (at most a few), and messages
    addressed to the actor (relations);
  - *the stretch* itself;
  - *after*, from code: whether its text was copied by others later (shingle lineage), whether it was answered, edited
    over, removed, or posted again after removal, within a fixed window.
- **Long working records** (transcripts, request logs): one unit per chunk of the sweep (one actor's working context,
  repeats folded). The context is already inside the chunk (what it read, what came back). A folded line stands for
  many events; it is marked once and counted both as one *occasion* and as the number of events it folds.

Subjects stay in what the judge sees, because function cannot be judged without them. They stay out of the feature
texts (section 6).

## 6. Building the dictionary

1. **Sample units** for the inducer, stratified so the rare is present: by time bin, by actor activity, and with
   every unit that carries a reader's concern, a contradicted or overstated statement, a broken instruction or an
   unusual stretch (the 2% least like their neighbours in `atlas.py`).
2. **Seed features** that are already measured:
   - by code (anchors): copies another actor's text; addresses a named actor; removes others' content; posts again
     after its content was removed; repeats one action many times; fails repeatedly;
   - by the sweep readers (fixed questions): makes a statement the record does not bear out; acknowledges an
     instruction and later acts against it; makes a commitment; does something an overseer should know about.
3. **Propose.** The inducer reads batches of sampled units and proposes features. Each proposal has:
   - `text`: a predicate in general words, decidable for one event;
   - `fires_if`: the test a judge applies;
   - `examples`: two event ids where it fires, one where it nearly does but does not.
   Rules: the behaviour, not the subject (no sites, pages, data sets, people or agent names, no numbers from the
   record); observable in the record (an intention only when the record states it); methods described by kind only.
4. **Merge.** One merge pass removes duplicates and near-duplicates; a feature broader than others it contains is
   split or dropped.
5. **Size.** Start with 40 to 60 features. The size is revisited after the checks (section 8): add features where
   unexplained events cluster, merge redundant ones, drop dead ones.

Dictionaries are versioned. A unit's marks are stored per (unit, feature, dictionary version), so a new feature needs
only its own judging pass.

## 7. Measuring activations

**Judging.** One call reads a batch of units (about 20, each with its context) and the dictionary, and returns, for each
unit, only the features present, each with the event ids that show it. Output is sparse, like an SAE's: a unit lists a
few features, not all of them. The schema is strict JSON.

**Verification.** Every cited id must belong to the unit; ids outside it are dropped and counted, as the sweep already
does.

**Strength.**

- Per event: `a(e, f) = 1` if marked, else `0`. Where two judges read the event, `a = 0, 0.5 or 1`.
- Per unit: `A(u, f) = sum of a(e, f) over its events / number of events` (also kept: the count, and occasions for
  folded lines).
- Code-measured features use the code value directly (share of text copied from others; actors addressed; removals).

This is strength as *how much of the unit shows the feature* and *how sure the judges are*, not the intensity an SAE's
magnitude encodes. It is enough for everything the atlas needs (profiles, vectors, top examples).

**Not used.** Asking for a 0 to 10 strength: scales drift between features and calls and cannot be compared. Token log
probabilities: the Codex CLI does not return them. Where an intensity is wanted ("how persistently"), it is graded
against a rubric with an anchored example per level and kept only if judges agree on the grade.

**Aggregates.**

- Temporal profile: `P_f(t) = sum of a(e, f) over events in bin t / number of events in bin t` (a rate), and the count.
- Spread: the number of distinct actors with `A(u, f) > 0` in bin t (in labels where actors are self-named).
- Top examples of a feature: units ranked by `A(u, f)` times agreement, the counterpart of top-activating contexts.

## 8. Checking features

Each check has a number, and each number has a use.

| Check | How | Used to |
|---|---|---|
| Reliability | a second judge on about 10% of units; per feature, event-level agreement (Cohen's kappa or F1) | drop or rewrite features below 0.6 |
| Anchor accuracy | the judge's marks on the code-measured features against code | estimate how far the judge can be trusted on the rest |
| Detection (autointerp) | a fresh judge gets only the feature text and a shuffled mix: 6 top units, 6 near units (similar activation vectors, feature off), 6 random units; it picks those the text fits | precision on near units, recall on top units, coverage on random units against the feature's density: too broad, too narrow, or right |
| Density | share of units and of events where the feature fires; specificity in bits = -log2(density) | flag dense features (above 30% of units) as too broad and dead ones (fewer than 3 units) |
| Redundancy | co-activation (Jaccard over units) between features | merge pairs above 0.8 |
| Unexplained events | share of events no feature marks, and a sample of them read by the inducer | propose new features (one loop), the counterpart of reconstruction error |

The blind check in `atlas.py` already implements the detection test (own, near and random stretches; a name fails
when it fits at least two more random stretches than its share predicts) and can be reused as it is.

Judges should come from more than one model where possible (the reliability check then also measures how much the
marks depend on the model).

## 9. Views

For people (plain words, no ids or feature codes on screen, sources on click, one meaning per view):

1. **When each behaviour happened.** Features by time bin; shade = rate, a second row or tooltip = how many actors.
   Click a cell: the units and their events.
2. **Feature atlas.** One point per feature; position = UMAP of its temporal profile (z-scored), so behaviours that
   rise and fall together sit together; size = density; colour = theme (features grouped by co-activation, the groups
   named by an LLM). Play the timeline: points brighten in the bins where they fire.
3. **Unit map.** One point per unit; position = UMAP of its activation vector (cosine on `A(u, ·)`); colour = its
   strongest feature; rings = a reader flagged something. Rare features stay visible because a unit is coloured by
   what it does, not by which partition it fell into.
4. **What follows what.** For each feature, the features that most often come next in the same channel or for the
   same actor (lift over chance). Optional.

For analysts (MCP tools, proposed names):

- `features`: the dictionary with text, density, reliability, detection scores and temporal profile;
- `feature {"id"}`: top units with the marked events, by period;
- `unit_features {"unit"}`: the features a unit activates, with events;
- co-activation queries: units where two features fire together in a period.

## 10. Levels of detail

No single mathematical level is chosen. The level is the dictionary: its size, and a hierarchy from themes to
features to split features. It is set by the checks of section 8: features are added where unexplained events cluster
and split while both halves stay reliable and pass detection; they are merged when redundant and dropped when dead.
The same trade-off as an SAE's dictionary size: more features explain more of the record but become rarer, more
redundant and harder to judge reliably.

## 11. Cost (estimates)

For the wiki (6,317 units, 50 features, Codex at low effort, 12 calls in parallel):

| Step | Calls |
|---|---|
| Induction and merge | 10 to 20 |
| Judging, 20 units per call | about 320 |
| Second judge on 10% | about 32 |
| Detection test, 1 to 3 per feature | 50 to 150 |
| Theme names | a few |

About 450 calls, roughly 30 to 40 minutes. A feature added later costs one judging pass for that feature alone (many
units per call). For long records the units are the sweep's chunks (432 for 1.13M government request events), so the
judging pass is a second, narrower sweep. Every call is cached in the work store.

## 12. A small validation (development first)

On the wiki only, with criteria fixed before the run:

1. The main lines of the outside analysis of this data (collusion.wiki) appear as features whose temporal profiles
   match its timeline. Not held out: its front page was read before.
2. Reliability: most features reach event-level agreement of 0.8 between two judges.
3. Concerns are not diluted: units carrying a reader's medium or high concern activate features that routine units do
   not, instead of falling into the largest routine group (version 1: 83 of 188 did).

A held-out check needs a record whose outside analysis has not been read.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Judges mark inconsistently | reliability per feature; anchors with known answers; strict schema; batches shuffled |
| Subjects leak into feature texts | inducer rules; a code check for names, links and numbers from the record in feature texts |
| Judges read intent into the record | features must be observable; intent only where the record states it |
| The dictionary sees only what the inducer noticed | stratified samples that include rare and flagged units; the unexplained-events loop |
| Label churn inflates spread | spread reported in labels where actors name themselves; cascades (connected events across actors) as a later unit |
| Untrusted text in prompts | judges run with no tools (`llm.isolation()`), as the sweep does |
| Cost on large records | chunks as units; caching; new features judged alone |

## 14. What exists and what is new

Exists: the event index with relations; chunks and the sweep readers with verified citations (`chunks.py`, `sweep.py`);
code-counted behaviour over time (`behaviour.py`); stretches, copy shingles and the blind name check (`atlas.py`);
isolated Codex calls with caching (`llm.py`); the `/atlas` page to extend.

New: the dictionary (induction, merge, versions), the judging pass with event marks, the checks of section 8, the
feature atlas and unit map views, and the analyst tools.
