# MOONMESH coverage model: Codex prompts

Run these in order, one prompt per Codex task. Do the check under each prompt before moving on.
If a check fails, paste the error or report back into Codex and fix it first.

## Before you start

- **The repo is the `hackNasa` folder:** `C:\Users\khush\OneDrive - Lake Washington School District\nasahack\hackNasa`. Always open PowerShell and Codex in that folder, not in `nasahack`.
- **Branch:** the work goes on the `coverage-training` branch (already created). Check with `git branch` and switch with `git checkout coverage-training` if needed.
- **The spec** is `MOONMESH_TERRAIN_COVERAGE_TRAINING.md` at the repo root. Every prompt refers to that path.
- **Data folder outside OneDrive:** `C:\moonmesh-data` with the `MOONMESH_DATA_ROOT` variable pointing to it (already set up). Wherever this file says `data/...`, it means that folder (for example, `data/reports/` is `C:\moonmesh-data\reports\`).
- **Python:** the training code uses its own uv virtual environment at `C:\moonmesh-data\venv` (Python 3.13, like the app). It stays separate from the app's `.moonmesh-data\venv` and outside OneDrive. You create it in Prompt 1's check.
- Commit after each prompt passes (`git add -A` then `git commit -m "prompt N"`), so you can roll back if a later prompt breaks something.
- Codex's sandbox may block network access. The download prompts only have Codex write the scripts; you run them in your own terminal.
- Before Prompt 12, download the four reference PDFs into `docs/references/`:

| Save as | Source |
| --- | --- |
| `docs/references/tall_lunar_towers_20230012634.pdf` | https://ntrs.nasa.gov/citations/20230012634 (use the PDF download on that page) |
| `docs/references/nasa_viewshed_trade_20230014675.pdf` | https://ntrs.nasa.gov/api/citations/20230014675/downloads/20230014675%20Final%20Document%20121423.pdf |
| `docs/references/jpl_rapid_low_cost_path.pdf` | https://www-robotics.jpl.nasa.gov/media/documents/A_Rapid_Low-Cost_Path_to_Lunar_Communication_and_Navigation.pdf |
| `docs/references/nasa_nokia_surface_network_20240003815.pdf` | https://ntrs.nasa.gov/citations/20240003815 (use the PDF download on that page) |

---

## Prompt 0: Read the codebase and set the rules

```text
Read MOONMESH_TERRAIN_COVERAGE_TRAINING.md (repo root) fully, then read README.md, scripts/start.ps1, requirements.txt, moonmesh/models.py, moonmesh/data.py, moonmesh/science.py, moonmesh/network.py, lunar_core/terrain.py, lunar_core/horizon.py, lunar_core/geometry.py, lunar_core/visibility.py and tests/test_moonmesh.py. Do not change any existing code.

1. Create AGENTS.md at the repo root with these standing rules for all future tasks:
   - MOONMESH_TERRAIN_COVERAGE_TRAINING.md (repo root) is the spec. Follow it. docs/CODEX_PROMPTS.md lists the tasks.
   - Python for training work is the uv venv at C:\moonmesh-data\venv (Python 3.13). Run Python and pytest with C:\moonmesh-data\venv\Scripts\python.exe. Install packages with `uv pip install --python C:\moonmesh-data\venv\Scripts\python.exe ...`. Never install training packages into the app's .moonmesh-data\venv.
   - The app's existing tests must keep passing: `python -m unittest discover -s tests -v`. New training tests use pytest under training/tests/.
   - .moonmesh-data/ is the app's own cache (LOLA PDS terrain, SPICE kernels, its venv). Treat it as read-only. Reuse its files instead of downloading them again.
   - Never invent metadata (units, nodata, sentinels, thresholds, radio settings, file names). Inspect the files. If a value isn't found, write null with a TODO and report it.
   - Never modify anything in data/raw/. data/ is gitignored. Never commit rasters, archives or model weights.
   - All data lives under a data root read from the MOONMESH_DATA_ROOT environment variable (default: <repo>/data). Paths written as data/... in docs/CODEX_PROMPTS.md mean paths under that root.
   - This repo runs on Windows, inside OneDrive. Use pathlib for every path. Don't rely on Unix-only tools (sha256sum, tar, curl, sed); use Python's hashlib, tarfile and requests/urllib instead.
   - New code goes in training/. Import from moonmesh/ and lunar_core/, but don't modify them unless the task says so.
   - Every script has an argparse CLI with --help, reads configs from training/configs/, writes reports to data/reports/, uses fixed seeds and logs the git commit.
   - Simulated labels (ours and LunarRF's) are always marked "simulated" in outputs and reports.
   - Model inputs must never include LunarRF output layers (best_prx, best_id, best_los, any counts), IDs, or full-trace LOS outputs (min_clearance, obstruction_*).
   - Never tune anything on the test split.
   - Run pytest after every change. End every task with: files changed, commands run, test results, open questions.
2. Write docs/training_codebase_notes.md summarizing, with function names and line numbers:
   - how surface_path builds the curved-body chord (sample spacing, interpolation, Moon radius, antenna heights)
   - how data.py decodes the PDS grids (byte order, 0.5 m scale, nodata) and which URLs it downloads
   - every radio and geometry default in models.py (frequency, tx power, gains, losses, sensitivity, required margin, antenna heights, deployment radius)
   - the projection and sampling functions in lunar_core/terrain.py
   - the free-space RF function in science.py
   If something isn't in the code, say so.
```

**Check before the next prompt:** read `docs/training_codebase_notes.md` and check that it matches your understanding of the app.

---

## Prompt 1: Set up the training workspace

```text
Following AGENTS.md, set up the training workspace. Don't download any data.
- requirements-training.txt for uv/pip: rasterio, pyproj, numba, pandas, pyarrow, xarray, rioxarray, scikit-learn, lightgbm, scikit-image, matplotlib, pyyaml, pytest, tqdm, pypdf, requests. It must work on Windows with Python 3.13 and stay compatible with requirements.txt (numpy 2.x). Don't list gdal: the rasterio wheel bundles GDAL. No torch yet.
- Folders: training/{configs,scripts,tests,src/moonmesh_train}. Add training/src/moonmesh_train/paths.py with data_root() (reads MOONMESH_DATA_ROOT, default <repo>/data) and helpers that create {raw/{lunarrf,pgda90,pgda78,lola_pds},interim,derived/{terrain,labels,datasets},models,reports} under it on demand. Every script must use these helpers, never a hard-coded data path. Add data/ to .gitignore.
- training/configs/radio_profiles.yaml with two profiles:
  * moonmesh_2p4_ref: every value copied from moonmesh/models.py, with a comment citing the source line.
  * lunarrf_1p8: frequency_ghz 1.8; every other field null with "TODO: fill from LunarRF run metadata".
  Fields: frequency_ghz, tx_power_dbm, tx_gain_dbi, rx_gain_dbi, fixed_loss_db, receiver_sensitivity_dbm, required_margin_db, max_range_m.
- training/configs/exp_a_lunarrf_haworth.yaml and exp_b_haworth_pilot.yaml: site haworth, lat -86.8, lon 337.2 E, grid 20 m, tx heights [5,10,20,50], rx height 2, max_range_m 8000, seed 42, pilot transmitters 150, min spacing 500 m, block size 5000 m.
- training/src/moonmesh_train/config.py: a loader that fails loudly if a run uses a profile with any null field.
- Add a minimal pyproject.toml so `uv pip install -e .` makes moonmesh, lunar_core and moonmesh_train (from training/src) importable. It must not change how the app runs (scripts/start.ps1, `python -m moonmesh.server`) or what requirements.txt installs.
- training/README.md listing the planned pipeline steps.
Add pytest tests for the config loader. If C:\moonmesh-data\venv exists, install requirements-training.txt and the editable package into it with uv, then run `python -m pytest training` and the app's unittest suite with that venv's python. If the venv doesn't exist or the install fails, stop and give me the exact commands to run.
```

**Check before the next prompt:** run these and make sure the tests pass.

```powershell
uv venv --python 3.13 C:\moonmesh-data\venv
C:\moonmesh-data\venv\Scripts\Activate.ps1
uv pip install -r requirements.txt -r requirements-training.txt
uv pip install -e .
python -m pytest training
python -m unittest discover -s tests -v
```

If `Activate.ps1` fails with "running scripts is disabled", run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again.
From now on, every time you open a new PowerShell for this project, `cd` into `hackNasa` and run `C:\moonmesh-data\venv\Scripts\Activate.ps1` before any `python` command.

---

## Prompt 2: Write the download script

```text
Following AGENTS.md, write training/scripts/00_download.py with these subcommands. Do NOT run any large download yourself. I will run them.
- lunarrf-meta: fetch MANIFEST.txt and SHA256SUMS from https://zenodo.org/records/23301640/files/<name>?download=1 into data/raw/lunarrf/.
- lunarrf --parts context 1 [2 3]:
  * context = lunarrf_atlas_context_v2.0.tar; part N = lunarrf_atlas_regions_20m_partN_v2.0.tar.
  * Send a HEAD request and print the size. Require --yes above 2 GB.
  * Download to <name>.part with resume (HTTP Range), then rename.
  * Verify sha256 against SHA256SUMS. If the file isn't listed, warn and record "unverified: compare with the Zenodo record page".
- extract --site Haworth --band 1p800GHz:
  * List the tar contents first.
  * Extract only files containing "<Site>__" together with either the band tag or "los_count".
  * Also extract every metadata-looking file (.txt .json .yaml .yml .csv .md .xml) from the context tar.
  * Output to data/interim/lunarrf/<site>/. Never modify the tar.
- pgda78 --site haworth|malapert|nobile-rim-1: download the 5 m surf.tif URLs from the spec (Haworth, Site23, Site06). Print the folder URL so I can find the companion slope/uncertainty/count files, and accept extra --files names.
- pgda90-window --bounds-from <raster> --buffer-m 10000:
  * Take the raster's bounds plus the buffer, reprojected if the CRSs differ.
  * Read only that window from /vsicurl/https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/LDEM_80S_20MPP_ADJ.TIF.
  * If range reads fail, print the full file size and stop with instructions.
- lola-pds: the app already downloaded LDEM_85S_20M and LDEM_75S_240M (.IMG + .LBL) into .moonmesh-data/terrain/. Record those files in the manifest with their existing paths (don't copy them). Only if they're missing, call the existing download functions in moonmesh/data.py. Don't reimplement them.
Every completed download adds or updates an entry in training/data_manifest.json with:
source_id, url, provider, version, license (CC BY 4.0 for LunarRF, "check catalog" otherwise), sha256, bytes, downloaded_utc, local_path, verified (true/false/unverified).
Write unit tests for the checksum, resume and manifest logic using temp files only (no network). Run pytest.
```

**Then run these yourself, in order:**

```powershell
python training/scripts/00_download.py lunarrf-meta
python training/scripts/00_download.py lunarrf --parts context 1 --yes
python training/scripts/00_download.py extract --site Haworth --band 1p800GHz
python training/scripts/00_download.py pgda78 --site haworth
python training/scripts/00_download.py lola-pds
# find the best_prx file the extract step produced, then pass its full path:
Get-ChildItem "$env:MOONMESH_DATA_ROOT\interim\lunarrf\Haworth" -Recurse -Filter "*best_prx_1p800GHz*"
python training/scripts/00_download.py pgda90-window --bounds-from "<full path printed above>" --buffer-m 10000
```

---

## Prompt 3: Inventory and LunarRF metadata

```text
Following AGENTS.md, write training/scripts/01_inventory.py and run it on everything in data/raw and data/interim.
1. For every raster, report:
   - path, driver, CRS (proj4 and sphere radius), bounds, pixel size, shape, dtype, block size, scale/offset
   - nodata value and nodata count
   - min/max/mean of valid pixels and a 20-bin histogram
   - all tags
   For PDS files, read via the .LBL with GDAL and also parse the scale/offset from the label text.
2. An overlap table: percent of each raster's footprint that overlaps the LunarRF Haworth best_prx raster.
3. Transform the Haworth point (-86.8, 337.2 E) using each raster's own CRS (pyproj CRS(src.crs).geodetic_crs → src.crs). Report whether it falls inside the raster and at which pixel.
4. Read MANIFEST.txt and every extracted metadata file. Write data/reports/lunarrf_findings.md answering each item below, citing the exact file and line, or "NOT FOUND":
   - units of best_prx; nodata/sentinel values
   - the coverage threshold
   - tx power, gains, losses and receiver sensitivity
   - mast and receiver heights
   - the propagation model
   - the terrain DEM used and its vertical reference
   - the maximum link range
   - how best_prx and best_id are aggregated, and over which active set of masts
   - the meaning of best_los, los_count and count
   - where the 11,827-candidate mast catalog is, and its columns
5. Write data/reports/inventory.md (one page) and inventory.json. Flag anything suspicious:
   - histogram spikes that look like sentinel values
   - a CRS radius other than 1737400 m
   - half-pixel offsets between grids
Do not fill any config values yet.
```

**Check before the next prompt:** read `data/reports/lunarrf_findings.md` yourself. It decides how much of Experiment A (Prompt 7) is possible.

---

## Prompt 4: Align the grids and derive terrain layers

```text
Following AGENTS.md and data/reports/inventory.md, write training/scripts/02_align.py and run it for Haworth.
1. Master grid:
   - If the LunarRF Haworth layers are polar stereographic at 20 m, use exactly their pixel lattice, extended to cover the PGDA 90 buffered window.
   - Otherwise use the PGDA 90 grid.
   - Write the choice and the reason to data/derived/terrain/haworth/grid.json.
2. Warp onto the master grid:
   - PGDA 90 DEM: bilinear
   - app PDS 20 m DEM: bilinear
   - PGDA 78 5 m: averaged to 20 m (also keep the native 5 m copy on its own grid)
   - LunarRF layers: only if they aren't already on the master grid; nearest-neighbor for ids, LOS and counts
   Never average dBm directly. Use a helper that converts to mW, averages, then converts back. Store nodata as NaN in float32.
3. Height reference:
   - Convert any radius-based product to height above 1737400 m. Decide from metadata and record the rule.
   - Write difference rasters and stats (median, MAD, p5, p95) for PGDA90 − PDS20 and PGDA90 − PGDA78@20m.
4. Registration: hillshade each DEM and run skimage phase_cross_correlation(upsample_factor=10) against PGDA 90. Report shifts in pixels and meters. Fail loudly if any shift is 0.5 px or more.
5. Primary DEM:
   - Use PGDA 90 for Experiment B.
   - If the findings show LunarRF used a different DEM, use that DEM for Experiment A and note it.
6. Derived layers from each primary DEM, with all neighborhoods defined in meters:
   - slope (degrees), relative elevation at 200 m and 1000 m radius, curvature
   - roughness (std within 100 m), valid mask
   - aligned PGDA quality layers, if present
7. Quick-look PNGs in data/reports/align/:
   - hillshade with the Haworth point marked
   - the difference maps
   - LunarRF best_prx over hillshade
Add tests for the dBm/mW averaging helper and the meters-based kernels. Run pytest and the script, and summarize the results.
```

**Check before the next prompt:** open the PNGs, and in QGIS check that three landmarks (a rim high point, a small crater, a ridge crest) line up across the layers.

---

## Prompt 5: The line-of-sight labeler (the most important prompt)

```text
Following AGENTS.md, implement the deterministic curved-body line-of-sight labeler in training/src/moonmesh_train/los.py. Match the chord construction of moonmesh.science.surface_path (see docs/training_codebase_notes.md).
- Endpoints: Moon-fixed 3D XYZ at radius 1737400 + ground height + antenna height. Use a straight chord (no refraction on the Moon).
- Sample interior points at a spacing of half a DEM pixel or less (configurable). For each sample: XYZ → lat/lon → master-grid x/y → bilinear DEM. clearance = |P| − (R + z).
- Per-receiver outputs:
  * los (0 = blocked, 1 = clear, 255 = unknown)
  * min_clearance_m, obstruction_x_m, obstruction_y_m, obstruction_distance_m
  * a valid flag and a quality flag (clear / blocked / partial / out_of_range)
  * when a wavelength is given, the max knife-edge ν and its location: ν = h·sqrt(2(d1+d2)/(λ·d1·d2)), with h = −clearance
- Invalid if any sample hits NaN or leaves the raster.
- Use numba njit with prange over receivers. Provide viewshed(tx_xy, h_tx, h_rx, dem, transform, crs, max_range_m, spacing_m, wavelength_m=None), returning grid arrays within max_range.
Tests in training/tests/test_los_analytic.py, using synthetic DEMs on a polar stereographic grid:
1. Bald sphere (DEM = 0): the LOS boundary is within one pixel of sqrt(2R·h_tx) + sqrt(2R·h_rx), which is 5272 m (2/2), 6804 m (5/2), 8531 m (10/2) and 15817 m (50/2).
2. Ridge with +0.5 m clearance → LOS, min_clearance ≈ +0.5. Raise it by 1 m → blocked, ≈ −0.5 (tolerance 0.05 m).
3. Reciprocity: swapping the endpoints and their heights gives identical results.
4. A NaN cell on the path → invalid.
5. A path that leaves the raster → partial/invalid.
6. Monotonicity: the visible set at h_tx = 10 contains the visible set at h_tx = 5, on a random rough DEM.
Then write training/scripts/03_check_los.py:
- 50 random tx/rx pairs within 8 km on the Haworth DEM, compared against moonmesh.science.surface_path with the same heights. Explain any mismatch (spacing, interpolation or chord construction) before changing code.
- A convergence test at 10, 5 and 2.5 m spacing for one Haworth transmitter (h_tx 5, h_rx 2): report the fraction of pixels that flip and where they are.
- The runtime of one full 8 km viewshed.
Save the reports to data/reports/los/.
```

**Check before the next prompt:** all 6 tests must pass, the `surface_path` mismatches must be zero or explained, and the flipped pixels should sit on the boundary. Don't go on until this is solid, because every label depends on it.

---

## Prompt 6: RF labels and one complete scenario

```text
Following AGENTS.md, implement training/src/moonmesh_train/rf.py:
- fspl_db(d_m, f_hz) = 20log10(d) + 20log10(f) − 147.55, using the 3D chord distance
- received_power_dbm; margin_db = prx − sensitivity
- ITU-R P.526 single knife-edge loss: J(ν) = 6.9 + 20log10(sqrt((ν−0.1)^2 + 1) + ν − 0.1) for ν > −0.78, else 0. Use the max ν from los.py.
- Class layers:
  * rf_covered: margin ≥ required margin
  * terrain_dead_zone: valid and los == 0
  * rf_dead_zone: valid and margin < required margin
  * terrain_caused_rf_outage: rf_dead_zone, but the free-space-only margin passes
  * unknown: not valid
  Apply the threshold last and always save the continuous margin.
Write training/scripts/04_label_scenario.py:
- Input: a scenario YAML (tx position, h_tx, h_rx, radio profile name).
- Output: one multi-band GeoTIFF on the master grid with bands los, min_clearance_m, obstruction_x_m, obstruction_y_m, obstruction_distance_m, valid_mask, path_loss_db, free_space_margin_db, received_power_dbm, link_margin_db, rf_covered, terrain_caused_rf_outage, quality_flags. Include band descriptions and units in tags.
- Plus a JSON sidecar containing: a full copy of the radio profile, propagation_model "fspl+p526_single_edge (simulated)", DEM source ids, sample spacing, code commit and threshold.
Tests:
- FSPL at 8 km ≈ 118.1 dB at 2.4 GHz and ≈ 115.6 dB at 1.8 GHz (±0.1)
- J(0) ≈ 6.0 dB; J(−1) = 0
- the outage logic on hand-built arrays
- a GeoTIFF round trip preserves georeferencing and band names
Then run one scenario: tx at the Haworth point, h_tx 5, h_rx 2, moonmesh_2p4_ref.
- Automatically pick three paths: clear, ridge-blocked, and near-boundary (|min_clearance| < 2 m).
- Plot each terrain profile with the chord, the first Fresnel zone and the obstruction point to data/reports/rf/profiles/.
- Report covered and dead areas in km².
```

**Check before the next prompt:** look at the three profile plots. The blocked path should visibly hit a ridge.

---

## Prompt 7: Experiment A (LunarRF comparison)

```text
Following AGENTS.md, implement Experiment A in training/scripts/05_exp_a_lunarrf.py using data/reports/lunarrf_findings.md.
1. Fill lunarrf_1p8 in radio_profiles.yaml only with values cited in the findings, with a comment naming the source file for each. Leave unknowns null.
2. If the mast catalog and all radio values exist:
   - Select masts whose max range reaches the Haworth window.
   - Run our per-mast labels with lunarrf_1p8.
   - Aggregate, following LunarRF's documented semantics: max received power → our best_prx; argmax → our best_id; count of LOS masts → our los_count.
   If something required is missing, run the LOS-only comparison (if mast positions and heights exist) and state exactly what was skipped and why.
3. Compare on valid pixels against the published 20 m Haworth layers (never the 160 m mosaics):
   - LOS confusion matrix and IoU
   - dB difference histogram, median, MAD, and map
   - covered/uncovered agreement at LunarRF's threshold, with IoU and false-covered km²
   - a disagreement map over hillshade
4. Write data/reports/exp_a/report.md with a diagnosis section:
   - a constant offset suggests power/gain bookkeeping
   - disagreement clustered on walls or ridges suggests terrain resolution or the diffraction model
   - a shifted band suggests registration
   Do not tune parameters to force agreement. Mark both LunarRF's layers and ours as simulated.
```

---

## Prompt 8: Transmitter sampling and splits

```text
Following AGENTS.md, write training/scripts/06_sample_transmitters.py and 07_make_splits.py. Use terrain layers only. Never look at coverage labels in this step.
1. Terrain strata from 1000 m relative elevation and slope: ridge/rim crest, shoulder, steep wall, floor, plain. Put the thresholds in the exp_b config.
2. Stratified random sample of 150 transmitters (~30 per stratum):
   - at least 500 m apart
   - only where a full max_range disk has valid terrain
   - record the stratum and a deployable flag (slope below a config limit and within the deployment radius of the site point)
3. Splits:
   - 5 km spatial blocks; assign whole blocks to train/val/test (~70/15/15, config seed).
   - Drop training transmitters within buffer_m (default = max_range_m) of any val/test transmitter. Report how many were dropped and how much training footprint still overlaps test footprints.
   - All heights, frequencies and augmentations of one transmitter share its split.
4. Write training/splits.json containing:
   - the seed, config hash and data hashes
   - transmitter ids per split
   - the planned cross-site splits: train Haworth → test Malapert + Nobile Rim 1, plus both rotations (empty until those sites are prepared)
5. Save counts per split (transmitters per stratum, blocks, deployable) and a map of transmitters colored by split over hillshade.
Tests: no transmitter appears in two splits; the buffer is respected; the same seed gives the same result.
```

---

## Prompt 9: Build the training dataset

```text
Following AGENTS.md, write training/scripts/08_build_dataset.py.
1. Scenarios: every transmitter in splits.json × tx heights [5,10,20,50] × rx height 2 × profile moonmesh_2p4_ref.
   - Reuse the 04_label_scenario functions.
   - Resumable: skip scenarios whose output and sidecar already exist with the same config hash.
   - A --workers flag.
   - After the first 5 scenarios, print the estimated total time and disk use, and require --yes to continue.
2. data/derived/datasets/haworth_pilot/scenarios.parquet, with the scenario schema from spec section 6 plus split, stratum and deployable.
3. Receiver tables for the tabular model, about 20k receivers per scenario:
   - a uniform sample (sample_kind=uniform)
   - a boundary oversample where |min_clearance| < 5 m or |margin − required| < 3 dB (sample_kind=boundary, with weights)
   Columns: the spec's receiver schema plus these features:
   - distance_m, sin/cos bearing, h_tx, h_rx, freq_ghz
   - tx and rx ground elevation, and their difference
   - slope, relative elevation 200 m and 1000 m, and roughness at both tx and rx
   - the tx horizon angle toward the receiver (precomputed per transmitter in 360 bins)
   - a coarse 16-point profile (the max terrain elevation angle seen from tx along the path, and the 16 sampled heights relative to the straight antenna-to-antenna line)
   Record the compute time of each feature group, so it can be charged to inference later.
4. Exclude invalid receivers from targets, but count them. Report per split: unique transmitters, blocks, valid px, blocked px, boundary px, unknown px, and covered prevalence (from the uniform sample only).
```

---

## Prompt 10: Baselines and LightGBM

```text
Following AGENTS.md, write training/scripts/09_train_tabular.py.
1. Baselines, evaluated on val:
   - Distance-only: LOS iff distance ≤ sqrt(2R·h_tx) + sqrt(2R·h_rx); covered iff that holds and the free-space margin ≥ required.
   - The deterministic labeler itself, as the reference, with its runtime per scenario.
2. LightGBM models:
   - Three models: an LOS classifier, a min_clearance regressor (Huber) and a link_margin regressor (Huber).
   - Use only the feature columns from step 08. Assert that no forbidden column (per AGENTS.md), split column or sample weight is among the inputs.
   - num_leaves 63, learning_rate 0.05, up to 2000 rounds, early stopping on val.
   - Class weights if LOS is imbalanced. Use the boundary weights in training only.
3. Coverage = predicted margin ≥ required_margin. Never touch test.
4. Save the models, feature list, config hash and a feature-importance plot. Warn if distance alone dominates.
5. Report on the val uniform sample, for each baseline and model:
   - LOS accuracy and F1
   - dead-zone precision and recall, false-covered rate
   - clearance MAE
   - margin MAE, bias, and MAE within ±3 dB of the threshold
```

---

## Prompt 11: Full-map evaluation and runtime

```text
Following AGENTS.md, write training/scripts/10_evaluate.py. It evaluates any predictor (deterministic, distance baseline, LightGBM, and later U-Net) on full maps.
- Use --split val by default. Allow --split test only together with --final.
- Predict full rasters per scenario (for LightGBM, compute features for every valid pixel in range).
- Metrics, reported separately for terrain dead zones and RF dead zones:
  * precision and recall, per-class IoU
  * area error km² and false-covered km²
  * boundary distance mean and p95 in meters (distance transform)
  * margin MAE, bias, and near-threshold MAE
  * unknown fraction
  Compute pixel areas from the CRS, including the polar stereographic scale factor.
- Stratify by site, range bin (0–2, 2–4, 4–6, 6–8 km), h_tx, stratum, PGDA observation-count quartile (if available) and distance-to-boundary bin.
- Runtime on CPU, cold and warm cache, end to end (raster read, features, inference, post-processing), against the deterministic labeler on identical windows. Report ms per scenario and the speedup.
- Cross-source check: regenerate labels for 10 scenarios on PGDA 78 5 m terrain and report the fraction of LOS pixels that flip versus the 20 m labels. This is the label-uncertainty floor.
- Figures:
  * TP/FP/FN/TN maps over hillshade for 4 representative scenarios
  * one traced contested path
- Create training/configs/acceptance.yaml with false_covered_frac ≤ 0.01, boundary_p95_m ≤ 60, near_threshold_margin_mae_db ≤ 2, min_speedup ≥ 10. Report PASS/FAIL for each criterion.
- Write raw CSVs, metrics.json and summary.md to data/reports/eval/<run_id>/.
```

**Check before the next prompt:** edit `training/configs/acceptance.yaml` to the thresholds you actually want, and commit it before you ever run with `--final`.

---

## Prompt 12: Published benchmarks

First, download the four PDFs into `docs/references/` (see the table at the top of this file).

```text
Following AGENTS.md: docs/references/ contains tall_lunar_towers_20230012634.pdf (NASA Tall Lunar Towers), nasa_viewshed_trade_20230014675.pdf (NASA viewshed trade study), jpl_rapid_low_cost_path.pdf (JPL Rapid Low-Cost Path study) and nasa_nokia_surface_network_20240003815.pdf (NASA/Nokia Future Lunar Surface Network Study). Extract their text with pypdf.
1. Write data/reports/benchmarks/configs.md. For each study, list every configuration value it states, with page numbers, and write "NOT STATED" where absent. Don't guess. Values to look for:
   - tower coordinates and heights, receiver height
   - terrain product and resolution, analysis extent
   - area method, frequencies, propagation tool
2. Write training/scripts/11_benchmarks.py, which reproduces each study with our deterministic code:
   - Tall Lunar Towers at Connecting Ridge with a 2 m receiver: 10 m and 50 m towers, compared with 185 km² and 325 km² of visible area.
   - The trade study's 4 m vs 20 m case.
   - JPL: a 15 m tx / 2 m rx viewshed, plus our FSPL+knife-edge path loss at 442.5 MHz and 2.245 GHz if a profile can be built from stated values.
   - NASA/Nokia §8.1.3: path gain vs antenna height at Connecting Ridge Site 1.
   Fetch any extra PGDA 90 windows through 00_download.py (print the commands for me if the network is blocked).
3. Report our value, theirs, the % difference, and the likely reasons for differences (terrain source and resolution, extent, area method, heights). Label everything as simulator-to-simulator consistency, not validation against measurements.
```

---

## Prompt 13: U-Net (run only if needed)

Only run this if Prompt 11 shows that LightGBM fails the boundary criteria **and** the deterministic code is too slow for your use.

```text
Following AGENTS.md, add torch to requirements-training.txt (the CPU build is fine for the smoke test; if I have an NVIDIA GPU, give me the uv command for the CUDA build) and implement a small transmitter-conditioned U-Net in training/src/moonmesh_train/unet.py, with training in training/scripts/12_train_unet.py.
- Input: tx-centered 832×832 patches at 20 m (±8.3 km), so the labels need no resampling.
- Channels: DEM minus tx ground elevation (scaled), slope, valid mask, distance, sin/cos bearing, and constant planes for h_tx, h_rx and log10(f).
- Three heads: min_clearance (Huber), link_margin (Huber) and an LOS logit (BCE), all masked by the valid mask.
- Size: about 5–8M parameters.
- Training:
  * AdamW, learning rate 1e-3 with a cosine schedule, batch 4, AMP, up to 50 epochs
  * early stopping on val false-covered area + boundary F1
  * augmentation: 90° rotations and flips, recomputing the bearing channels after each transform
  * use the same splits.json
- Log the parameter count and training time.
- Register it as a predictor in 10_evaluate.py and compare it against LightGBM and the deterministic code.
- Add a pytest marked slow that overfits 4 patches on CPU.
```

---

## Prompt 14: Held-out sites (Malapert and Nobile Rim 1)

First run the downloads yourself:

```powershell
python training/scripts/00_download.py lunarrf --parts 2 3 --yes
python training/scripts/00_download.py extract --site Malapert --band 1p800GHz
python training/scripts/00_download.py extract --site Nobile --band 1p800GHz   # check the exact site name in MANIFEST.txt
python training/scripts/00_download.py pgda78 --site malapert
python training/scripts/00_download.py pgda78 --site nobile-rim-1
```

```text
Following AGENTS.md, extend the pipeline to Malapert (LunarRF part 2, PGDA 78 Site23) and Nobile Rim 1 (part 3, Site06). Reuse the existing scripts with --site.
1. Run inventory, findings and alignment for both sites, and flag any differences from Haworth.
2. Sample transmitters using the same rules, and generate labels.
3. Evaluate the Haworth-trained models on both sites without retraining (the cross-site test).
4. Run the rotations from splits.json: train Haworth + Malapert → test Nobile, and train Haworth + Nobile → test Malapert.
5. Report per-site metrics side by side, and say where performance drops (terrain stratum, data quality).
```

---

## Prompt 15: App integration

```text
Following AGENTS.md (this task may modify moonmesh/ and frontend/), integrate surface coverage into the app. Read moonmesh/server.py and frontend/ first, and follow their existing patterns.
- Backend coverage route:
  * Inputs: site, tx position, h_tx, h_rx, radio profile, threshold, and predictor (deterministic or model).
  * Returns the LOS, margin, class and valid layers.
  * Cache key: terrain version, grid, tx coordinates, both heights, propagation model and profile hash. A threshold-only change reclassifies the cached margin without recomputing.
  * Fall back to deterministic when inputs are outside the evaluated ranges (sites, heights, range).
- Handle these states: loading, missing data, no valid pixels, no link, out of domain.
- Frontend "Surface coverage" mode:
  * layer toggles for elevation, slope, LOS, RF margin, covered/dead, and unknown (in its own color and hatch)
  * a legend, and the active model, terrain source and resolution
  * click-to-inspect: coordinates, terrain source, both heights, an obstruction profile chart, margin and state
  * stale-response protection when the tx is moved quickly
- Export: GeoTIFF layers plus a JSON sidecar (sources, checksums, threshold, masks, units, nodata, model and code versions, assumptions).
Add tests for the route and the cache key.
```

---

## Prompt 16: Demo and final report

```text
Following AGENTS.md:
1. Build an offline Haworth demo bundle (cached terrain and results) so the demo runs without downloads.
2. Demo flow: start at Haworth, place a mast, show a ridge-caused dead zone with its obstruction profile, then raise or move the mast and show the boundary change.
3. Write training/MODEL_CARD.md covering:
   - data sources and licenses
   - the label generator and its assumptions (simulated)
   - splits and metrics, with PASS/FAIL against acceptance.yaml
   - runtime
   - limitations and out-of-domain behavior
   - the final decision: ship the model, or ship the deterministic map
4. Go through every acceptance checkbox in section 5 of the spec and report pass/fail with evidence file paths.
```

---

Prompts 0–11 are the core pilot and run on a laptop. Prompt 13 is the only one that may need a GPU (Colab is enough).
If LightGBM passes your acceptance thresholds, or the deterministic map is already fast enough, skip Prompt 13 and go straight to 14.
