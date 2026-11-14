# Step 09: Build the training dataset

Write `training/scripts/08_build_dataset.py`, then run the full build yourself in resumable chunks.

1. Scenarios: every transmitter in `training/splits.json` x tx heights [5, 10, 20, 50] x rx height 2 x profile moonmesh_2p4_ref.
   - Reuse the step 06 labeling functions and los.py `mode="fast"`.
   - Resumable: skip scenarios whose output and sidecar already exist with the same config hash.
   - A --workers flag. Numba already parallelizes inside one scenario, so keep workers low enough not to oversubscribe the CPU.
   - After the first 5 scenarios, estimate the total time and disk use. If the projected disk use is over 40 GB, cut storage first (for example, drop rasters you can recompute) and record that decision.
2. Write `derived/datasets/haworth_pilot/scenarios.parquet` with the scenario schema from spec section 6, plus split, stratum and deployable.
3. Receiver tables for the tabular model, about 20k receivers per scenario:
   - a uniform sample (sample_kind=uniform)
   - a boundary oversample where |min_clearance| < 5 m or |margin - required| < 3 dB (sample_kind=boundary, with weights)

   Columns: the spec's receiver schema plus these features:
   - distance_m, sin/cos bearing, h_tx, h_rx, freq_ghz
   - tx and rx ground elevation, and their difference
   - slope, relative elevation at 200 m and 1000 m, and roughness at both tx and rx
   - the tx horizon angle toward the receiver (precomputed per transmitter in 360 bins)
   - a coarse 16-point profile: the max terrain elevation angle seen from the tx along the path, and the 16 sampled heights relative to the straight antenna-to-antenna line

   Record the compute time of each feature group, so it can be charged to inference later. Store one Parquet file per split.
4. Exclude invalid receivers from the targets, but count them. Write `reports/dataset/report.md` with, per split: unique transmitters, blocks, valid px, blocked px, boundary px, unknown px, and covered prevalence (from the uniform sample only).

## Gates

1. Every planned scenario was built (transmitters x 4). The scenarios.parquet row count matches.
2. Receiver Parquet files exist for train, val and test. A check confirms that no forbidden column (per AGENTS.md) is among the feature columns.
3. The dataset report exists, with per-split counts and prevalence.
4. Each split has both LOS-clear and blocked receivers.

Status file: `reports/pipeline/step_09.json`.
