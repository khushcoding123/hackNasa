# Next steps for an environment with data and network access

Written 15 Nov 2026 from a cloud session that had no `data/` folder, no training venv and a proxy blocking `ntrs.nasa.gov` and `www-robotics.jpl.nasa.gov`. Follow [AGENTS.md](../AGENTS.md) (no `git commit`/`git push` by agents unless the user grants it; Windows paths; training venv at `C:\moonmesh-data\venv`).

## Already done on branch `claude/repo-task-review-7s8lqh`

- Underground-to-surface radio link: `training/src/moonmesh_train/subsurface.py`, `subsurface_model.py`, `training/configs/subsurface.yaml`, `training/scripts/20_train_subsurface.py`, `training/tests/test_subsurface.py` (9 tests pass), `docs/SUBSURFACE_LINK.md`. All labels simulated.
- PRP citation resolved in `docs/HAZARD_RESPONSE.md` (PDS bundle citation, doi:10.17189/wj0s-w188; no licence text in the record).

## 1. Setup and verification

- [ ] Pull the branch.
- [ ] Create the training venv if missing: `uv venv --python 3.13 C:\moonmesh-data\venv`, then `uv pip install --python C:\moonmesh-data\venv\Scripts\python.exe -r requirements.txt -r requirements-training.txt -e .`
- [ ] Run `C:\moonmesh-data\venv\Scripts\python.exe -m pytest training` (only the 9 subsurface tests were run so far).
- [ ] Run `python -m unittest discover -s tests -v` (15 of 17 errored in the cloud container from missing dependencies such as `fastapi`; unverified on a full environment).
- [ ] Optionally run `training/scripts/20_train_subsurface.py` (first result: in-distribution MAE 2.85 dB; extrapolation to 40-80 m basalt MAE 7.5 dB with +6.5 dB optimistic bias).

## 2. Subsurface model: values to verify (none are confirmed)

- [ ] Regolith formulas in `subsurface.yaml` (eps' = 1.919^density; tan_delta = 10^(0.038*(FeO+TiO2) + 0.312*density - 3.260)) came from memory of Carrier et al. 1991, Lunar Sourcebook ch. 9. Confirm against the text.
- [ ] Basalt eps' 6-9 and tan_delta 0.003-0.03 are assumed ranges. Find and cite Apollo sample measurements.
- [ ] Replace placeholder radio values (30 dBm, 2 dBi antennas, -105 dBm sensitivity) with the real hardware.
- [ ] Decide what "lunar one storms" meant. I assumed a LunarRF-style simulated-label model for this link. LunarRF itself covers surface links only.

## 3. Prompt 12: published benchmarks (blocked by network)

- [ ] Download the four PDFs into `docs/references/` (sources are in the table near the top of `docs/CODEX_PROMPTS.md`):
  - `tall_lunar_towers_20230012634.pdf`
  - `nasa_viewshed_trade_20230014675.pdf`
  - `jpl_rapid_low_cost_path.pdf`
  - `nasa_nokia_surface_network_20240003815.pdf`
- [ ] Then run Prompt 12: write `data/reports/benchmarks/configs.md` (NOT STATED where absent) and `training/scripts/11_benchmarks.py`. Fetch extra PGDA 90 windows through `00_download.py`.
- [ ] Label results as simulator-to-simulator consistency, not validation.

## 4. Prompt 14: held-out sites (needs large downloads)

- [ ] `00_download.py lunarrf --parts 2 3 --yes`, then extract Malapert and Nobile (check the exact site names in `MANIFEST.txt`), then `pgda78 --site malapert` and `pgda78 --site nobile-rim-1`.
- [ ] Inventory, alignment, transmitter sampling, labels, cross-site evaluation and the two split rotations in `splits.json`. Report per-site metrics.

## 5. Diviner PCP ingestion (hazard question 2: user said yes)

- [ ] Reachable at `https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_pcp/` (`diurnal/` and `seasonal/`; about 200 MB per bin file, 192 local-time bins per season, 59-byte ASCII rows per `docs/HAZARD_RESPONSE.md`). Several GB in total.
- [ ] No script written yet. Inspect a real label/row layout first, then build a night-minimum target. Do not guess the format.

## 6. Reports and housekeeping

- [ ] Regenerate `ora_evaluation.json`, `ora_stress.json`, `ora_benchmark.json`, `ora_tall_towers.json` and `ora_headline.json` (they predate provenance stamping), and add artifact hashes to the ORA model card (`python -m ora.train` or regenerate the reports).
- [ ] Optional: raw-data check of near-pole PRP hazard labels (median maximum about 285 K; unverified against raw Diviner data).
- [ ] CI: a `MOONMESH_REQUIRE_DATA=1` job only makes sense on a runner that has the data (a clean GitHub clone has none).
- [ ] Replace placeholder hardware values in `ora/modules.yaml` and `ora/thermal.py`.
- [ ] Fill licence TODOs for PGDA 90, PGDA 78 and LOLA PDS, and the PGDA vertical reference.
- [ ] Prompt 13 (U-Net): only if LightGBM fails the boundary criteria and deterministic code is too slow. The MODEL_CARD says the learned predictor was rejected, so likely skip.
- [ ] Prompt 16 close-out: finish the MODEL_CARD final decision and the spec section 5 acceptance audit once benchmarks and held-out sites exist.

## 7. Website

- [ ] The Explore-panel declutter was tried and reverted at the user's request.
- [ ] Result pages (Layout, Earth link, Blackouts, Resilience, Cost, Sites, Relays, Sensitivity, Data, ORA tabs) were never viewed. They need the backend and mission data to render.
- [ ] Ideas not applied: hide the greyed-out nav steps before a mission exists; drop the landing status strip.
