#!/bin/bash
# The grading half of after_flow.sh, resumable (formalized tests are cached in hold_flow/tests.json).
cd "$(dirname "$0")/.." || exit 1
D=../data
echo "[$(date +%T)] graded over event text, August only"
python3 eval/hyp_test.py run $D/hold_flow feat=$D/ab_flow:sgfeat flow=$D/ab_flow:sgflow \
  --big $D/aiv-julaug_flowtrain.sqlite --test-big $D/aiv-julaug.sqlite --ids-big $D/aiv-julaug_oos_test_ids.txt
echo "[$(date +%T)] graded over behaviours, August only"
python3 eval/flow_grade.py run $D/flowgrade feat=$D/ab_flow:sgfeat flow=$D/ab_flow:sgflow \
  $D/aiv-julaug.sqlite 2026-08-01
echo "[$(date +%T)] ALL DONE"
