# Common rules for unattended steps

You are running one step of the MOONMESH coverage pipeline unattended. It was launched by `scripts/run_training_steps.ps1`. Follow AGENTS.md, this file, and the step file.

## Unattended rules

- Never ask questions or wait for input. When a choice is needed, make the most reasonable one consistent with MOONMESH_TERRAIN_COVERAGE_TRAINING.md, AGENTS.md and the earlier reports. Record it under "decisions" in the status file, then continue.
- Never run git commit, push, reset, checkout or stash. The runner commits after a step passes. Read-only git commands (status, diff, log, rev-parse) are fine.
- Run Python with `C:\moonmesh-data\venv\Scripts\python.exe`. Install packages only into that venv, with uv.
- Network access is available. Download only what the step names.
- Long computations: write resumable scripts that skip finished work and save checkpoints. Run them in chunks so no single command runs longer than about 10 minutes, and keep going until the work is complete. Leave no background process running when you finish.
- Disk: before writing more than 1 GB, check free space on C:. If less than 10 GB would remain, stop with status "fail" and say why.
- Reuse code from earlier steps. Do not rewrite earlier outputs unless the step says so.
- Everything simulated (our labels and LunarRF's) is marked "simulated" in outputs and reports.

## What already exists (Prompts 0-5, committed)

All data paths below are under the data root `C:\moonmesh-data` (environment variable MOONMESH_DATA_ROOT).

- Master grid: `derived/terrain/haworth/grid.json`. Polar stereographic, R = 1737400 m, 20 m pixels, 4859 x 4809, bounds [-86320, 42460, 10860, 138640]. It is the LunarRF Haworth lattice extended by 25 km on each side.
- Primary DEM: `derived/terrain/haworth/pgda90_dem_20m.tif` (PGDA 90 LDEM_80S_20MPP_ADJ, identical to LunarRF's Haworth terrain). Terrain features: `derived/terrain/haworth/features_pgda90/*.tif`. Hillshade: `derived/terrain/haworth/hillshade_pgda90.tif`. PGDA 78 5 m: `derived/terrain/haworth/pgda78_native_5m.tif` and `pgda78_dem_20m.tif`.
- LunarRF Haworth layers on the master lattice: `derived/terrain/haworth/lunarrf/*.tif` (simulated). In the count layers, the declared nodata value 0 means zero masts, not missing data.
- LunarRF findings: `reports/lunarrf_findings.md`. Metadata is under `interim/lunarrf/meta/`: `atlas_v2/bundle/radio_params.json` and `data/atlas_base/candidates.csv`, the 11,827-mast atlas catalog with all masts at 4 m; 267 of them are within 22,040 m of the Haworth window.
- LOS tracer: `training/src/moonmesh_train/los.py`. Use `mode="fast"` with 10 m spacing for all label generation; it is proven identical to the brute-force reference. Reference numbers in `reports/los/report.md`: research point, 8 km, h_tx 5 m / h_rx 2 m, 10 m spacing gives 503,434 valid and 70,612 clear pixels.
- Radio profiles: `training/configs/radio_profiles.yaml`. moonmesh_2p4_ref is complete; lunarrf_1p8 is filled in step 07. Config loader: `training/src/moonmesh_train/config.py`.
- Download tool: `training/scripts/00_download.py`, with the subcommands sites, lunarrf --parts ..., extract, extract-meta, pgda78, pgda90-window and lola-pds.
- The app's research points are in `moonmesh/data.py` SITES: Haworth (-86.8, 337.2), Nobile Rim 1 (-85.4, 37.3), Malapert (-85.964, 357.681).

## Finishing a step

1. Run `python -m pytest training -q` and `python -m unittest discover -s tests` with the training venv. Both must pass.
2. Write `reports/pipeline/step_NN.json`, where NN is the two-digit step number, in this format:

```json
{"step": 6, "title": "short title", "status": "pass", "summary": "one or two sentences",
 "gates": [{"name": "gate name", "passed": true, "value": "measured value"}],
 "decisions": ["choices you made without asking"],
 "warnings": ["anything a reviewer should look at"],
 "outputs": ["important output paths"]}
```

   - The status is "pass" only if every gate in the step passed.
   - It is "fail" if a gate failed and you could not fix it within the step. Explain why in the summary.
   - It is "skipped" only if the step explicitly allows skipping.
3. End your final message with the same summary.
