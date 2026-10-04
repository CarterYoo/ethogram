#!/bin/sh
# Rebuild a dataset from its raw source with every LLM stage run again (Codex). Takes one to two hours per dataset;
# model output differs from run to run, so expect the same structure and similar numbers, not identical text.
# Results go to a new folder and never overwrite the indexes in the data folder.
#
#   scripts/reproduce.sh wiki|wiki_all|aiv-julaug [OUT_DIR]
#
# Raw sources (see docs/REPRODUCE.md):
#   $SWARMGRAPH_DATA/collusion-download   https://collusion.wiki/explorer/download (all files, checked against SHA256SUMS)
#   $SWARMGRAPH_DATA/ai-village-raw       huggingface.co/datasets/aidigestorg/ai-village (non-screenshot files)
# Environment: SWARMGRAPH_DATA (default ../data), SWARMGRAPH_WORKERS (12), SWARMGRAPH_MODEL (the Codex model;
# default: the one the user's Codex config names), MAPS_PY (a Python with requirements-maps.txt; default .venv/bin/python)
set -eu
cd "$(dirname "$0")/.."
NAME="${1:?usage: scripts/reproduce.sh wiki|wiki_all|aiv-julaug [OUT_DIR]}"
DATA="${SWARMGRAPH_DATA:-../data}"
OUT="${2:-$DATA/repro}"
MAPS_PY="${MAPS_PY:-.venv/bin/python}"
W="${SWARMGRAPH_WORKERS:-12}"
RAW="$DATA/collusion-download"
mkdir -p "$OUT"

checksums() {  # the published checksums of the files that are present
  (cd "$RAW" && grep -E ' (pages|revisions|events|labels|records|links|other-wikis|shortener-logs|site-coverage|coverage-gaps)\.' SHA256SUMS \
     | while read -r sum file; do [ -f "$file" ] && echo "$sum  $file"; done | shasum -a 256 -c -)
}

case "$NAME" in
  wiki)
    checksums
    python3 adapters/wiki_logs.py "$RAW" "$OUT/wiki" ;;
  wiki_all)
    checksums
    [ -f "$OUT/wiki/events.jsonl" ] || python3 adapters/wiki_logs.py "$RAW" "$OUT/wiki"
    python3 adapters/other_boards.py "$RAW" "$OUT/wiki" "$OUT/wiki_all" ;;
  aiv-julaug)
    python3 adapters/ai_village.py "$DATA/ai-village-raw" "$OUT/aiv-full"
    python3 scripts/window.py "$OUT/aiv-full" "$OUT/aiv-julaug" 2026-07-01 2026-09-01 ;;
  *) echo "unknown dataset: $NAME" >&2; exit 2 ;;
esac

DB="$OUT/$NAME.sqlite"
python3 -m swarmgraph --db "$DB" prepare "$OUT/$NAME" --workers "$W"     # index, delegated reading, storyline
"$MAPS_PY" -m swarmgraph --db "$DB" features all "$OUT/${NAME}_features" --workers "$W"   # behaviours, judges, checks, names
python3 -m swarmgraph --db "$DB" arc --effort high                         # phases, turning points, hypotheses, review
echo "done: $DB (serve with: python3 -m swarmgraph --db $DB serve --home /features)"
