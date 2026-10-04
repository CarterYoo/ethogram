# Flow: how behaviour moves, as numbers an agent can use

Status: built (`swarmgraph/flow.py`, tools `flow_overview`, `flow_shift`, `flow_feature`, `flow_cascades`,
`flow_coupling`, `flow_test`, ledger metrics `flow_transmission`, `flow_coupling`, `flow_shift`; tests
`tests/test_flow.py`), with the arc written by an analyst that uses these tools (`arc --agent`). Who or what changes
whom is measured by the influence model (`swarmgraph/influence.py`, tool `flow_influence`; docs/HARNESS.md section
10): the spread ratios below compare connected stretches with connected stretches and do not separate influence
from stretches in contact being alike.

## 1. Why

A person watching the behaviour atlas play sees three things: which behaviours are on, when the mix changes, and which
behaviour travels from whom to whom (the lines). An agent cannot watch it. Until now an agent writing the storyline or
the arc got a digest of the readers' findings per period, cut to fit one call. On AI Village (July-August), one goal
period holds 297 of the 313 chunks: its digest had 4,392 lines of findings and the writer received 238 of them (5%),
none of the lines on what was done, contradicted or broken. The record had been read in full; the summary of it was
not.

The flow layer gives the agent what the person sees, defined on the behaviour features and computed by code over the
whole record, so that it asks for the flow instead of reading summaries: what defined each stretch of time, what
changed at each boundary, what spread along which kind of contact, and which behaviour followed which. Numbers come
with intervals, the edges they rest on, and the events to open.

## 2. Objects

| object | definition |
|---|---|
| unit `u` | a stretch of work: one actor's events in a row (no pause over 15 minutes, at most an hour); start `t(u)`, actor `a(u)`, place `c(u)` |
| activation `x[u][f]` | share of the unit's events the judges marked with behaviour `f` (judged units only); `f` is present when `x > 0` |
| uniform units | judged units drawn at random from all stretches; every rate uses only these as targets (flagged units drawn on purpose are not) |
| edge `v → u` | `v` starts no later than `u`, different actors, of five kinds: |
| `reuse` | `u` reuses word sequences `v` wrote first (shared shingles; code) |
| `reply` | an event in `u` answers an event in `v` (reply fields and reply windows of the index) |
| `address` | `v` mentions `u`'s actor, and `u` is that actor's next stretch within a day |
| `channel` | `v` is one of the last 3 stretches by others in `u`'s place in the hour before `u`: what `u` could have seen there |
| `next` | `u` is the same actor's next judged stretch within a day: persistence and sequence within an actor, not spread |

## 3. Measures

**State.** `p_f(t)` = share of uniform units in bin `t` (day, or week for records over 45 days) that show `f`, with a
Wilson 95% interval; first seen, peak, last seen; actors showing it for the first time per bin.

