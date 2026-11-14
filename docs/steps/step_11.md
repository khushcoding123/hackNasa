# Step 11: Evaluate on full maps (validation split only)

Write `training/scripts/10_evaluate.py`. It evaluates any predictor (deterministic, distance baseline, LightGBM, and later U-Net) on full maps.

- Use --split val by default. Allow --split test only together with --final. In this step, run it on val only.
- Predict full rasters per scenario. For LightGBM, compute features for every valid pixel in range.
- Metrics, reported separately for terrain dead zones and RF dead zones:
  - precision and recall, per-class IoU
  - area error km² and false-covered km²
  - boundary distance mean and p95 in meters (distance transform)
  - margin MAE, bias, and near-threshold MAE
  - unknown fraction

  Compute pixel areas from the CRS, including the polar stereographic scale factor.
- Stratify by range bin (0-2, 2-4, 4-6, 6-8 km), h_tx, stratum, and distance-to-boundary bin. PGDA quality layers are not available, so skip that stratum and say so.
- Runtime on CPU, cold and warm cache, end to end (raster read, features, inference, post-processing), against the deterministic labeler (fast mode) on identical windows. Report ms per scenario and the speedup.
- Cross-source check: regenerate labels for 10 val scenarios on the PGDA 78 5 m terrain, where it covers the scenario, and report the fraction of LOS pixels that flip compared with the 20 m labels. This is the label-uncertainty floor.
- Figures: TP/FP/FN/TN maps over the hillshade for 4 representative scenarios, and one traced contested path.
- Create `training/configs/acceptance.yaml` with false_covered_frac <= 0.01, boundary_p95_m <= 60, near_threshold_margin_mae_db <= 2 and min_speedup >= 10. Report PASS/FAIL for each criterion on val.
- Write raw CSVs, metrics.json and summary.md to `reports/eval/<run_id>/`.

U-Net decision: write `reports/eval/unet_decision.json` with `{"recommended": true/false, "reasons": [...]}`. Recommend the U-Net only if both of these hold:

- (a) LightGBM fails the boundary_p95_m or false_covered_frac criterion on val
- (b) the deterministic labeler's measured end-to-end time per 8 km scenario is at least 10 times the measured CPU inference time of a randomly initialized U-Net of the planned size (about 5-8M parameters) on one 832 x 832 patch. Measure it with torch on CPU if torch is importable. Otherwise install torch (CPU build) into the training venv just for this measurement.

## Gates

1. Val metrics exist for the deterministic labeler, the distance baseline and LightGBM.
2. The runtime table and the cross-source flip fraction are reported.
3. acceptance.yaml and unet_decision.json are written.

Status file: `reports/pipeline/step_11.json`.
