#!/bin/bash
# Round 4 (RESULTS section 12), kept small (development first): finish the prepared reading on the transcript, then a
# sanity check that analysts can use it, against the raw analysts of earlier rounds.
#   bash eval/round4.sh [WAIT_PID]     run from the package root; logs to ../data/round4.log
# 1. rules (if not merged yet), 2. threads + traced statements in parallel, 3. three analysts with the full harness,
# 4. a blind pairwise judge against three raw reports, and the target report (code).
set -u
DB=../data/aiv-transcript.sqlite
CFG=eval/ab_config_transcript.json
OUT=../data/ab_tr
LOG=../data/round4.log
exec >>"$LOG" 2>&1
echo "== $(date) round 4 (small)"
if [ -n "${1:-}" ]; then
  while kill -0 "$1" 2>/dev/null; do sleep 15; done
  echo "== $(date) process $1 ended"
fi
python3 -u -c "from swarmgraph import sweep as S; print(S.rules('$DB', workers=9))"
python3 -u -c "from swarmgraph import sweep as S; print(S.threads('$DB', workers=24))" > ../data/round4_threads.log 2>&1 &
T1=$!
python3 -u -c "from swarmgraph import sweep as S; print(S.trace('$DB', workers=16))" > ../data/round4_trace.log 2>&1 &
T2=$!
wait $T1 $T2
tail -1 ../data/round4_threads.log ../data/round4_trace.log
echo "== $(date) prepared reading done"
python3 -u -c "import sys; sys.path.insert(0,'eval'); import analyst_ab as A; A.run('$CFG','$OUT',reps=3,workers=3,conds=('sgdelegate',))"
echo "== $(date) analysts done"
python3 -u eval/pairwise.py $CFG $OUT TR raw sgdelegate --pairs 3 --ev-limit 150
python3 -u eval/delegate_report.py $OUT $DB ../data/aiv-transcript --conds raw,sgaction3,sgdelegate
echo "== $(date) round 4 done"

