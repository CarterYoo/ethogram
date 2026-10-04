# Diagnosis: why the summary structure did not help the analyst, and what can be fixed

State on 2026-10-01. Every number comes from `eval/RESULTS.md` (§2–8) or from the script named next to it. "Hold" means
a hypothesis passed the code-graded bar on events the analyst never saw (rate after A at least 10 points above the
same actors' own baseline and 1.5x it, in both halves, bootstrap bound above zero; mismatched A/B pairs pass by
accident 7–11% of the time).

## 0. The problem we were supposed to solve (added 2026-10-02)

Sections 1-7 below diagnose why the structure did not help an analyst write regularities that hold next month. That
was the wrong yardstick. The incident that motivates the project (METR's report on the OpenAI / Hugging Face attack,
RESULTS section 9) needed something else: over data no team can read (about 1,300 transcripts of many millions of
tokens, over 70,000 board messages), surface ALL the relevant activity, say what agents did as opposed to what they
claimed, and hand the people in charge findings they can check. Their analysis agents failed in exactly the ways our
analysts did: they missed data without anyone noticing, produced voluminous output that "did not highlight the most
important findings", and needed surrounding context to read any single message.

Measured on our own 48 runs: the analysts saw the 68 messages in which agents retract or correct an earlier claim at
the chance rate (0.8 per run against 0.7 expected), with or without the structure. Their own regexes matched 66 of the
68; what failed was selection, not retrieval. The theme layer had discovered the class (871 events, 71% of the 68) and
printed its name to all 24 board analysts, 19th of 35 because the board was ordered by size; 1 of 24 opened it. Size has
no relation to what an investigator wants to open (rank correlation 0.03 and 0.05 on two datasets). That is P14 below,
and it is fixed: one LLM call rates each class for investigative interest (stable across passes, 0.89), the board is
ordered by it, and a class can be read whole. It did not make analysts find more (RESULTS 9): asked an investigation
question, all 30 runs found the claims-vs-reality pattern, raw included, and raw was cheapest. On chat-scale logs a
strong analyst with a good question is at the ceiling; the question raised exposure to the retraction class 6.6 times,
the structure not at all. A structure has to earn its place where reading fails: the action and reasoning layer, at
the scale of the incident.

What still has to change, in order: (1) measure recall of what matters and the checkability of claims, not hold rates;
(2) put the concern lens (exhaustive, per event, with basis; precision 1.00 / recall 0.91 on the wiki) back into the
analyst's path, where it was left out of every big-window run; (3) summarise with context and importance as METR
ended up doing (per context window, the few writes and reads that changed what the reader did), not every event alone
with one preceding message; (4) index the action layer (one AI Village agent's own transcript: 912 MB, 244,820 turns,
never indexed), where what agents did can be set against what they said.

## 0b. The action layer: the first place the structure made the analyst better (added 2026-10-02)

Following section 0, the structure was moved to where reading fails: one AI Village agent's own working transcript
(912 MB; converted to 219,740 events with two new kinds, `reasoning` and `result`), with a ledger that sets every claim
against the record (own claims vs its actions and their results; claims it heard from others vs its own checks; private
plans to conceal or evade vs what it then did), flagged by code and judged by an LLM from each case's window. Same
investigation question to both arms, rubric frozen first, plus a blind pairwise judge that must agree in both orders
(RESULTS section 10):

- **Overseers should rather have the ledger analyst's report in 7 of 10 pairs (raw 1, 2 ties), and after reframing
  in 8 of 10 (raw 1, 1 tie; p = 0.039)**, with 15-19% fewer tokens and the same time. The judge's reason: verification
  failures grounded in tool results ("tests pass" for a pull request tested on `main`, a scan declared clean despite
  an error, "no file matches" after a matching file), which raw analysts rarely reach in 117 MB.
- The structure still narrowed attention: no ledger analyst opened the cases about claims it heard from others (0 of
  20 runs), framed either way; raw analysts reported how the agent handled other agents' claims in 8 of 10 runs (ledger
  1/10, then 3/10). What raw analysts found there is accusations against this agent and its rebuttals, a kind of case
  the ledger does not compute yet.
