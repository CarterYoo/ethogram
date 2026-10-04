#!/bin/bash
# The flow round (RESULTS section 17): analysts see July only (with a July behaviour atlas), half of them with the flow
# tools; their hypotheses are graded on August only, over event text (hyp_test.py) and over behaviours (flow_grade.py).
cd "$(dirname "$0")/.." || exit 1
D=../data
echo "[$(date +%T)] analysts"
python3 eval/analyst_ab.py run eval/ab_config_flow.json $D/ab_flow --reps 12 --workers 6 --conds sgfeat,sgflow
echo "[$(date +%T)] graded over event text, August only"
python3 eval/hyp_test.py run $D/hold_flow feat=$D/ab_flow:sgfeat flow=$D/ab_flow:sgflow \
  --big $D/aiv-julaug_flowtrain.sqlite --test-big $D/aiv-julaug.sqlite --ids-big $D/aiv-julaug_oos_test_ids.txt
echo "[$(date +%T)] graded over behaviours, August only"
python3 eval/flow_grade.py run $D/flowgrade feat=$D/ab_flow:sgfeat flow=$D/ab_flow:sgflow \
  $D/aiv-julaug.sqlite 2026-08-01
echo "[$(date +%T)] ALL DONE"
