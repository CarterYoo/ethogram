#!/bin/sh
# Make a self-contained deploy bundle: the committed code, consistent copies of the served indexes and their work
# stores, the serve script and a Dockerfile. Nothing else from the data folder is included.
#
#   scripts/bundle.sh [OUT_DIR]          (default dist/swarmscope-deploy)
#
# Then either run it on any machine with Python 3.9+:   sh OUT_DIR/serve.sh            (http://localhost:8080)
# or build a container:                                  docker build -t swarmscope OUT_DIR && docker run -p 8080:8080 swarmscope
set -eu
cd "$(dirname "$0")/.."
OUT="${1:-dist/swarmscope-deploy}"
DATA="${SWARMGRAPH_DATA:-../data}"
if [ -n "$(git status --porcelain -- swarmgraph deploy)" ]; then
  echo "uncommitted changes in swarmgraph/ or deploy/: commit first (the bundle takes the committed code)" >&2; exit 1
fi
rm -rf "$OUT"
mkdir -p "$OUT/app" "$OUT/data"
git archive HEAD swarmgraph adapters scripts README.md requirements-maps.txt | tar -x -C "$OUT/app"
cp deploy/serve.sh deploy/Dockerfile "$OUT/"
python3 - "$DATA" "$OUT/data" <<'EOF'
import os, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
for name in ("wiki_all", "aiv-julaug"):  # the served datasets
    for suffix in (".sqlite", ".sqlite.work"):
        a, b = sqlite3.connect(os.path.join(src, name + suffix)), sqlite3.connect(os.path.join(dst, name + suffix))
        a.backup(b)  # consistent, write-ahead log included
        b.execute("PRAGMA journal_mode=DELETE")  # no -wal/-shm files needed beside it
        b.execute("VACUUM")
        a.close(); b.close()
        print(f"  {name + suffix}: {os.path.getsize(os.path.join(dst, name + suffix)) / 1e6:.0f} MB")
for f in os.listdir(dst):  # left by the copy while the source's write-ahead mode was in effect
    if f.endswith(("-shm", "-wal")):
        os.remove(os.path.join(dst, f))
EOF
{
  echo "SwarmScope deploy bundle"
  echo "commit: $(git rev-parse HEAD)"
  echo "made:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  (cd "$OUT" && find data -type f | sort | xargs shasum -a 256)
} > "$OUT/MANIFEST.txt"
echo "bundle: $OUT ($(du -sh "$OUT" | cut -f1))"
