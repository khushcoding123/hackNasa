# Step 17: Demo bundle and final report

1. Build an offline Haworth demo bundle (cached terrain and results) so the demo runs without downloads. Document how to start it.
2. Demo flow: start at Haworth, place a mast, show a ridge-caused dead zone with its obstruction profile, then raise or move the mast and show the boundary change. Write the steps to `docs/DEMO.md`.
3. Write `training/MODEL_CARD.md` covering:
   - data sources and licenses
   - the label generator and its assumptions (simulated)
   - splits and metrics, with PASS/FAIL against acceptance.yaml
   - runtime
   - the Experiment A and benchmark findings
   - limitations and out-of-domain behavior
   - the final decision from `reports/eval/final/decision.json`
4. Go through every acceptance checkbox in section 5 of MOONMESH_TERRAIN_COVERAGE_TRAINING.md and report pass/fail, with evidence file paths, in `reports/acceptance_checklist.md`.

## Gates

1. MODEL_CARD.md, DEMO.md and the acceptance checklist exist.
2. The demo bundle loads offline. Test it with network access unused.

Status file: `reports/pipeline/step_17.json`.
