# Step 15: Final test (one time only, time-boxed)

Do not change any code or config. First confirm with `git diff` that `training/configs/acceptance.yaml` has no uncommitted changes; if it has any, stop with status "fail".

Run `training/scripts/10_evaluate.py` on the test split with --final, using its --scenarios option on a stratified subset of **24 test scenarios**:

- 6 test transmitters, chosen to cover as many terrain strata as possible (deterministic choice, seed 42), x all 4 heights.
- Record the chosen scenario ids and the selection rule.
- Never pick transmitters by looking at any results.

Evaluate these predictors:

- the deterministic labeler
- the distance baseline
- the LightGBM model
- the U-Net, if it exists

Include the end-to-end runtime measurement that the min_speedup criterion needs.

Write `reports/eval/final/summary.md`:

- State that this is a time-boxed final test on 24 of the 80 test scenarios, with the selection rule.
- PASS or FAIL for each acceptance criterion, for each predictor.
- The shipping decision: ship the learned model only if it passes every criterion; otherwise ship the deterministic map. Give the reasons.

Also write the decision to `reports/eval/final/decision.json` as `{"ship": "model" or "deterministic", "model": name or null, "scenarios_evaluated": 24, "reasons": [...]}`.

## Gates

1. The final evaluation completed on the 24 scenarios for every available predictor.
2. `decision.json` is written.

The status is "pass" if the evaluation ran, whatever the model's result.

Status file: `reports/pipeline/step_15.json`.
