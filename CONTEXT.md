# CONTEXT.md: MOONMESH terrain-coverage model handoff

This file is the handoff from a long working session between Khush and Claude on Saturday, 14 November 2026 (America/Los_Angeles). It reflects the state at about 6:55 PM PT. If a later Claude or Codex session picks this up, read it top to bottom before acting, then check the live state (git log, `C:\moonmesh-data\reports\pipeline\step_*.json`), because a run was still in progress when this was written.

---

## 1. Summary

**Goal.** Build the MOONMESH "surface coverage and dead-zone" feature. Given lunar south-pole terrain, a transmitter location and height, a receiver height and a radio profile, it maps where the surface link has line of sight (LOS), where terrain blocks it, the RF link margin, and where coverage is unknown. It also trains a machine-learning model that predicts those maps. The spec is `MOONMESH_TERRAIN_COVERAGE_TRAINING.md`, at the repo root.

**Approach.** The training labels come from our own deterministic physics code (curved-Moon LOS + free-space loss + ITU-R P.526 single knife-edge diffraction), run on NASA PGDA 90 LOLA terrain. LunarRF (a published WPI simulation atlas) is used only as a benchmark, never as training input. The deterministic map is the minimum viable product. The learned model (LightGBM) ships only if it passes the pre-set acceptance thresholds.

**Status at about 6:55 PM PT.**
- Done and committed on branch `coverage-training`: Prompts 0–5b (by hand), then steps 06, 07, 08 and 09 (by the automated runner). All passed their gates.
- Running: step 10 (LightGBM training). The LOS and clearance models are finished; the margin model was about 60% done at 6:30 PM.
- Queued: step 11 (time-boxed evaluation) runs next in **Window 1**. **Window 2** is waiting in a loop and will automatically run steps 15, 16 and 17 (final test, app integration, demo and model card) once a commit titled `step 11:*` exists.
- Expected finish: about 8:30–9:00 PM PT.
- Optional and skipped for speed: step 12 (NASA/JPL benchmarks), step 13 (U-Net, deferred), step 14 (Malapert and Nobile held-out sites).
- Not pushed: `coverage-training` is about 30 or more commits ahead of `origin/coverage-training`.

**Headline results so far.**
- Experiment A (our code vs LunarRF at Haworth, 1.8 GHz, 267 masts): any-LOS IoU **0.9947**; no-reflection coverage IoU **0.9524**; median power difference **−0.01 dB**.
- LOS engine: matches the app's `surface_path` exactly (50/50 Haworth pairs, 200/200 synthetic). Halving the sample spacing changes only 0.0145% of pixels.
- Dataset: 588 scenarios, **11.76 M** receiver rows (train 8.48 M, val 1.68 M, test 1.60 M), 35 features, about 10.8 GB.

---

## 2. People, project and context

- **User:** Khush (Khush Kothari), a student. This is for the NASA International Space Apps Challenge 2026, project MOONMESH (a lunar south-pole mission connectivity and infrastructure optimizer).
- **Teammates** (from git history): NexusByteBooster / Jeswanth Sri Sai Battula (pushed the ORA relay lab, Moon rendering and drawn-area features to `main` on 14 Nov), and AyaanOberoi (the `UI` branch, Cesium Moon workspace).
- **GitHub repo:** `https://github.com/khushcoding123/hackNasa`.
- **Laptop:** Windows, device name "khushslaptop", PowerShell 5.1, 22 logical CPUs, 296 GB free on C: (at last check).
- **Tools:**
  - Codex CLI v0.169.0 (model "GPT-6.1-Sol medium"). Khush runs it in **YOLO mode** (full access).
  - Python 3.13 via `uv` (uv 0.11.26).
  - The app uses `scripts/start.ps1`, which creates its own venv at `.moonmesh-data/venv`.
- Khush asked for step-by-step, simple instructions, and later for the fastest path to a fully finished feature.

---

## 3. Requirements (from the spec)

The spec file is `MOONMESH_TERRAIN_COVERAGE_TRAINING.md`, at the repo root. Its links resolve from the root.

**Primary feature.** Given terrain, transmitter coordinates and height, receiver height and a radio configuration, map:
- geometric LOS (clear / occluded / unknown)
- minimum signed terrain clearance (m), with the obstruction location and distance
- path loss / received power / RF link margin (dB)
- operational coverage (covered / below margin / unknown)
- network coverage (optional)

Moving the transmitter or changing a height must change the map accordingly.

**Label names.**
- `terrain_dead_zone` = the geometric LOS is blocked.
- `rf_dead_zone` = margin < required.
- Terrain-caused RF outage = fails the terrain-aware budget, but passes the free-space budget.
- Unknown and nodata pixels are excluded from both classes.

**Research sites** (from `moonmesh/data.py` SITES):

