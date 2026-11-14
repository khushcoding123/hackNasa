# Terrain coverage training workspace

Follow [AGENTS.md](../AGENTS.md), the repository-root [specification](../MOONMESH_TERRAIN_COVERAGE_TRAINING.md) and [ordered tasks](../docs/CODEX_PROMPTS.md). The existing `moonmesh` and `lunar_core` packages are deterministic reference physics. No trained model or pretrained weights are included or expected. This workspace currently provides configuration and path helpers only; pipeline scripts are planned below.

## Windows environment

Use Python 3.13 in the separate training venv. Create it only if it is missing:

```powershell
uv venv --python 3.13 C:\moonmesh-data\venv
```

Install the app dependencies, training dependencies and editable package together:

```powershell
uv pip install --python C:\moonmesh-data\venv\Scripts\python.exe -r requirements.txt -r requirements-training.txt -e .
& C:\moonmesh-data\venv\Scripts\python.exe -m pytest training
& C:\moonmesh-data\venv\Scripts\python.exe -m unittest discover -s tests -v
```

The editable installation exposes `moonmesh`, `lunar_core` and `moonmesh_train` from this checkout, including outside the repository working directory. Dependency installation remains explicit in the two requirements files; `requirements.txt` and the app launch commands are unchanged. Do not install training dependencies into `.moonmesh-data/venv` or run `scripts/start.ps1` for training setup. NumPy stays in the app's 2.x range. Rasterio's [official wheels](https://pypi.org/project/rasterio/) bundle GDAL; no separate GDAL or torch dependency is requested. The resolver also respects [Numba's NumPy/Python compatibility constraints](https://numba.readthedocs.io/en/stable/user/installing.html).

## Paths and configuration

`moonmesh_train.paths.data_root()` reads `MOONMESH_DATA_ROOT` each time, defaulting to `<repo>/data`. Relative environment values resolve against the repository; an empty value is an error. Merely importing the package or loading configs creates no directories or downloads. All pipeline scripts must use the path helpers, never embed a data path:

```python
from moonmesh_train.config import load_experiment
from moonmesh_train.paths import derived_dir, reports_dir

run = load_experiment("exp_b_haworth_pilot.yaml")
terrain_directory = derived_dir("terrain")
report_directory = reports_dir()
```

`raw_dir(source)`, `interim_dir()`, `derived_dir(kind)`, `models_dir()` and `reports_dir()` create just the requested directory. Pass `create=False` to resolve without creating. `ensure_data_dirs()` explicitly creates the complete empty layout:

```text
<MOONMESH_DATA_ROOT or repo/data>/
  raw/{lunarrf,pgda90,pgda78,lola_pds}/
  interim/
  derived/{terrain,labels,datasets}/
  models/
  reports/
```

Raw files are immutable. Reuse `.moonmesh-data` as a read-only source cache; do not duplicate its terrain or kernel downloads. Keep rasters, archives and model weights out of Git.

Both experiment YAMLs specify the requested Haworth pilot geometry and seed 42. `load_experiment()` resolves relative config names under `training/configs/` and attaches validated numeric radio settings under `radio`. `load_radio_profile()` rejects unknown profiles, missing/null fields and invalid numeric values. It validates only the selected profile, so the incomplete LunarRF entry does not prevent the app-reference pilot.

`moonmesh_2p4_ref` copies the surface-radio defaults from `moonmesh/models.py:40`, converts 2 W to dBm, and converts the deployment radius from line 27 to meters. Sensitivity applies at the app's 128 kbps reference rate. `lunarrf_1p8` intentionally contains nulls for everything except frequency. Experiment A therefore refuses to run until the LunarRF run metadata has been inspected and all TODO fields filled. The experiment's requested 8000 m range must not silently fill the unknown benchmark range. Experiment A's pilot geometry is a requested configuration, not a verified description of LunarRF's simulation setup.

## Planned pipeline

1. Inventory source metadata and checksums; reuse cached LOLA PDS files and acquire only authorized missing sources (Prompt 2).
2. Inspect and align lunar terrain/benchmark grids, projections, nodata and quality layers (Prompts 3-4).
3. Build and test curved-body per-transmitter LOS labels against the deterministic engine (Prompt 5).
4. Generate documented RF margins and dead-zone labels, always marked **simulated** (Prompt 6).
5. Compare Experiment A against LunarRF with matched, inspected metadata (Prompt 7).
6. Construct scenario sampling and leakage-safe spatial/transmitter splits (Prompt 8).
7. Assemble the dataset using only inference-available features (Prompt 9).
8. Train the first model from scratch with LightGBM; tune only on validation (Prompt 10).
9. Evaluate accuracy, unknowns, boundaries and end-to-end runtime; compare matched references (Prompts 11-12).
10. Optionally train a U-Net from scratch after the baseline is justified (Prompt 13; torch is not installed now).
11. Evaluate held-out Malapert and Nobile Rim 1 sites (Prompt 14).
12. Integrate validated maps into the app, then prepare the demo and final report (Prompts 15-16).

Every future script in `training/scripts/` must provide an argparse CLI with `--help`, read configuration from `training/configs/`, use the path helpers, seed stochastic work, log the git commit, and write reports under `reports_dir()`. Outputs and reports must identify simulated labels. Model features must exclude LunarRF output layers, IDs and full-trace LOS diagnostics. Never tune on the test split. No pipeline script or dataset download is run by this workspace setup.
