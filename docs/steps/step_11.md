# Step 11: Evaluate on full maps (validation split only, time-boxed)

Write `training/scripts/10_evaluate.py`. It evaluates any predictor (deterministic, distance baseline, LightGBM, and later U-Net) on full maps.

- Use --split val by default. Allow --split test only together with --final. In this step, run it on val only.
- Add a --scenarios option. In this step, evaluate a stratified subset of 24 val scenarios: 6 val transmitters, chosen to cover as many terrain strata as possible, x all 4 heights. Record the chosen scenario ids in the report. The --final test run in step 15 evaluates every test scenario.
- Predict full rasters per scenario. For LightGBM, compute features for every valid pixel in range.
- Metrics, reported separately for terrain dead zones and RF dead zones:
  - precision and recall, per-class IoU
  - area error km² and false-covered km²
  - boundary distance mean and p95 in meters (distance transform)
  - margin MAE, bias, and near-threshold MAE
  - unknown fraction

  Compute pixel areas from the CRS, including the polar stereographic scale factor.
- Stratify by range bin (0-2, 2-4, 4-6, 6-8 km), h_tx, stratum, and distance-to-boundary bin. PGDA quality layers are not available, so skip that stratum and say so.
- Runtime on CPU, end to end (raster read, features, inference, post-processing), against the deterministic labeler (fast mode) on identical windows. Time 5 scenarios warm, plus one cold run. Report ms per scenario and the speedup.
- Cross-source check: regenerate labels for 5 val scenarios on the PGDA 78 5 m terrain, where it covers the scenario, and report the fraction of LOS pixels that flip compared with the 20 m labels. This is the label-uncertainty floor.
- Figures: TP/FP/FN/TN maps over the hillshade for 4 representative scenarios, and one traced contested path.
- Create `training/configs/acceptance.yaml` with false_covered_frac <= 0.01, boundary_p95_m <= 60, near_threshold_margin_mae_db <= 2 and min_speedup >= 10. Report PASS/FAIL for each criterion on val.
- Write raw CSVs, metrics.json and summary.md to `reports/eval/<run_id>/`.

U-Net decision: do not install torch in this step. Write `reports/eval/unet_decision.json` with `{"recommended": true/false, "reasons": [...]}`. Recommend the U-Net only if LightGBM fails the boundary_p95_m or false_covered_frac criterion on val. Note that the U-Net is deferred in this time-boxed run.

## Gates

1. Val metrics exist for the deterministic labeler, the distance baseline and LightGBM on the 24-scenario subset.
2. The runtime table and the cross-source flip fraction are reported.
3. acceptance.yaml and unet_decision.json are written.

Status file: `reports/pipeline/step_11.json`.