| Site | Key | Latitude | East longitude |
|---|---|---:|---:|
| Haworth | `haworth` | −86.800 | 337.200 |
| Nobile Rim 1 | `nobile-rim-1` | −85.400 | 37.300 |
| Malapert Massif | `malapert` | −85.964 | 357.681 |

**App defaults** (from `moonmesh/models.py`):
- Surface radio: 2.4 GHz, 2 W (= 33.0103 dBm), tx/rx gain 4/4 dBi, loss 3 dB, sensitivity −105 dBm at 128 kbps, required margin 3 dB.
- Earth radio: 8.4 GHz, 20 W, 30/68 dBi, −115 dBm, 3 dB loss, 3 dB margin.
- Geometry: lander/observer antenna 2 m, relay mast 5 m, deployment radius 8 km, slope limit 15°.
- Demo window: 2026-11-01 to 2026-12-01, 30-minute step.

**Acceptance checklist** (spec section 5):
- grids overlap, with licenses, hashes and nodata recorded
- the map distinguishes elevation, LOS, RF margin, RF dead zones and unknown
- a changed transmitter gives an explainable change
- deterministic labels pass analytic and terrain checks, with convergence recorded
- labels keep obstruction locations and continuous margins, plus a matched LunarRF comparison
- splits grouped by geography and transmitter, with prevalence and metrics published
- learned vs deterministic compared on identical cases; exports keep georeferencing
- a demo that starts at Haworth, picks a mast, shows a ridge dead zone, changes the mast and shows the new boundary

**Two experiments** (spec section 6):
- **(A)** Reproduce a fixed-network LunarRF comparison at Haworth.
- **(B)** Train a transmitter-conditioned model on per-tower LOLA labels.

Direct-to-Earth (DTE), power and relay ranking stay downstream.

**Acceptance thresholds** (set in step 11 in `training/configs/acceptance.yaml`; edit them before the final test if needed):
- `false_covered_frac <= 0.01`
- `boundary_p95_m <= 60`
- `near_threshold_margin_mae_db <= 2`
- `min_speedup >= 10`

---

## 4. Environment and locations