- Ledger precision, checked by reading: 10 of 10 sampled material mismatches real (4 minor); 4 of 4 `deceives` cases the
  sanctioned saboteur day; 25 of 25 rejected intent cues innocent.

What made the difference is the regime, not a better summary: on chat-scale logs the strong analyst is at the ceiling
(section 0); on the action layer the code computes a join (statement x preceding actions x results) the analyst does
not make by reading, and an LLM judges each joined case with its context, as METR's investigators did by hand.

## 1. Verdict

1. As built, the structure did not make the analyst's hypotheses hold more often on unseen data (10% vs 13% raw,
   p = 0.78), cost 1.5x the tokens and time, and the hypotheses that grew from the board's leads held least (7–9%).
2. The analysts were not confused by it and did not fail to use it. They opened the orientation once and went back to
   the raw events, because the board answers "what happened in July", the task is "which regularity carries over",
   and nothing on the board checks a claim.
3. A claim check inside the analyst's loop (`grep`, `context`, `test_claim`) raised the held-out hold rate (33% vs 13%,
   rawtool vs raw; 28% vs 10%, sgfixed vs swarmgraph), but part of that is built into the claims: in 16 of rawtool's 47
   after-then hypotheses, 40% or more of the A-events are also B-events (the same words on both sides), and those hold 10
   of 16. Without them rawtool is 26% vs raw 18% (p = 0.56) and sgfixed 30% vs swarmgraph 11% (p = 0.048).
4. That fix has a price: with it the analysts propose shallower claims (48–57% are topic echoes, 62–80% obvious),
   and counting only holds that are not obvious the tool conditions are not ahead (2–3 and 1–2 vs 6–7 per 60).
5. On top of the claim tools the structure adds nothing measurable (28% vs 33%, p = 0.69) for 1.6x the tokens.
6. Reading eight trajectories by hand (§3b) showed why: the analysts need text, instances, one way of counting and the
   rare telling events; the structure gave labels, example pairs that were not instances, a second way of counting,
   and nothing for the telling events.
