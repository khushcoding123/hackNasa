# ORA training prompts

A series of prompts, one per remaining model or data task. Each is self-contained: paste it to a coding agent opened in the repo root. Do them in order; prompts 1 and 2 matter most because the siting score feeds the relay optimizer.

Shared rules for every prompt:

- Python is `.moonmesh-data/venv/Scripts/python.exe`. Run the tests with `-m pytest tests -q` before and after; all must pass.
- Read `docs/ORA_TRAINING.md` and `ora/model_card.json` first. Do not change the seed (0) or the cross-validation scheme: one whole region or site is held out per fold.
- Report every metric as measured, including when the new model is worse. Update `ora/model_card.json` through `python -m ora.train`, never by hand.
- Do not invent data, units or citations. If a source cannot be downloaded or read, say so and stop that step.

---

## 1. Measure the siting benefit on a held-out lunar site

> In `ora/optimize.py` and `ora/scenario.py`, relay sites are chosen with help from the siting prior and the coverage surrogate. Write `ora/benchmark.py` with a command `python -m ora benchmark [--site haworth] [--budget 8] [--seeds 20]`.
>
> Hold one lunar site out (the surrogate must not have been trained on it; retrain with that site excluded if needed). Choose `budget` relay sites with four strategies: random, greedy by exact viewshed coverage, greedy by surrogate score, greedy by siting-prior score. Report for each: exact viewshed coverage gained, number of line-of-sight links, and mean over `seeds` random draws with 95% confidence intervals.
>
> Write the table to `docs/figures/ora_benchmark.json` and an SVG with `ora.export.line_chart_svg`. Add a test that the exact-greedy strategy never does worse than random on a small synthetic terrain. State plainly in `docs/ORA_TRAINING.md` whether the surrogate beats random and how close it gets to exact greedy.

## 2. Improve the coverage surrogate

> The coverage surrogate in `ora/train.py` has R² 0.18 and Spearman 0.61 on held-out lunar sites. Improve it without changing the evaluation.
>
> Steps: (a) add lunar sites to `ORA_SITES` in `ora/data.py` (Haworth, Malapert and Connecting Ridge windows already exist in `.moonmesh-data/terrain`) and raise samples per site; (b) add features: horizon-profile summaries in eight azimuth sectors, mean and maximum horizon elevation, Sun and Earth visible fractions from `ora/earth_vis.py`; (c) compare HistGradientBoosting against a random forest and a ridge baseline; (d) keep leave-one-site-out CV.
>
> Record each variant's per-fold R², Spearman and mean absolute error in the model card. Keep the best only if it beats the current Spearman on the mean of held-out folds. Retrain, and confirm `python -m ora network` still runs.

## 3. Strengthen the Earth siting prior

> The siting prior (`ora/train.py`) scores mean AUC 0.84 and beats the view-only baseline in four of six regions. Add more Earth tiles to `EARTH_TILES` in `ora/data.py`, choosing low-population mountain regions in the western US (for example Idaho, Montana, Utah and Nevada ranges) so roads and towns pull less on the labels. Download with `python -m ora.data --earth`.
>
> Then: (a) retrain with leave-one-tile-out CV; (b) add a feature-importance table to the model card; (c) test whether adding distance-to-road proxies hurts or helps transfer (use only data already downloaded; if none, skip and say so); (d) report AUC, average precision and top-10% recall per fold against the view-only baseline.
>
> Do not claim the model transfers to the Moon. State in the model card that it was trained on Earth towers and used on the Moon as a terrain-shape prior only.

## 4. Reproduce the Tall Lunar Towers benchmark

> `papers/Tall_Lunar_Towers_ASCEND_2023.pdf` in the team dataset folder (`Desktop/datasetsnetworknodes`) reports viewshed results at Connecting Ridge. Read the paper, extract the stated tower heights, observer and target heights and the coverage figures, and quote only numbers actually in the paper.
>
> Using `ora/los.py` (`viewshed_fraction`) and the `connecting-ridge` terrain window, compute the same quantities at the same heights. Write `scenarios/tall_towers_benchmark.yaml` and a test or script that prints our value next to the paper's. Report the difference honestly and list likely causes (resolution, curvature handling, site definition) without claiming agreement that is not there.

## 5. Fit the tunnel radio model to measurements

> `ora/tunnel.py` uses a free-space plus waveguide plus bend-loss model with placeholder constants (`TunnelRadio`: `ghz`, `alpha_db_per_100m`, `bend_db_per_90deg`). Replace placeholders with fitted values.
>
> Option A (no hardware): download the UWB mine-tunnel measurements (https://doi.org/10.5281/zenodo.18835598), check the licence and file format, and fit the model with `hardware/fit_tunnel_model.py` (extend it to read this dataset). Option B: compile and flash `hardware/esp32_rssi_logger` with PlatformIO, log RSSI against distance in a corridor, and fit with the same script.
>
> Report fitted constants with confidence intervals and residuals, and the frequency band they apply to. Update `scenarios/mine_tunnel_demo.yaml`. If neither dataset can be used, say so and leave the placeholders marked as placeholders.

## 6. Load a real mine or cave graph

> `Mine.from_survey` in `ora/tunnel.py` builds a tunnel graph from a survey. Try the ETH Hagerbach dataset (https://doi.org/10.3929/ethz-b-000425328) and the Wadi Michmash cave survey (https://doi.org/10.5281/zenodo.20410901). Check each one's format and licence first. Convert whichever loads into a `Mine`, add a scenario file under `scenarios/`, and run `python -m ora tunnel --layout` equivalents on it with the breadcrumb policies in `ora/breadcrumb.py`.
>
> Report how many repeaters each policy needs and whether the resilience-aware policy still survives any single repeater failure on the real graph. If the format cannot be read, document exactly why.

## 7. Fill in the module catalogue

> `ora/modules.yaml` holds placeholder mass and power values for the node configurator in `ora/configure.py`. Replace each with a sourced value (datasheets, NASA technical reports, or a stated engineering assumption). Add a `source` field per entry. Mark any value without a source as `assumed: true`, and make `python -m ora parts` print a warning when assumed values are used. Do not guess a value and present it as sourced.

## 8. Use the LuGRE lunar GNSS data (optional)

> `datasets/LuGRE_lunar_GNSS/LuGRE.zip` (CC BY 4.0) holds measured GPS and Galileo signals at the Moon. Unzip it into its own empty folder, read its documentation, and report what signal-strength and availability statistics it contains. Then propose, in one page in `docs/ORA.md`, whether it can support a timing layer for duty-cycled relays. Do not feed it into the surface link model; it does not describe surface radio links.

## 9. Khush's terrain coverage training task

> Read `docs/CODEX_PROMPTS.md` and the terrain coverage training spec that arrived on `main` in commit dc92dd9. Do the first unfinished prompt only. Before coding, list how it overlaps with the ORA coverage surrogate in `ora/train.py` so the two models are not trained twice on the same task.

---

## After all prompts

- Run `python -m pytest tests -q`.
- Run `python -m ora train`-equivalent (`python -m ora.train`) once more from a clean state and confirm the model card date changes and the app's Models & limits tab shows the new numbers.
- Commit on `jeswanth/ora-lab`. Do not merge `UI` from Ayaan until his page and the ORA page are reconciled (`web/index.html`, `web/style.css` and `moonmesh/server.py` conflict).