| Thing | Location / value |
|---|---|
| Repo (real clone) | `C:\Users\khush\OneDrive - Lake Washington School District\nasahack\hackNasa` |
| Parent folder | `...\nasahack`. It contains only `hackNasa` now. A stray `git init` repo there was deleted. |
| Work branch | `coverage-training`. It includes teammate `main` up to `3929d49` via merge `edc6029` (4:46 PM). |
| `main` (remote) | `3929d49` (teammate ORA work); it also contains the earlier merge of the spec and prompts (`35798cc`) |
| Data root | `C:\moonmesh-data` (outside OneDrive). Env var `MOONMESH_DATA_ROOT` is set via `setx`. |
| Training venv | `C:\moonmesh-data\venv` (Python 3.13, uv). Activate with `C:\moonmesh-data\venv\Scripts\Activate.ps1`. |
| App venv | `hackNasa\.moonmesh-data\venv` (Python 3.13.6, from `start.ps1`). Do not install training packages there. |
| App data cache | `hackNasa\.moonmesh-data\` (about 718 MB: LOLA PDS LDEM_85S_20M and LDEM_75S_240M .IMG/.LBL, SPICE kernels, site crops). Treated as read-only by training. |
| Execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (needed for `Activate.ps1` and the npm `codex.ps1` shim) |
| Packages added for teammate tests | `uv pip install --python C:\moonmesh-data\venv\Scripts\python.exe -r requirements.txt httpx2` (adds networkx, scikit-learn and PyYAML from the teammate's requirements.txt, plus httpx2 for the Starlette TestClient). Done at about 6:20 PM. |

**Claude device access in this session:**
- the `nasahack` folder (delete permission granted)
- `C:\moonmesh-data`

---

## 5. Data acquired and key findings

### LunarRF atlas v2.0 (Zenodo 23301640, CC BY 4.0, simulated)

**Archives** (`MANIFEST.txt` lists 15):
- **Downloaded:** context (165 MB), regions_20m_part1 (398 MB), mosaics_160m_part2 (59 MB), candidates (8.6 MB), validation (10 MB). All sha256-verified against `SHA256SUMS`.
- **Not downloaded:** mosaics part1 (395 MB), regions part2/part3 (381/355 MB), sideruns.

**The spec's MD was wrong about the metadata.** It is **not** in the context tar:
- `radio_params.json`, `manifest.json`, `store.json` and `stats_exact/*` are in mosaics part2.
- The mast catalog is in the candidates tar.
- Validation JSON and figures are in the validation tar.

**Radio params** (`interim/lunarrf/meta/atlas_v2/bundle/radio_params.json`):
- Frequencies 1.8, 2.58, 3.65, 5.855 and 25.375 GHz.
- tx 30 dBm; gains 0/0 dBi; mast 4.0 m; rx 1.5 m; max radius 22,040 m.
- rx sensitivity / coverage threshold −94.40475498807768 dBm; datalink threshold −84.40 dBm.
- Diffraction and LOS model "delta_bullington"; two-ray height-averaged reflection; eps_r 3.7; loss tangent 0.01; rms height 0.03 m.
- No fixed loss or extra margin is stated.

**Mast catalog:** `data/atlas_base/candidates.csv`.
- 11,827 masts, all 4 m. Its sha256 `e435673b…` matches `docs/candidate_freeze.md`.
- **267** masts are within 22,040 m of the Haworth window.

**Haworth layers:**
- 2359 × 2309 px at 20 m, bounds [−61320, 67460, −14140, 113640].
- Polar stereographic, R = 1,737,400 m.
- Layers: `best_prx`, `best_id`, `best_los`, `count`, `los_count`, plus `*_norefl` variants, plus the context `Haworth__dem.tif` / `__psr.tif`.

**Two quirks:**
- **Count layers declare nodata = 0, which actually means zero masts.**
- LunarRF's Haworth DEM is **identical** to PGDA 90 (0.0 m difference on all 5.4 M pixels).

**Sites in the archives:**
- `Malapert_Massif` spans region parts 1 and 2.
- A `Connecting_Ridge` region exists, which is the NASA Tall Lunar Towers site.

### Terrain

- **PGDA 90** (`LDEM_80S_20MPP_ADJ.TIF`, 2.696 GB, read by window with /vsicurl): two windows in `interim/pgda90/`.
  - 10 km buffer (3359 × 3309, `..._43e4fd8aecfcbaa4.tif`)
  - 25 km buffer (4859 × 4809, `..._a1dd8a7c810088de.tif`)
  - Both are exactly on the LunarRF 20 m lattice.
- **PGDA 78** Haworth 5 m (`Haworth_final_adj_5mpp_surf.tif`, 142 MB, 5960 × 5960). Its companion slope/uncertainty/count layers were **not** downloaded.
- **App LOLA PDS** 20 m and 240 m: reused from `.moonmesh-data/terrain`.
- **Alignment results:**
  - PGDA90 − PDS20: median −0.038 m, MAD 0.65 m
  - PGDA90 − PGDA78: median −0.031 m, MAD 0.17 m
  - Registration shift 0.0 px for all
  - The PGDA vertical datum is still undocumented (a TODO).

---

## 6. Work completed

| Commit (PT) | Step | Result |
|---|---|---|
| 28a93a3 | Prompt 0 | `AGENTS.md` rules, plus `docs/training_codebase_notes.md` (verified accurate against the code) |
| c0acc04 | Prompt 1 | Workspace, `requirements-training.txt`, `pyproject.toml`, `paths.py` (data root), `config.py` (null-field guard), radio profiles, exp configs. 37 tests. |
| 4f33756 | Prompt 2 | `00_download.py` (lunarrf-meta, lunarrf, extract, pgda78, pgda90-window, lola-pds) and `training/data_manifest.json`. 69 tests. |
| 38615a5 | Prompt 2b | Added mosaics2/candidates/validation, `extract-meta`, site context DEM extraction, unique `source_id`s, and the `sites` subcommand. Also fetched the 25 km PGDA 90 window. 91 tests. |
| f6538cf | Prompt 3 | `01_inventory.py`: 289 rasters, 0 errors. `reports/lunarrf_findings.md` holds all the facts in section 5. |
| 417e500 | Prompt 4 | `02_align.py`: master grid, aligned DEMs, features (slope, relative elevation 200/1000 m, curvature, roughness 100 m, valid mask), PNGs. PASS. |
| 2d53a91 | Prompt 5 | `los.py` + `03_check_los.py`: 50/50 app agreement; convergence 10→5 m 73 flips (0.0145%), 5→2.5 m 25 flips; 8 km at 10 m spacing ≈ 8 s. |
| 85ef452 | Prompt 5b | `mode="app"` (exact surface_path), `mode="fast"` (8.3× faster, identical), the 4/1.5 m test, 22 km timing (brute 236 s → fast 28.4 s; 267 masts ≈ 2.1 h), `viewshed_8km.png`. 16 LOS tests. |
| a44e79f | Automation | `docs/steps/COMMON.md`, `step_06…17.md`, `scripts/run_training_steps.ps1` |
| 1446de1 | Step 06 PASS | `rf.py` + `04_label_scenario.py`. Research point: 503,434 valid, 70,612 clear (exact match). Invariants hold. GeoTIFF 10.7 MB. Uint8 classes live in a companion file because a multi-band GeoTIFF needs one dtype. |
| 6fec018 | Step 07 PASS | Experiment A (`05_exp_a_lunarrf.py`, `exp_a.py`): 267/267 masts; any-LOS IoU 0.9947; no-refl coverage IoU 0.9524; median Δ −0.0101 dB; p95 13.44 dB; false-covered 34.45 km², missed 48.62 km². Report in `reports/exp_a/report.md`. |
| 26f0a7b | Step 08 PASS | 147 of 150 transmitters (106 train / 21 val / 20 test, 5 strata of about 30, 5 km blocks, 8 km buffer). Min spacing 957 m; min train-to-holdout 8,649 m; residual footprint overlap 21.37 km² (1.2%); only 5 deployable (all in train). `training/splits.json`. |
| 634af8f | (Claude) | Step 11 time-boxed |
| c3c5ee4 | Step 09 PASS | `08_build_dataset.py`, `dataset.py`: 588 scenarios; 20,000 receivers each (15,000 uniform + 5,000 boundary, inverse-multiplicity weights); 11.76 M rows; 35 features; 10.79 GB; covered prevalence train 0.555 / val 0.506 / test 0.508. |
| edc6029 | merge | `origin/main` (teammate ORA work) merged into `coverage-training` at 4:46 PM |
| (running) | Step 10 | LightGBM. LOS: 407 rounds (best 357), logloss 0.0314, 775 s. Clearance: 2000 rounds, Huber 20.13, 3405 s. Margin: ≥1200 of 2000 rounds at 6:30 PM, Huber 0.597 and still improving. Files: `training/scripts/09_train_tabular.py`, `training/src/moonmesh_train/tabular.py`, `training/tests/test_tabular.py`, `training/configs/tabular_haworth.yaml`. Models: `C:\moonmesh-data\models\tabular\`. |

---

## 7. Technical design and decisions

- **Grid:**
  - CRS: polar stereographic, `+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +R=1737400`.
  - Master grid: 4859 × 4809 at 20 m, bounds [−86320, 42460, 10860, 138640].
  - It is the LunarRF lattice extended by 25 km, so 22 km links have terrain.
  - LunarRF layers are placed by pixel offset and never resampled.
- **LOS (`los.py`):**
  - Endpoints become Moon-fixed XYZ at R + ground + antenna height, joined by a straight 3D chord.
  - Interior samples every ≤10 m (half a pixel). Each sample: XYZ → stereographic, `px = 2R·y/(r−z)`, `py = 2R·x/(r−z)`, then a bilinear DEM lookup.
  - `clearance = |P| − (R + z)`. Strictly > 0 means clear.
  - Any NaN, or leaving the raster, marks the receiver invalid.
  - Outputs: los (0/1/255), min_clearance, obstruction x/y/distance, max knife-edge ν and its location, a quality flag (clear 0 / blocked 1 / partial 2 / out_of_range 3).
  - Modes: `default` (brute force, the reference), `fast` (identical, allocation-free; **use this for labels**), `app` (reproduces `moonmesh.science.surface_path`).
- **Bald-sphere horizon checks** (√(2R·h_tx) + √(2R·h_rx)):
  - 5,272 m (2/2), 6,804 m (5/2), 8,531 m (10/2), 15,817 m (50/2), 6,011 m (4/1.5)
  - Note: the app's 8 km radius is beyond the bald-Moon limit for a 5 m mast and a 2 m receiver.
- **RF (`rf.py`):**
  - FSPL = 20log10(d_m) + 20log10(f_Hz) − 147.55 (chord distance). This is about 118.1 dB at 8 km / 2.4 GHz and 115.6 dB at 1.8 GHz. It matches the app's `network.link_margin` (20log10 km + 20log10 GHz + 92.45).
  - Diffraction loss J(ν) = 6.9 + 20log10(√((ν−0.1)² + 1) + ν − 0.1) for ν > −0.78 (J(0) ≈ 6.0 dB).
  - Path loss = FSPL + J(ν_max).
  - Classes are thresholded last; the continuous margin is always kept.
  - Propagation model tag: "fspl+p526_single_edge (simulated)". No reflection.
- **Radio profiles** (`training/configs/radio_profiles.yaml`):
  - `moonmesh_2p4_ref` = the app surface radio, with max range 8 km.
  - `lunarrf_1p8` = LunarRF values. `fixed_loss_db` 0 and `required_margin_db` 0 are documented assumptions.
- **Splits:** spatial 5 km blocks, an 8 km buffer, and transmitter-grouped (all heights of a transmitter share its split). Cross-site plan: train Haworth, test Malapert / Nobile Rim 1, plus rotations (step 14, optional).
- **Dataset features:**
  - distance, sin/cos bearing, heights, frequency
  - tx/rx ground elevation and their difference
  - slope, relative elevation 200/1000 m and roughness at both ends
  - a tx horizon angle (360 bins)
  - a 16-point coarse profile

  **Forbidden as inputs:** LunarRF outputs, IDs, and full-trace LOS outputs (min_clearance, obstruction_*).
- **Model:** LightGBM, three models (LOS classifier; clearance and margin Huber regressors). num_leaves 63, lr 0.05, ≤2000 rounds, early stopping on val. Coverage = predicted margin ≥ required.
- **Decision rule:** ship the learned model only if it passes **all** acceptance criteria on the one-time `--final` test run (step 15). Otherwise the app uses the deterministic map, which takes about 1 s per 8 km LOS map with the fast tracer.
- **Process decisions:**
  - Codex CLI, not the Codex app.
  - The user runs YOLO. Claude recommended `--sandbox workspace-write --ask-for-approval on-request --add-dir C:\moonmesh-data` instead.
  - Data and model weights are never committed.
  - Work happens on `coverage-training`; push it and open a PR at the end, never push straight to `main`.
  - Fastest path: skip steps 12 and 14, defer 13.

---

## 8. Code and file map

**Repo (`hackNasa`, branch `coverage-training`):**
- `MOONMESH_TERRAIN_COVERAGE_TRAINING.md`: the spec
- `AGENTS.md`: standing rules for Codex:
  - The spec is the source of truth.
  - No pretrained weights; build from scratch.
  - Use the training venv path; keep app tests passing.
  - `.moonmesh-data` is read-only.
  - Never invent metadata; use null plus a TODO.
  - Never modify data/raw; never commit data or weights.
  - The data root comes from the environment variable.
  - Use pathlib and Python-only tools (Windows).
  - New code goes in `training/`.
  - Every script has an argparse CLI.
  - Mark everything simulated.
  - Respect the forbidden inputs.
  - Never tune on test.
  - **Never run git commit/push/reset/checkout.**
  - Run pytest and report.
- `docs/training_codebase_notes.md`: how the app works (`surface_path`, PDS decoding, defaults, projection, `link_margin`)
- `docs/CODEX_PROMPTS.md`: the original manual prompts 0–16, with user steps
- `docs/steps/COMMON.md`, `docs/steps/step_06.md … step_17.md`: unattended step specs with gates. Step 11 is the time-boxed version.
- `scripts/run_training_steps.ps1`: the runner (section 9)
- `pyproject.toml` (editable install for moonmesh, lunar_core and moonmesh_train), `requirements-training.txt`
- `training/configs/`: radio_profiles, exp_a_lunarrf_haworth, exp_b_haworth_pilot, downloads, alignment, los_checks, dataset_haworth, tabular_haworth
- `training/scripts/`: 00_download, 01_inventory, 02_align, 03_check_los, 04_label_scenario, 05_exp_a_lunarrf, 06_sample_transmitters, 07_make_splits, 08_build_dataset, 09_train_tabular (later: 10_evaluate, …)
- `training/src/moonmesh_train/`: paths, config, downloads, inventory, alignment, los, rf, rf_report, exp_a, sampling, dataset, tabular
- `training/tests/`: about 151+ pytest tests
- `training/data_manifest.json`, `training/splits.json`

**Data root (`C:\moonmesh-data`):**
- `raw/lunarrf/` (MANIFEST, SHA256SUMS, 5 tars), `raw/pgda78/haworth/`
- `interim/lunarrf/Haworth/…` (region and context layers), `interim/lunarrf/meta/…` (metadata), `interim/pgda90/` (2 windows)
- `derived/terrain/haworth/`:
  - `grid.json`, `pgda90_dem_20m.tif` (primary DEM), `pds20_dem_20m.tif`, `pgda78_dem_20m.tif`, `pgda78_native_5m.tif`
  - `features_pgda90/*.tif`, hillshades, difference rasters
  - `lunarrf/*.tif` (on the master lattice)
- `derived/datasets/haworth_pilot/`: scenarios/* (labels.tif, labels_classes.tif, labels.json, receivers.parquet, horizon.npz, dataset.json), plus scenarios.parquet and the receiver tables
- `models/tabular/`: LightGBM models and checkpoints
- `reports/`: inventory.md, lunarrf_findings.md, align/, los/, rf/, exp_a/, splits/, dataset/, `pipeline/step_NN.json`
- `venv/`: the training venv

---

## 9. Automation: how the remaining steps run

`scripts/run_training_steps.ps1` (PowerShell 5.1, ASCII). For each `docs/steps/step_NN.md` in the `-From`/`-To` range, it:
1. Runs `codex exec --dangerously-bypass-approvals-and-sandbox "<read COMMON.md, then step file; unattended; write status file>"`.
2. Requires `C:\moonmesh-data\reports\pipeline\step_NN.json` with status `pass` or `skipped`.
3. Runs `python -m pytest training -q` and `python -m unittest discover -s tests` with the training venv.
4. Runs `git add -A` and commits `step NN: <title> [status]`.
5. Stops at the first problem and prints the `-From` value to resume from.

Before it starts, it refuses to run if the branch isn't `coverage-training` or the working tree is dirty.

`COMMON.md` tells Codex:
- Never ask questions; record decisions.
- Never run git writes.
- Use the training venv; network is allowed only for the named downloads.
- Run resumable chunks of 10 minutes or less.
- Check disk space (≥10 GB must remain).
- Reuse earlier code.
- Mark everything simulated.
- Keep both test suites passing.
- Use this status JSON schema: `{step, title, status, summary, gates[], decisions[], warnings[], outputs[]}`.

| Step file | Purpose | Status |
|---|---|---|
| step_06 | RF labels | done (pass) |
| step_07 | Experiment A vs LunarRF | done (pass) |
| step_08 | transmitter sampling and splits | done (pass) |
| step_09 | dataset build | done (pass) |
| step_10 | LightGBM + baselines | **running** |
| step_11 | full-map eval on val (time-boxed: 24 val scenarios, 5 cross-source, no torch); writes `acceptance.yaml` + `reports/eval/unet_decision.json` | queued (Window 1) |
| step_12 | NASA/JPL benchmarks (downloads the 4 PDFs via the NTRS API; Connecting Ridge terrain) | optional, not scheduled |
| step_13 | U-Net, only if recommended (otherwise "skipped") | deferred |
| step_14 | Malapert + Nobile held-out sites and rotations | optional, not scheduled |
| step_15 | final one-time test (`--final`) + `reports/eval/final/decision.json` (ship model or deterministic) | queued (Window 2) |
| step_16 | app integration: coverage route, cache, fallback, "Surface coverage" UI mode, layers, click-inspect, GeoTIFF + JSON export; gates: tests, `npm.cmd run build`, server smoke test | queued (Window 2) |
| step_17 | offline Haworth demo bundle, `docs/DEMO.md`, `training/MODEL_CARD.md`, `reports/acceptance_checklist.md` | queued (Window 2) |

**Windows in use right now:**
- **Window 1:** running `powershell -ExecutionPolicy Bypass -File scripts\run_training_steps.ps1 -From 10 -To 11`. (It was originally started `-From 6 -To 11` at about 2:20 PM.) Do not press Ctrl+C.
- **Window 2:** waiting:
  ```powershell
  while (-not ((git log -1 --format=%s) -like "step 11:*")) { Start-Sleep -Seconds 60 }; powershell -ExecutionPolicy Bypass -File scripts\run_training_steps.ps1 -From 15 -To 17
  ```

**Scheduled check-in:** Claude set a one-time `send_later` for 7:30 PM PT (trigger `trig_01W1cAKLW5XD85HBF6oq8RfQ`). When it fires:
- If step 11 is committed and Window 2 has started, notify Khush (message + push) with the step 10/11 results.
- Otherwise, re-check every 15 minutes.
- If anything breaks, notify him immediately.

---

## 10. Incidents and lessons (what went wrong and how it was fixed)

1. **Only the MD was attached**, not the datasets. The datasets are the links inside the spec.
2. **OneDrive:** the repo lives in OneDrive. The multi-GB data was moved to `C:\moonmesh-data` via `MOONMESH_DATA_ROOT` so it doesn't sync or lock files.
3. **Wrong git root:** Khush ran `git init` in `nasahack`. The real clone is `nasahack\hackNasa`. Claude deleted the stray `.git` and the misplaced `nasahack\docs`, created the branch, and committed the spec and prompts.
4. Khush later **merged `coverage-training` into `main` and pushed** (`35798cc`). The branch was then fast-forwarded and used for the work.
5. **Prompt 2 download:** the `pgda90-window` command was first run with the literal `PASTE-PATH-HERE`, then with a folder path ("Permission denied"). Fixed by using the full file path.
6. **The spec named the wrong archives for the LunarRF metadata** → Prompt 2b.
7. **LunarRF links reach 22 km** → a 25 km PGDA 90 window was added.
8. **Prompt 5 was run from the older text** → follow-up 5b. Lesson: paste the latest prompt text.
9. **Codex Prompt 5 brute-force 22 km = 17.5 h for 267 masts** → the fast exact tracer (8.3×) made Experiment A take about 1.5 h.
10. **`device_commit_files` once wrote a stale cached file** (the step 11 edit). Fixed by resending under a new staged filename.
11. **Branch switch mid-run (about 5:17 PM):** the folder was switched to `UI`, then `main`, while step 10 ran (reflog: merge origin/main into coverage-training at 4:46 PM, then checkout UI, then checkout main). This removed the training code from the working tree, and the runner would have committed onto `main`. At about 6:10 PM, Claude ran `git checkout coverage-training` from the device VM; step 10's untracked files were kept and step 10 kept running. **Lesson: never switch branches or pull in this folder while the runner works.** Use a separate folder or worktree for teammate branches.
12. **Teammate tests added dependencies:** networkx, scikit-learn, PyYAML and httpx2 were installed into the training venv. With them, 65 app tests pass (2 skipped) in Claude's check environment.
13. **The run took longer than hoped** → step 11 was time-boxed, and steps 12, 13 and 14 were made optional.
14. A Claude Docs handoff doc was started (`https://claude.ai/artifact/DUAgHA6dQk7HmAYk1mA1jA`). Only its outline exists; this `CONTEXT.md` supersedes it.

---

## 11. Unfinished tasks and next steps

- [ ] Step 10 finishes, then the runner commits `step 10: …`.
- [ ] Step 11 (time-boxed eval on val) finishes and is committed. Check `reports/eval/<run_id>/summary.md`, `acceptance.yaml` and `unet_decision.json`.
- [ ] Window 2 auto-runs steps 15 → 16 → 17 (about 1.5 h) and commits each.
- [ ] Claude reviews steps 10, 11 and 15–17 (model vs deterministic, the final decision, the app feature, the demo).
- [ ] Manually test the app: `powershell -ExecutionPolicy Bypass -File scripts\start.ps1`, open http://127.0.0.1:8765, open Surface coverage mode, place a mast at Haworth, change its height, click a dead zone.
- [ ] Push: `git push -u origin coverage-training`, then open a PR from `coverage-training` into `main` on GitHub (do not push to `main` directly).
- [ ] Decide how teammates get the data and demo bundle (it is not in git; for example a GitHub release or a shared drive).
- [ ] Optional: step 12 (`-From 12 -To 12`), step 14 (`-From 14 -To 14`), step 13 (U-Net; needs torch, and a GPU or Colab).
- [ ] Optional TODOs:
  - download the PGDA 78/90 quality layers
  - document the PGDA vertical datum
  - review the Experiment A disagreement tail (p95 13.4 dB; about 34 km² false-covered and 49 km² missed, mostly from the diffraction-model difference: single knife-edge vs delta-Bullington)
  - only 5 deployable transmitters, all in train
  - 1.2% train/test footprint overlap
- [ ] If a resume is needed after a STOPPED message: fix the cause, make sure the tree is clean (commit or discard), then rerun the runner with the `-From` it printed.

---

## 12. Command cheat sheet (PowerShell, from the repo root)

```powershell
cd "C:\Users\khush\OneDrive - Lake Washington School District\nasahack\hackNasa"
C:\moonmesh-data\venv\Scripts\Activate.ps1                  # training venv
git branch ; git status ; git log --oneline -5              # check you are on coverage-training and clean
powershell -ExecutionPolicy Bypass -File scripts\run_training_steps.ps1 -From 15 -To 17   # run or resume steps
Get-Content C:\moonmesh-data\reports\pipeline\step_10.json  # a step's result
python -m pytest training -q ; python -m unittest discover -s tests   # both test suites
uv pip install --python C:\moonmesh-data\venv\Scripts\python.exe -r requirements.txt httpx2   # deps after teammate merges
$py = 'C:\moonmesh-data\venv\Scripts\python.exe'; & $py training/scripts/00_download.py sites   # download tool
git push -u origin coverage-training                        # share the branch when done
```

Manual Codex usage, if not using the runner (YOLO, as Khush uses it):
```powershell
codex 'Open docs/CODEX_PROMPTS.md and carry out Prompt N exactly as written in its text block. Do only that prompt. Do not run the commands I am told to run myself in that file; I will run those. When you are done, report the files changed, commands run, test results and open questions, then stop.'
```
The safer flags Claude recommended: `codex --sandbox workspace-write --ask-for-approval on-request --add-dir C:\moonmesh-data '...'`.

---

## 13. Runner script (full source of `scripts/run_training_steps.ps1`)

```powershell
<#
Runs the remaining MOONMESH training steps (docs/steps/step_NN.md) with Codex, unattended.
For each step it: 1) runs Codex non-interactively on the step file, 2) reads the step's status file
(pass / skipped / fail), 3) runs the training and app test suites, 4) commits the result,
and stops at the first problem so you can fix it and resume with -From.
Usage, from the repository root:
  powershell -ExecutionPolicy Bypass -File scripts\run_training_steps.ps1 -From 6 -To 11
#>
param([int]$From = 6, [int]$To = 17)
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
if (-not $env:MOONMESH_DATA_ROOT) { $env:MOONMESH_DATA_ROOT = "C:\moonmesh-data" }
$py = "C:\moonmesh-data\venv\Scripts\python.exe"
$statusDir = Join-Path $env:MOONMESH_DATA_ROOT "reports\pipeline"
New-Item -ItemType Directory -Force -Path $statusDir | Out-Null
$script:current = $From
function Stop-Run([string]$message) {
    Write-Host ""; Write-Host "STOPPED: $message" -ForegroundColor Red
    Write-Host "Nothing from this step was committed. Fix the problem (or send this message to Claude)," -ForegroundColor Yellow
    Write-Host "then resume with: powershell -ExecutionPolicy Bypass -File scripts\run_training_steps.ps1 -From $script:current -To $To" -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path $py)) { Write-Host "Training venv not found at $py"; exit 1 }
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne "coverage-training") { Write-Host "Switch to the coverage-training branch first (current: $branch)."; exit 1 }
$dirty = git status --porcelain
if ($dirty) { Write-Host "You have uncommitted changes. Commit or discard them first:" -ForegroundColor Yellow; $dirty; exit 1 }
$steps = Get-ChildItem -Path "docs\steps" -Filter "step_*.md" | Sort-Object Name
foreach ($file in $steps) {
    $n = [int]($file.BaseName.Substring(5))
    if ($n -lt $From -or $n -gt $To) { continue }
    $script:current = $n
    $id = "{0:D2}" -f $n
    $statusFile = Join-Path $statusDir "step_$id.json"
    if (Test-Path $statusFile) { Remove-Item $statusFile }
    Write-Host ""; Write-Host "=== Step $id ($($file.Name)) started $(Get-Date -Format 'HH:mm') ===" -ForegroundColor Cyan
    $prompt = "Read docs/steps/COMMON.md, then carry out docs/steps/$($file.Name) exactly, following AGENTS.md. This is an unattended run: never ask questions or wait for input; make the most reasonable documented choice and continue. Finish by writing the status file named at the end of the step file."
    codex exec --dangerously-bypass-approvals-and-sandbox $prompt
    if ($LASTEXITCODE -ne 0) { Stop-Run "Codex exited with code $LASTEXITCODE on step $id." }
    if (-not (Test-Path $statusFile)) { Stop-Run "Step $id did not write its status file ($statusFile)." }
    $status = Get-Content $statusFile -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Host "Step $id status: $($status.status). $($status.summary)"
    foreach ($w in $status.warnings) { Write-Host "  warning: $w" -ForegroundColor Yellow }
    if ($status.status -ne "pass" -and $status.status -ne "skipped") { Stop-Run "Step $id reported status '$($status.status)'. See $statusFile." }
    & $py -m pytest training -q
    if ($LASTEXITCODE -ne 0) { Stop-Run "Training tests failed after step $id." }
    & $py -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) { Stop-Run "App tests failed after step $id." }
    git add -A
    git commit -q -m "step ${id}: $($status.title) [$($status.status)]"
    Write-Host "Step $id committed $(Get-Date -Format 'HH:mm')." -ForegroundColor Green
}
Write-Host ""; Write-Host "Finished steps $From to $To. Status files are in $statusDir." -ForegroundColor Green
```

---

## 14. Risks, caveats and open questions

- **Everything is simulated.** No public, georeferenced measured lunar coverage data exists. Agreement with LunarRF is simulator-to-simulator consistency, not ground truth.
- **The learned model may not beat the deterministic map.** The fast tracer is already about 1 s per 8 km LOS map, and the `min_speedup >= 10` criterion is hard to meet. If the model fails, the app ships the deterministic map. This is acceptable under the spec.
- **The runner's app-test gate runs the teammates' tests too.** If teammates add dependencies or data-dependent tests, steps can STOP for reasons unrelated to training. Fix by installing the dependencies, or by switching the gate to `python -m pytest tests` (their `conftest.py` skips data-dependent tests under pytest).
- **Codex plan usage limits** can stop a run partway. Resume with `-From`.
- **Laptop sleep or branch switches** break runs. Keep it plugged in and awake, and don't switch branches.
- `lunarrf_1p8` uses fixed_loss 0 and required_margin 0 as documented assumptions.
- The PGDA vertical datum is undocumented. Heights agree across sources to within about 0.04 m median.
- **Open questions:**
  - Should steps 12 and 14 run before the hackathon submission?
  - How should the data and demo bundle be shared with the team?
  - Should the acceptance thresholds be changed before step 15 (they are committed in step 11)?

---

## 15. Instructions for the next assistant

1. Read this file, `AGENTS.md`, and `MOONMESH_TERRAIN_COVERAGE_TRAINING.md`.
2. Check the live state:
   - `git log --oneline -10` on `coverage-training`
   - `C:\moonmesh-data\reports\pipeline\step_*.json`
   - whether Windows 1 and 2 are still running (recent files in the data root)
3. Review each new step's status file and reports before telling Khush it is done. Look at the images in `reports/*` and the numbers.
4. Keep instructions short and step-by-step for Khush. Give exact PowerShell commands. Use the training venv path.
5. Never switch branches in the working folder while a runner is active. Never commit data or weights. Never push to `main` directly.