7. Fixable? Of the thirteen problems below, two are fixed and tested (P7, P10), one is built but weaker than first
   reported (P1), three are partly fixed with the main part still open (P4, P5, P8), two are a changed mechanism or
   stance not yet tested on analysts (P6, P9), four are open designs (P2, P3, P12, P13) and one is a ceiling of this
   task and data (P11). The cheapest and most certain are P12 (the checker's guards) and P13 (a ledger of corrections).

## 2. The structure, end to end

| stage (command) | made by | what it holds | cost on 68k events | meant for | seen in the trajectories |
|---|---|---|---|---|---|
| adapter + `build` | code | events, actors, relations (addressed / replied / invoked / returned, each with the event that shows it and how it was found) | seconds | everything | the analysts' index; relations rarely queried |
| `tag` (notes) | LLM, per event | 20-word summary, 17 behaviour tags, addressees, `responds_to`, `continues`, claims (all notes together: ~0.8M tokens to read) | 2.3 h for 68k events with 32 workers (wiki: 52 min per 20k with 24) | search, tag metrics, cards | summaries used as match text by the grader; analysts read raw instead |
| `structure` | code | useful links (behaviour toward another actor), note search | minutes | link/interaction queries | not called |
| `cards` | LLM | trajectory / link / swarm cards, every sentence marked observed / claim / inferred, ids checked | wiki: 386 cards in 31 min (12 workers) | people, orientation | not built for the big window (they would summarise the whole log, including August) |
| `align` | LLM | inferred environment, per-event concern flags with `basis` and `unconfirmed`, concern cards | one LLM call per event | finding misaligned behaviour | not computed for the big window (the hypothesis task does not use it); wiki: the misuse was found blind by raw and structure alike |
| `themes` | LLM | ~35 dataset-specific recurring situations/moves, assigned per event | ~1 h | leads for hypotheses | `theme_map` opened once per run |
| boards (`map`, `node`, `theme_map`, `theme`) | code | counts vs baselines, clue + event ids + `open` target | seconds | orientation, leads | the first 2–4 commands of every structure run |
| hypothesis ledger | code | propose / measure / verdict with gates | – | the core loop in SKILL.md | **0 calls in 114 runs** |
| 38 tools + `SKILL.md` | code | CLI JSON and MCP | – | the interface | 15 of 38 used; 71 commands opened the DB directly |
| claim tools (new) | code | `grep`, `context`, `test_claim` (one implementation shared with the grader) | seconds | check a claim before reporting it | **24 of 24 tool runs**, 8.5–18.5 commands per run on average |
| people view (`story`, explorer) | LLM + code | storylines, change points, network | minutes | people, not the analyst | not part of the analyst test |

The design intent was: the analyst orients on cards and boards, picks leads, and reads only what it must; code
computes every number; verdicts are gated. What the trajectories show instead is in §3.

## 3. What the analysts did (48 big-window runs, every command read)

| per run | raw | swarmgraph | rawtool | sgfixed |
|---|---|---|---|---|
| input tokens / minutes / commands | 272k / 2.3 / 7.4 | 408k / 2.9 / 14.8 | 1,056k / 8.0 / 24.9 | 1,725k / 12.0 / 25.8 |
| commands that are our boards | 0 | 6.6 | 0.1 | 2.2 |
| commands that are claim tools | 0 | 0 | 18.5 | 8.5 |
| distinct events shown (of 36,994) | 223 (0.6%) | 249 | 275 | 314 |
| hypotheses whose evidence was first shown by... board / claim tools / raw reading | 0 / 0 / 60 | 33 / 0 / 27 | 0 / 53 / 7 | 14 / 24 / 22 |

The loop is the same everywhere: skim a sample, narrate, regex over the raw text to print examples, write five
hypotheses. 0 of 12 raw runs computed a rate over time, 3 of 12 structure runs did. A board adds reading before the
first look at an event (13k characters vs 1k) and takes nothing away: regex probing per run is the same (3.4 raw, 3.3 structure).
Twelve of twelve structure analysts wrote down what was wrong with the board (§3b).

## 3b. Eight trajectories read by hand

Three runs with the first board, three with the repaired board (including the two slowest), one raw and one with claim
tools only, command by command, plus the written feedback of all 24 board runs (counts below are keyword matches in that
feedback, so lower bounds), then each observation checked in code. In every condition the analyst does the same five
things: sample the text, notice a narrative, collect instances with a regular expression, read the events around them,
write the hypothesis. The structure stood in for none of them:

| the analyst needs | what it needed | what the structure gave | what followed |
|---|---|---|---|
| to see what the log is | a spread of verbatim messages | labels and 20-word summaries (`map` 8k characters, `theme_map` 15k, later 27k) | the raw analysts' even sample of ~45 messages (35k characters) says more; labels had to be checked against text |
| instances of a behaviour | every member of a theme, with text, spread over days and actors | `theme`: the earliest ten one-line summaries | analysts rebuilt each theme with their own regular expressions (scripts named `pause`, `ethics_adopt`, `ci_adopt`...) |
| a lead it can trust | example pairs that are instances of it | the top lead's example pair was a broken document and the check of a different site, in both boards | follow-ups reuse one event (9/12, first board), concern other projects (4/12 and 6/12), themes mix plans, reports and checks (5/12 and 6/12) |
| to check it | one way of counting | the board counted message-only episodes of LLM labels; `test_claim` counted regex hits in summary + raw text over every event kind, session intentions and memory snapshots included | the top lead (gap +0.21, bound +0.14) read gap +0.11, 1.29x, `not_holds` as patterns; 6/12 wrote that leads did not survive `test_claim`, 5/12 that the two count differently, 10/12 that it counts session intentions |
| to learn the tool | one page | `SKILL.md`, 13k characters, opening with how to convert data and a hypothesis ledger nobody used; then `claims.py` source (22k characters) because nothing said what "holds" means (24/24 tool runs read our code); one run opened the SQLite file to read its schema | ~17k tokens before the first test; 67k vs 42k input tokens per command |
| to read a result | a verdict and a few lines | one batch of `test_claim` calls printed 37k characters (`pairs_to_read` for every spec) | carried through every later turn |
| to notice what matters | the rare, telling events | nothing | the corrections and retractions the analysts cited (a "simulated estimate generated without a live web search", a configuration reported pushed and three minutes later "cloning failed", a count retracted from eleven to nine and corrected downstream) were found by regular expressions they happened to write (P13) |

Not a problem: speed. One `test_claim` call takes 1.7 s and one `grep` 0.7 s on the 37k-event index; the 35-minute runs
were the analyst's own time.

## 4. Problems, with evidence, cause and what can be done

Status: **fixed** = changed and measured; **mechanism** = changed, effect on analysts not measured; **open** = designed
only; **ceiling** = not a defect of the structure.

### The loop

**P1. No measurement in the analyst's loop.** Regularities were read off examples; the ledger that gates verdicts was
never called (0 of 114 runs) because it asks for a metric from a catalog with tag arguments, which is not how an analyst
works (grep, Python). Fix: `grep` / `context` / `test_claim`: free-text patterns for A and B, the actors' own off-windows
as baseline (activity-conditioned), both halves, a bootstrap bound, a plain-language reading, pairs to read from
the start / middle / end. Status: **built, and weaker than first reported**. Held-out hold rate 33% vs 13% and 28% vs
10% (p = 0.017 and 0.019), but without the echo claims of P2/P12 it is 26% vs 18% (p = 0.56) and 30% vs 11% (p = 0.048).

**P2. A tool narrows what gets proposed.** `test_claim` checks one shape of claim: pattern A is followed by pattern B
within a window. The tool analysts converge on it: topic echoes 57% / 48% (0% without the tool), "what one named agent
does" 0% (28% without), obvious 80% / 62% (15%), mean insight 1.6 / 1.9 (3.5). Hold-and-not-obvious: 2–3 and 1–2 vs 6–7
of 60. In 16 of rawtool's 47 after-then hypotheses 40% or more of the A-events are also B-events; those hold 10 of 16,
the other 31 hold 8 (26%) (`echo_check.py`; raw 0 of 3 and 7 of 38, swarmgraph 0 of 1 and 5 of 47). Cause: the analyst
keeps what it can verify; the verdict is the reward. Status: **open**. Design: widen the
claim language (per-agent and per-model filters, conditions on who was addressed, three-step sequences, claim vs
later action) and allow the structure's labels as predicates (`tag:ask_help`, `theme:...`) so rich claims become
checkable; ask for hypotheses by kind in the prompt. Judge hypotheses by "holds and not obvious", not by "holds".

**P3. Looks are not counted.** Tool analysts test dozens of candidate claims per run (27–84 specs in 11 of 12 rawtool
runs, 61 one-by-one checks in the twelfth) and report the winners. Their hold rate falls from 50% / 45% on the month they
searched to 33% / 28% on the next; the raw analysts, who search nothing, stay at 12% / 13%. Cause: each call returns
a verdict and forgets. Status: **open**. Design: the tool keeps a ledger of its own calls (file in the working
directory), prints "looks so far: N" and raises the bar with N; hold out a confirmation slice of the analyst's own
window that only a limited `confirm_claim` can read (budget ~5, every use logged).

### The board

**P4. The board's leads are in-sample and topic-bound.** Hypotheses that grew from events first shown by the board hold
7–9% in August, down from 24% (swarmgraph) and 57% (sgfixed) in July. The first board was also statistically invalid:
the top lead had 742 "cases" that were 25 distinct follow-up events from 7 actors on 4 days (each follow-up counted
16.5 times), pairs were ranked by effect among ~1000 candidates (winner's curse), and chance was never shown.
Repaired (**fixed**): a burst counts once, every follow-up once, at least 4 actors and 3 days, same sign in both halves,
cluster bootstrap bound, ranked by the bound, the same table under shuffled labels shown next to it, a second list that
links A and B by a shared artifact. The example pairs under the top lead were still a broken document and the check
of a different site, and the board counted differently from `test_claim` (above), so a lead could not be re-tested as
shown. That is not enough: the repaired board's hypotheses still fall 57% -> 7%, and a
July -> August check without any analyst gives rank correlation 0.19 between the July and the August effect of a theme
pair (0.64 for the 17 generic tags, which are stable but small, 100% in the right direction, none above the bar).
Cause: a theme carries a topic, and July's projects are not August's. Status: **open**. Design: leads on move-level
labels, themes only to describe; a lead is shown only if it also holds in two or more disjoint time blocks of the
analyst's own window; say plainly on the board that a lead is a place to look, with the replication table beside it.

**P5. Orientation costs context and is read once.** sgfixed needs 67k input tokens per command against 42k for
rawtool; the first outputs (guide, overview, `theme_map`) are carried through every later turn. All 12 sgfixed analysts
opened `theme_map` once and then used the board 8 more times in total. The overview described the whole history
(2025-04 to 2026-09, 661 actors where 32 were active in the window, roles from each actor's latest goal) and `theme`
showed the earliest 10 events of a theme spanning 20–26 days. Status: window-scoped metadata and spread examples **fixed**; the
size of the orientation is **open**: one page, no guide to read first, measured again for tokens and minutes. What it
weighed: `SKILL.md` 13k characters opening with data conversion and an unused ledger, `theme_map` grown to 27k, one batch
of `test_claim` calls 37k.

**P6. Summaries lose the analyst's vocabulary.** Distinctive terms of a statement are found in the cited events' raw
text 30–31% of the time and in their note summaries 15–18%; numbers survive 40%; `continues` is set on 89% of events and
separates nothing; a note is 90 characters on average (25% of the raw event). The same summaries make *matching* better
(testable hypotheses 60% -> 96%). Stance (**mechanism**): notes are filters and match text, never reading material; show
raw snippets beside any label. Not tested as a separate arm.

**P7. Intentions counted as behaviour.** 42% of AI Village events are session-start intentions; the largest themes are
93–94% such records; the analysts cited 528 events, all messages. Status: **fixed** by the `lead_kinds` dataset field
(leads are built from chat messages only); each new adapter must set it.

**P8. Links by time only.** A broken document and the check of a different page were one "sequence". Status:
**mechanism**: a same-artifact list (issue/PR numbers, host and path, file names) replicates far better (sign 100% in the
top 50, rank correlation 0.44–0.46 both ways) but covers 39% of messages; a real task/artifact entity layer is **open**.

### The interface and the process

**P9. The interface does not match how the analyst works.** 23 of 38 tools were never called; 71 commands opened our
database directly; every tool-arm analyst read `claims.py` before trusting it and 12 of 12 rawtool runs imported the
package in Python. Status: **mechanism**: a small library (`Log`, `grep`, `context`, `test_claim`) whose output carries
its own `bar`, `reading` and `pattern_problems`. The unused 23 should leave the analyst's surface; one analyst opened
the SQLite file to read its schema: they want a database they can query and a library, not 38 JSON tools.

**P10. Nothing calibrates the measurement.** Seven defects were found only by external controls: three session confounds
(agents work a few hours a day, so off-windows hours away are silent and any pair looks linked: in the grader, in
`theme_map`, in `metrics.reaction`), a sign test that cannot decide one-shot labels, unpaired cases biasing the rate,
patterns too narrow, and set-order nondeterminism (2 of 120 verdicts changed between two runs of the same input).
Status: **fixed** each with a regression test that fails on the old code; the mismatched-pair control, the
shuffled-label table and the hash-seed test now ship with the tool. Rule: no number goes on a board without its
chance counterpart.

**P12. The verdict can be gamed and has no guards.** `holds` needs 1.5x the baseline, so 76% vs 53% (bound +0.18)
reads `not_holds` while a narrower pattern at 68% vs 38% passes; analysts shortened windows to 5–30 minutes, narrowed
patterns and put the same words on both sides until the label flipped. `pattern_problems` warns only about patterns
that are too narrow or too broad: not A = B (one run used the identical pattern for both), not concentration (one test
passed on 128 cases whose A-events fell 67% on one day and 82% on one actor; `grep` shows it, `test_claim` does not), not
event kinds (session intentions and memory snapshots count). A positive effect below the bar is printed `not_holds`.
Status: **open**, cheap: warn or refuse when A and B overlap, show concentration in the verdict, a `kinds` filter
(messages by default), words like "positive, below the bar", one counting rule shared with the board.

**P13. A clue class the analysts needed is not computed: corrections, retractions, claims later contradicted.** The
most behaviour-relevant events the analysts cited were of this kind (examples in §3b, plus several agents correcting the
same wrong date). They were found by luck, with regular expressions the analysts wrote. The notes already tag
`claim_success` (12k events) and keep the claims. A plain text rule (`eval/probe_corrections.py`) matches 68 of 21,406
July messages (0.3%, 20 actors); of 22 read at random, 20 were corrections or retractions of something said earlier
(one a code change called a correction, one a request that someone else retract). Status: **open**, with a feasibility
signal: a ledger of corrections, each linked to the claim it corrects and to the actors that updated afterwards, is a
clue the code can compute and a raw analyst cannot cheaply find. Recall and value to analysts are unmeasured.

**P14. The board hid what it had found: ordered by size.** See section 0. Status: **fixed** (interest rating, paging,
the board says when it shows a sample); effect on analysts under measurement.

### The ceiling

**P11. The task and the data cap the effect.** Median after-then effect on unseen data is +0.026 (raw) to +0.053
(swarmgraph) in absolute rate; mismatched pairs pass 7–11%, so raw's 13% is barely above chance; 90% of the analysts'
hypotheses are compound claims and are tested on their core only; AI Village is benign (the one misalignment test, the
wiki logs, was solved blind by both conditions, so there the structure's value is speed, not capability). Tool-checked
claims keep about two thirds of their in-sample rate on the next month; nobody should expect more. Status:
**ceiling**; report effect sizes with their intervals, never a bare "holds".

## 5. What would make the structure pay (in order of expected return)

1. **Make the checker honest and cheap** (P12, P3, P4's two ways of counting): guards for A/B overlap and concentration,
   messages only by default, verdicts in words, one counting rule shared with the board, a ledger of looks, compact
   output. Unit-testable; no analyst experiment is needed to know it works.
2. **Compute the clues the analysts found by luck** (P13): corrections and retractions linked to what they correct and to
   who updated afterwards; a claim followed by its contradiction; the same wrong belief appearing in several agents.
   Judged by precision on a labelled sample and by whether analysts' hypotheses use them.
3. **Deliver text and use labels as filters** (P6, P4): verbatim samples spread over actors and days with long fiction and
   boilerplate cut, `members(label, filters)` returning text and the events that follow, a documented schema to query,
   one page of orientation.
4. **Let the labels feed the claim language** (P2): tags, themes and addressees usable as A/B predicates, per-agent and
   multi-step shapes.
5. **Score by holds that are neither obvious nor echoes** (two judge passes, `echo_check.py`) and judge each clue class by
   its own precision. A 12-run hold-rate comparison cannot see a 10-point gain: 13% vs 23% needs about 230 hypotheses
   per condition for 80% power.

## 6. What this means for the hackathon

- Ship the claim tools only after the guards of P12, and the honest board as what it is: a way to see where behaviour
  concentrates. Do not present theme-pair leads as findings.
- The strongest verified result is still the blind finding of misuse in the wiki logs and the traceable
  evidence behind each flag; the hypothesis work is a method for checking what analysts (human or model) claim.
- State the limits openly: effects on unseen data are small, a hold rate is not an insight rate, and the structure's
  benefit over a good shell plus the claim tools is not shown.

## 7. Not known

- Whether the structure helps where the log is too big to grep, or the analyst is weaker or time-boxed.
- Whether P2–P4 as designed recover depth without losing the checkability (never run).
- How the hold and insight numbers move on another dataset; everything above is one dataset, one analyst model, 12 runs
  per condition.
- Whether people (not models) find the people-facing view faster; not tested.
- Whether a corrections ledger changes what analysts find (only the probe exists), and its recall.
- Whether the claim tools help once echoes are excluded: 31 and 37 hypotheses per cell cannot tell 26% from 18%.
