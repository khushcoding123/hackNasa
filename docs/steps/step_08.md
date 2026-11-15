# Step 08: Choose transmitter locations and split the data

Write `training/scripts/06_sample_transmitters.py` and `training/scripts/07_make_splits.py`. Use terrain layers only. Never look at coverage labels in this step.

1. Terrain strata, from 1000 m relative elevation and slope: ridge/rim crest, shoulder, steep wall, floor, plain. Put the thresholds in `training/configs/exp_b_haworth_pilot.yaml`.
2. Stratified random sample of 150 transmitters, about 30 per stratum:
   - at least 500 m apart
   - only on the master grid where a full 8 km disk has valid terrain
   - record the stratum and a deployable flag: slope below the app's slope_limit (15 degrees, moonmesh/models.py) and within 8 km of the Haworth research point
3. Splits:
   - Use 5 km spatial blocks, and assign whole blocks to train/val/test (about 70/15/15, seed from the config).
   - Drop training transmitters within buffer_m (default = max_range_m = 8000) of any val or test transmitter. Report how many were dropped, and how much training footprint still overlaps test footprints.
   - All heights, frequencies and augmentations of one transmitter share its split.
4. Write `training/splits.json` containing:
   - the seed, config hash and data hashes
   - transmitter ids per split
   - the planned cross-site splits: train Haworth, test Malapert and Nobile Rim 1, plus both rotations (empty until step 14)
5. Save counts per split (transmitters per stratum, blocks, deployable) and a map of the transmitters colored by split over the hillshade to `reports/splits/`.

Tests: no transmitter appears in two splits; the buffer is respected; the same seed gives the same result.

## Gates

1. The tests pass.
2. At least 120 transmitters remain after the buffer, and each split has at least 10.
3. Every stratum has at least 15 transmitters. If not, add a warning and explain why (for example, too little steep wall terrain).
4. `training/splits.json` and the split map exist.

Status file: `reports/pipeline/step_08.json`.
