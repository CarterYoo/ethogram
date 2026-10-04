# Reproducing the results

Two levels: check that the stored results rebuild exactly (no LLM, about a minute), or run everything again from the
raw data (LLM stages, one to two hours per dataset; model output differs from run to run).

## 1. Check the stored results (deterministic)

```bash
python3 -m unittest discover -s tests                         # 101 tests, standard library only
python3 -m venv .venv && .venv/bin/pip install -r requirements-maps.txt
MAPS_PY=.venv/bin/python python3 scripts/verify.py ../data      # rebuilds each atlas in a temporary copy and compares
```

`verify.py` rebuilds the behaviour atlas of each served dataset from the LLM outputs stored in its feature folder (the
judges' marks) and compares it with the stored atlas: behaviours, judged stretches, the two-judge checks, the layout
(UMAP, seeded) and the time profiles. It also checks that the storyline, the arc and the short names are stored.
Expected output (2026-10-04):

| dataset | behaviours | judged stretches | median two-judge kappa | short names | result |
|---|---|---|---|---|---|
| `wiki_all` | 66 | 2,214 | 0.888 | 66 | OK |
| `aiv-julaug` | 56 | 2,000 | 0.857 | 56 | OK |

Where the stored LLM results live: `../data/<name>.sqlite.work` (the work store: delegated reading of every chunk,
rules, threads, storyline, arc, short names, the atlas) and `../data/<feature folder>/` (dictionary, samples, the batch
files every judge read and what it wrote, the blind detection test).

## 2. Run everything again from the raw data

```bash
scripts/reproduce.sh wiki        # or wiki_all, aiv-julaug; results go to ../data/repro/, nothing is overwritten
```

| dataset | raw source | conversion |
|---|---|---|
| `wiki` | https://collusion.wiki/explorer/download, group 1 (`pages`, `revisions`, `events`, `labels` .jsonl); checked against the published `SHA256SUMS` | `adapters/wiki_logs.py` |
| `wiki_all` | the same download, group 2 (`records.jsonl`, `other-wikis.json`, `shortener-logs.json`; `links.jsonl` and the coverage CSVs are only counted) | `adapters/other_boards.py` on top of `wiki` |
| `aiv-julaug` | huggingface.co/datasets/aidigestorg/ai-village (non-screenshot files) | `adapters/ai_village.py`, then `scripts/window.py … 2026-07-01 2026-09-01` (67,969 events) |

Then, for each: `prepare` (index, chunks, delegated reading, rules, threads, traces, storyline), `features all`
(induction, merge, two calibration judges, more judging, blind detection, atlas, short names) and `arc` (skeleton,
arc analyst, reviewer).

Environment: Python 3.9+ (standard library) for everything except maps; `requirements-maps.txt` (pinned) for the
atlas; the Codex CLI for LLM stages, called through `swarmgraph/llm.py` with none of the user's tools. The stored
results used the model `gpt-6.1-sol` (set `SWARMGRAPH_MODEL` to choose). Each stage sets its own reasoning
effort (for example the judges low, the arc high).

How the stored runs differ from `reproduce.sh`, so a fresh run will not match them line for line:
- `wiki` behaviours: the first reading (induction, merge, judges, detection) was done by Claude agents from the same
  batch files; `features codex` / `features all` read them with Codex.
- `wiki_all` reused the `wiki` dictionary and judgments for the wiki part and judged the new stretches separately
  (`judge_new`), so its checks equal the wiki's.
- The arcs were written twice (`arc_v1` kept); the second run added the question how the agents' goals relate to the
  rules of their setting, and on AI Village the storyline digest kept measured lines for long periods.