**Regimes.** Each uniform unit is a 0/1 vector over behaviours. Time is split at day boundaries into `K` segments
that minimise the within-segment squared error `Σ_segments Σ_u ||b_u − μ_segment||²` (for 0/1 vectors this is
`Σ_f c_f (1 − c_f / n)` per segment, so exact dynamic programming over all splits is cheap). `K` is chosen by split-half
prediction: the units are halved at random (by a hash of the id), boundaries and segment means are fitted on one half
and scored on the other, both ways; the simplest `K` within 0.1% of the lowest held-out error wins (at least 3% of
the units, and 25, per segment). Reported: the held-out error each `K` removes (`heldout_explained`), each regime's
defining behaviours (lift = share in the regime ÷ share overall, with a two-proportion z), and at each boundary the
change of mix (`1 − cos` of the two regimes' share vectors), what rose and fell, and whether both halves put a
boundary within 3 days of it.

**Transmission** of `f` along edge kind `k`. Targets: uniform units with at least one judged source along `k`.
Exposed: a source shows `f`. Risk ratio `P(f in u | exposed) ÷ P(f in u | not exposed)`, Mantel-Haenszel over the
ISO week of the target, so a rise shared by everyone that week cancels; Greenland-Robins 95% interval; a stratum with
an empty cell gets half a case and half a non-case per group (Haldane-Anscombe). Also observed ÷ expected, the
expectation being the target week's base rate of `f`. Conditioning on targets that have a source compares connected
units with connected units.

**Adoption** of `f`. Targets: uniform units whose actor showed `f` in no earlier judged unit. Exposed: a source along
any exposure kind shows `f` (no judged source counts as not exposed). The same risk ratio by weeks.

**Coupling** `A → B`: the risk ratio of `B` in the target when a source shows `A` versus not, by weeks, over one edge
kind or all. With `next`, within an actor (A, then B in its next stretch); with `channel` and a removal behaviour as
`A`, what follows removals in that place.

**Cascades** of `f`: groups of units showing `f` joined by edges, with the order in which actors appear, the depth of
the longest chain, the time span and the members' events.

**Held-out tests.** A claim (transmission, adoption, coupling, or a shift: `f` more common in a window) is measured on
a discovery part and a test part: split by time (find before a date, test from it on) or by actors (two halves by a
hash of the name). The verdict uses the test part only: `holds` when the interval is above 1 (or `z ≥ 1.96` in the
claimed direction for a shift), `contradicted` when it is below, `undecided` otherwise, `too few cases` under 5
exposed cases. The same measures are ledger metrics, so a claim can be registered before it is looked at.

## 4. Tools

| tool | what a person would do on the page | returns |
|---|---|---|
| `flow_overview` | watch the whole record play | regimes and boundaries, what spreads most (overall and per edge kind), what persists within actors, the strongest couplings, coverage of each edge kind |
| `flow_shift` | replay one stretch of time | behaviours higher and lower in a window than elsewhere (or in the active days after a date than before it), actors new to it, the chunk ids to read |
| `flow_feature` | click a behaviour | its course with intervals, transmission per edge kind, adoption, who passed it on, its largest cascades |
| `flow_cascades` | follow the lines | who passed a behaviour to whom, when, with events |
| `flow_coupling` | (not on the page) | which behaviour follows which |
| `flow_test` | (not on the page) | a claim measured on a part of the record not used to find it |

Every answer carries its counts; `flow_overview` explains the measures in one paragraph (`how`) and says what to call
next.

## 5. How an agent uses it

1. `flow_overview`: the regimes give a skeleton of the record measured by code, not written by a model; spread and
   couplings give candidate mechanisms.
2. For a boundary: `flow_shift {"at": date}` says what changed and which chunks cover it; `chunk` and `delegate` give
   the reasons the readers found there.
3. For a behaviour: `flow_feature`, `flow_cascades` for who started it and how far it went; open the events.
4. Write the hypothesis in flow terms ("from June 16, predicting values or rounds not yet seen spread along replies
   (rr 5.4) and the same pages (rr 7.4), and was followed by declarations of watching the page continuously (rr 22)")
   with the alternative.
5. Test it where it was not found: `flow_test` with a time or actor split, or register a `flow_*` metric with
   `propose_hypothesis` on the later part before measuring.

What the agent reads grows with the number of questions it asks, not with the size of the record; nothing is cut to
fit, because code aggregates the whole record and the agent pulls the parts it needs.

## 6. Checks

Synthetic records (`tests/test_flow.py`):

- a behaviour whose rate rises for everyone over eight weeks, with edges playing no part: not spread (the interval
  contains 1); the same counts pooled without weeks would call it spread (lower bound above 1, in all three seeds
  tried);
- a behaviour passed along edges (60% when a source shows it, 10% otherwise): spread, lower bound above 2, and
  `flow_test` with a time split says `holds`; a behaviour that does not travel does not;
- a change of mix on day 15 of 30: two regimes, boundary within a day of it; no change: one regime;
- a cascade along a chain of four actors comes back as one cascade of depth 4 in that order;
- 9 of 9 exposed against 96% of the rest: the interval contains 1 (it did not before the empty-cell correction).

On the two records (code only, about 1-3 seconds per record):

| | wiki and other boards | AI Village, July-August |
|---|---|---|
| uniform targets | 2,139 | 2,000 |
| edges measurable (judged source, uniform target) of all edges: reuse / reply / address / channel / next | 1,463 of 13,195 / 706 of 8,945 / 2 of 39 / 709 of 6,644 / 592 of 620 | 30 of 899 / 127 of 3,676 / 391 of 12,583 / 1,042 of 33,182 / 1,532 of 1,532 |
| regimes, held-out error removed | 7, 14.4% | 3, 0.35% |
| largest boundary changes | June 16 (predicting future outcomes 0 → 55% of stretches), June 18, June 19, June 22 (copying others' text 11% → 71%, removals 12x their overall share after) | July 6, the new goal (mentions of another participant 19% → 49%) |
| spreads most (rr, 95% interval) | outside signalling 19 (12-32), access through intermediaries 17 (11-25), predicting future outcomes 9.9 (7.6-13) | mentions of another participant 3.0 (2.4-3.7), promises of future action 2.4 (1.9-3.0) |
| persists within an actor (rr) | posting similar versions 11, linking a page to itself 13 | explaining pauses after prompting 33, limiting rating and tracking 19 |

On the wiki the regimes match the outside analysis's timeline (answers relayed between runs from June 16, cleanup
after June 22) without being told it; that analysis had been read before (BEHAVIOUR_FEATURES.md section 12), so this
is not a held-out result. Held out by time: predicting future outcomes spreads before June 19 (rr 11.5, 8.4-15.7) and
from June 19 on (rr 4.2, 2.5-7.2): `holds`. On AI Village the overall mix barely changes after the July 6 goal
change, so its account has to come from single behaviours and their spread, which is what the tools say
(`heldout_explained` under 1% comes with that note). Whether its variation lies between agents rather than over time
has not been measured.

## 7. Limits and next steps

- **Associations, not proof.** A risk ratio along edges fits spread net of week-wide trends; actors who connect may
  be alike, and unseen causes may reach both. Behaviour carried inside copied text spreads mechanically along reuse
  edges (copying a link copies "shares outside links"); spread along replies, mentions and the same place is the
  stronger evidence of influence.
- **Coverage.** Only judged units carry behaviours: before the flow sample, 11% of the wiki's reuse edges and 3% of
  AI Village's could be measured. The flow sample (the sources of sampled stretches, judged) raised the wiki's to 72%
  and AI Village's edges to 44-66% by kind; the encoder (HARNESS.md section 7.9) fills in sources nobody judged for
  the behaviours it reproduces at kappa >= 0.6, as sources only.
- **Labels are not agents** on the wiki (self-chosen author labels): adoption per actor there is per label.
- **Done since**: the arc written by an analyst with these tools (HARNESS.md section 11); regime boundaries and the
  influence view on the atlas page; the July / August round with and without the tools (RESULTS section 17: not
  shown over event text, a lead at the behaviour level).
