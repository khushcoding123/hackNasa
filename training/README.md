# Terrain coverage training workspace

Follow [AGENTS.md](../AGENTS.md), the repository-root [specification](../MOONMESH_TERRAIN_COVERAGE_TRAINING.md) and [ordered tasks](../docs/CODEX_PROMPTS.md). The existing `moonmesh` and `lunar_core` packages are deterministic reference physics. No trained model or pretrained weights are included or expected. This workspace provides configuration, path helpers and source acquisition; later pipeline scripts are planned below.

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

## Source acquisition (Prompt 2)

Run downloads yourself with the training Python. All commands have `--help`:

```powershell
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/00_download.py --help
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/00_download.py lunarrf-meta
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/00_download.py lunarrf --parts context 1 --yes
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/00_download.py lunarrf --parts mosaics2 candidates validation
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/00_download.py sites
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/00_download.py extract-meta
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/00_download.py extract --site Haworth --band 1p800GHz
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/00_download.py pgda78 --site haworth
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/00_download.py lola-pds
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/00_download.py pgda90-window --bounds-from '<extracted raster path>' --buffer-m 10000
```

Settings and source URLs live in [configs/downloads.yaml](configs/downloads.yaml). Put `--config <path>` before the subcommand to override them. Downloads require `--yes` above 2,000,000,000 bytes or when HEAD cannot determine a size. `pgda78` prints the source folder; pass inspected companion names with `--files name1 name2`. No companion names are guessed.

Downloads use `.part` files, validate resumed HTTP ranges and verify published SHA256 checksums before promoting the file. If the server ignores Range, the partial restarts from zero. Completed raw files are reused and never overwritten. A checksum mismatch leaves the partial for inspection; inspect or remove that partial yourself before retrying. `lunarrf` requires `lunarrf-meta` first. Unlisted checksums are recorded as `verified: "unverified"`, with the instruction to compare against the Zenodo record page. All LunarRF labels are **simulated**.

`mosaics2` downloads only `lunarrf_atlas_mosaics_160m_part2_v2.0.tar`, which contains run metadata and exact statistics; mosaics part 1 is not offered. `candidates` downloads `lunarrf_candidates_v2.0.tar`, and `validation` downloads `lunarrf_validation_v2.0.tar`. These use the same size, resume, checksum and manifest rules as the existing parts.

`sites` reads local `raw/lunarrf/MANIFEST.txt` without opening or downloading tars. It prints exact site names (such as `Malapert_Massif` and `Nobile_Rim_1`) and file counts per region archive, including separately named siderun archives. A site's files may span parts: use these counts when selecting downloads.

Extraction lists all available configured archives before writing selected files under `interim/lunarrf/<Site>/`, preserving their internal paths and the site's case. It includes the site's `<Site>__dem.tif` and `<Site>__psr.tif` from the context tar, plus all context metadata files. `extract-meta` scans every completed `lunarrf*.tar` in `raw/lunarrf/` (including any manually downloaded archives) and extracts `.json`, `.csv`, `.md`, `.txt`, `.yaml`, `.yml` and `.xml` files into `interim/lunarrf/meta/`. It also includes `.png` and `.pdf` figures from the validation archive. Both commands preserve internal directories, reject links and unsafe paths, reuse identical files, and refuse conflicting output files. Neither changes archives; incomplete `.tar.part` downloads are ignored.

PGDA90 checks HTTP Range support, transforms reference bounds into the DEM's projected CRS, adds the metre buffer, rounds outward to pixels and clips to the source extent. Only that window is read, in blocks, into `interim/pgda90/`; the report records its actual window. Range failure stops with the full source size when available and instructions; it never falls back to a full download.

`lola-pds` registers existing `.moonmesh-data/terrain/` paths without copying. Missing files are acquired through `moonmesh.data.download` into the training data root's `raw/lola_pds/`, leaving the app cache read-only. PDS product versions are read from the companion `.LBL`; unknown versions stay null with a TODO.

[data_manifest.json](data_manifest.json) contains a versioned `entries` list, updated atomically after each completed acquisition. Every file has a unique `source_id`, such as `lunarrf_context_v2.0`, `pgda78_haworth_5m` or `lola_pds_ldem_85s_20m_img`. Entries are updated by absolute local path; repeated registration retains a known acquisition time, including when replacing an old family-wide ID. An ID already assigned to a different path is rejected. Existing cache files without an earlier manifest timestamp use `downloaded_utc: null` and a TODO rather than inventing a download date. PGDA versions and unchecked licenses remain marked for catalog inspection. Computing a local hash does not mark an acquisition publisher-verified. Run one acquisition process at a time; concurrent manifest writers are not supported. Each executed command writes a timestamped JSON report under the configured data root's `reports/`, including the git commit, seed, messages and failures. `--help` has no data side effects.

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

### Terrain-only transmitter sampling and spatial splits (step 08)

Run with the training Python and `MOONMESH_DATA_ROOT` set:

```powershell
C:/moonmesh-data/venv/Scripts/python.exe training/scripts/06_sample_transmitters.py --help
C:/moonmesh-data/venv/Scripts/python.exe training/scripts/06_sample_transmitters.py
C:/moonmesh-data/venv/Scripts/python.exe training/scripts/07_make_splits.py --help
C:/moonmesh-data/venv/Scripts/python.exe training/scripts/07_make_splits.py
```

The exp_b config fixes disjoint terrain strata: steep wall first (slope >=20
 degrees), then crest (relative elevation >=15 m), shoulder (>=0 m), floor
(<=-15 m), and plain otherwise. These are sampling rules chosen from the
observed terrain distribution, not reviewed semantic landform labels. Each
stratum contributes 30 placements, with global 500 m spacing. A conservative
Euclidean distance transform excludes raster edges and nodata holes within
8 km plus half a pixel diagonal. Deployability uses the app's slope limit and
the configured 8 km research-point radius; it does not restrict sampling.

Occupied blocks use a global 5 km lattice anchored at projected (0,0).
Seeded contiguous block slabs approximate 70/15/15 before buffering. Among
256 seeded orientations, select the one retaining most placements while
meeting minimum counts, using coordinates only. Ties keep the first layout.
The validation/test block allocations adjoin to reduce the training boundary.
No coverage labels or model scores inform sampling or split selection.
Dropped training placements remain in the manifest for audit, and must not
be used for training. Scenario variants inherit their transmitter's split.

Outputs: `training/splits.json`, and data-root `reports/splits/` containing
`transmitters.json`, `counts.json`, `split_map.png`, and `report.md`.
The report estimates residual disk-union overlap at 100 m resolution: an
8 km transmitter buffer does not imply disjoint 8 km receiver disks.
Cross-site manifests stay empty until step 14. Reruns are deterministic and
small, so interrupted output generation can be safely repeated.
