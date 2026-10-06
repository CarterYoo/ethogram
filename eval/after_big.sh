#!/bin/bash
# Unattended continuation of the large-window experiment: waits for run_big.py to finish, then runs the analysts
# (12 runs per condition) and grades their hypotheses on the held-out month only.
cd "$(dirname "$0")/.." || exit 1
D=../data
until grep -q READY $D/aiv-julaug.big.log 2>/dev/null; do
  if grep -q Traceback $D/aiv-julaug.run.out 2>/dev/null; then echo "pipeline failed"; exit 1; fi
  sleep 60
done
echo "[$(date +%T)] pipeline ready; analysts"
python3 eval/analyst_ab.py run eval/ab_config_big.json $D/ab_big --reps 12 --workers 6
echo "[$(date +%T)] analysts done; hold-test"
python3 eval/hyp_test.py run $D/hold_big raw=$D/ab_big:raw sg=$D/ab_big:swarmgraph \
  --big $D/aiv-julaug_oos_train.sqlite --test-big $D/aiv-julaug.sqlite --ids-big $D/aiv-julaug_oos_test_ids.txt
echo "[$(date +%T)] ALL DONE"
