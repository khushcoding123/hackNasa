# Step 15: Final test (one time only, time-boxed)

First confirm with `git diff` that `training/configs/acceptance.yaml` has no uncommitted changes; if it has any, stop with status "fail". Do not change acceptance.yaml.

The step 11 evaluator refuses `--final` together with `--scenarios` ("Final test evaluates every scenario"). This time-boxed run needs a subset, so make exactly one small code change:

- Add an explicit `--allow-final-subset` flag to `training/scripts/10_evaluate.py` (and the matching parameter in `training/src/moonmesh_train/evaluation.py`). With this flag, `--final` may be combined with `--scenarios`. Without it, the current refusal stays.
- When the flag is used, write `"final_subset": true`, the scenario ids and the selection rule into the run's metrics.json and summary.md.
- Add a pytest that covers both cases: refused without the flag, allowed with it.
- Change nothing else in the code or config.

Then run `10_evaluate.py --split test --final --allow-final-subset --scenarios <ids>` on a stratified subset of **24 test scenarios**:

- Use the seed-42 selection already recorded in `reports/eval/final/` if it exists. Otherwise choose 6 test transmitters that cover as many terrain strata as possible (deterministic, seed 42), x all 4 heights.
- Never pick transmitters by looking at results.

Evaluate the deterministic labeler, the distance baseline and the LightGBM model. Include the end-to-end runtime measurement that the min_speedup criterion needs.

Write `reports/eval/final/summary.md`:

- State that this is a time-boxed final test on 24 of the 80 test scenarios, with the selection rule.
- PASS or FAIL for each acceptance criterion, for each predictor.
- The shipping decision: ship the learned model only if it passes every criterion; otherwise ship the deterministic map. Give the reasons.

Overwrite `reports/eval/final/decision.json` with `{"ship": "model" or "deterministic", "model": name or null, "scenarios_evaluated": 24, "final_subset": true, "reasons": [...]}`.

## Gates

1. The `--allow-final-subset` change and its test pass.
2. The final evaluation completed on the 24 scenarios for all three predictors.
3. `decision.json` is written.

The status is "pass" if the evaluation ran, whatever the model's result.

Status file: `reports/pipeline/step_15.json`.
