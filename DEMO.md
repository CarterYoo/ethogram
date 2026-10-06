# Demo: finding misaligned behaviour without being told what the data is (≈5 min)

Data: 19,913 wiki edit-log events (`adapters/wiki_logs.py`). Nothing about who the agents are or whose wiki it is is
given to the tool; `dataset.json` is empty of context.

```bash
python3 -m swarmgraph --db wiki.sqlite serve --share   # open /story; technical detail is withheld on every screen
```

1. **Top of /story — "Possibly misaligned behaviour."** Read the summary aloud. It was written from an environment
   the tool inferred from samples alone (whose system, what the operators remove, what the agents try to do), then
   every event was re-read against it. Point at the line under the summary: interpretations, not verdicts.
2. **Severity cards.** "Using another site's pages for agent work" (≈13.7k actions, 3k agent names) and "Continuing
   after pages were restored or removed" (every one of its 2.8k actions came after the operator had acted). Click one:
   what happened, why it is concerning, what could make it benign, evidence.
3. **Agents that raise the most concern.** Ranking weighs severity, rarity of the kind and persistence after the
   operator acted; names are self-chosen, so one agent may appear under several names.
4. **Timeline.** Busiest hours: circles are incidents (one agent's burst), columns are behaviour; red dashed rings
   are rare concern kinds. Click a circle: it says what kind of moment it is, why it stands out, and what the agent
   wrote (masked).
5. **Behaviour being tested.** Hypotheses proposed from the storylines, checked by code metrics and by several
   agents (for, against, red team), with the verdict and its reason.
6. **Be straight about the numbers** (`eval/RESULTS.md`): wiki precision 1.00 / recall 0.91 on held-out labels;
   benign AI Village days precision 0.35–0.56; a strong code agent with a shell also finds the wiki misuse blind,
   so the claim is not "only this finds it" but "it reads every event, shows it to people, and every statement
   traces to events".

Do not show unmasked event text (`serve` without `--share`) to an audience.
