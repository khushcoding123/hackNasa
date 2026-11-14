# Step 10: Train the first model (LightGBM)

Write `training/scripts/09_train_tabular.py` and run it.

1. Baselines, evaluated on val:
   - Distance-only: LOS if distance <= sqrt(2R·h_tx) + sqrt(2R·h_rx). Covered if that holds and the free-space margin >= the required margin.
   - The deterministic labeler itself, as the reference, with its runtime per scenario.
2. LightGBM models:
   - Three models: an LOS classifier, a min_clearance regressor (Huber) and a link_margin regressor (Huber).
   - Use only the feature columns from step 09. Assert that no forbidden column (per AGENTS.md), split column or sample weight is among the inputs.
   - num_leaves 63, learning_rate 0.05, up to 2000 rounds, early stopping on val.
   - Use class weights if LOS is imbalanced. Use the boundary weights in training only.
3. Coverage = predicted margin >= required_margin. Never touch test.
4. Save the models to `models/tabular/`, with the feature list, config hash and a feature-importance plot. Warn if distance alone dominates.
5. Write `reports/models/tabular_val.md`. Report on the val uniform sample, for each baseline and model:
   - LOS accuracy and F1
   - dead-zone precision and recall, and the false-covered rate
   - clearance MAE
   - margin MAE, bias, and MAE within 3 dB of the threshold

## Gates

1. All three models are saved, and the forbidden-column assertion ran.
2. The val metrics table exists, covering both baselines and the models.

Add a warning, not a failure, if LightGBM does not beat the distance-only baseline on LOS F1 and margin MAE.

Status file: `reports/pipeline/step_10.json`.
