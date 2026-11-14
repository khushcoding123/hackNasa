# Step 15: Final test (one time only)

Do not change any code or config. First confirm with `git diff` that `training/configs/acceptance.yaml` has no uncommitted changes; if it has any, stop with status "fail".

Run `training/scripts/10_evaluate.py` on the test split with --final for:

- the deterministic labeler
- the distance baseline
- the LightGBM model
- the U-Net, if it exists

Write `reports/eval/final/summary.md`:

- PASS or FAIL for each acceptance criterion, for each predictor
- the shipping decision: ship the learned model only if it passes every criterion; otherwise ship the deterministic map. Give the reasons.

Also write the decision to `reports/eval/final/decision.json` as `{"ship": "model" or "deterministic", "model": name or null, "reasons": [...]}`.

## Gates

1. The final evaluation completed for every available predictor.
2. `decision.json` is written.

The status is "pass" if the evaluation ran, whatever the model's result.

Status file: `reports/pipeline/step_15.json`.
