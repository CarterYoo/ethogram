#!/bin/sh
# Serve the bundled datasets: the behaviour atlas at /, the other pages beside it. Run from anywhere.
#   PORT               port to listen on (default 8080)
#   SWARMGRAPH_HOST    address to listen on (default 0.0.0.0: reachable from outside the machine or container)
#   SWARMGRAPH_SHARE   1 (default): technical detail withheld and no agent runs can be started; 0: full detail
#   SWARMGRAPH_DATA    folder with the indexes (default: data/ next to this script)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="${SWARMGRAPH_DATA:-$HERE/data}"
SHARE=""
[ "${SWARMGRAPH_SHARE:-1}" = "1" ] && SHARE="--share"
cd "$HERE/app"
exec python3 -m swarmgraph --db "$DATA/wiki_all.sqlite" serve --host "${SWARMGRAPH_HOST:-0.0.0.0}" --port "${PORT:-8080}" \
  --home /features $SHARE --also "aiv-julaug=$DATA/aiv-julaug.sqlite"

