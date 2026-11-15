# MOONMESH

MOONMESH is a lunar communication planning product targeting **lander → surface relay → lunar communications satellite → Earth**. It will place surface relays against terrain, satellite access, power, blackout, and resilience constraints. NASA LOLA elevation data and JPL DE440 SPICE ephemerides support the existing terrain and Earth/Sun geometry.

**Current prototype:** the planner calculates direct-to-Earth service and lander → surface relay → Earth paths. Satellite orbits and satellite-assisted links are planned and are not included in current results. See [Communication architecture](docs/COMMUNICATION_ARCHITECTURE.md) for the target, modeling requirements, and implementation boundary.

## Run locally

On Windows with Node.js and uv installed, run this from the repository root.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

Open [MOONMESH](http://127.0.0.1:8765/). The first setup downloads about 535 MB into the ignored `.moonmesh-data` directory. Subsequent runs use the local source files.

To set up manually on other platforms, install the Python dependencies from `requirements.txt` into a Python 3.11+ environment, then run the following.

```shell
npm ci
npm run build
python -m moonmesh.data
python -m uvicorn moonmesh.server:app --host 127.0.0.1 --port 8765
```

## One command to get a working demo

```shell
python -m moonmesh.setup           # checks packages, NASA terrain and kernels, the built UI, trained models, and runs a short real mission
python -m moonmesh.setup --apply   # also installs missing packages, downloads missing terrain/kernels, builds the UI
```

It never needs the trained models to produce results (relay ranking is the computed viewshed); a missing model is reported as a warning with its rebuild command, and `--apply --train` rebuilds them (slow). Exit code 1 means the app cannot run, and every line marked MISS comes with its fix. The preflight now checks every package in `requirements.txt`, so a package the server imports at start-up (for example XlsxWriter) can no longer hide behind a READY status.

## Check what this checkout has

```shell
python -m ora preflight            # data, trained models actually loaded, library versions, code revision
python -m ora preflight --json     # the same, machine readable (also GET /api/status)
```

`python -m ora accuracy` scores the coverage calculation and the models against dense line-of-sight truth on windows never used for tuning; [docs/ACCURACY.md](docs/ACCURACY.md) has the results.

The status is `ready`, `degraded` (the app runs, but a research model failed to load, or optional inputs or test dependencies are missing) or `not_ready` (required terrain or kernels are missing; exit code 1). The model card in `ora/model_card.json` is a training record, not proof that a model is loaded; the preflight and the ORA lab's *Models & limits* tab show what is loaded now, with file hashes. [docs/BASELINE_STATUS.md](docs/BASELINE_STATUS.md) is the baseline report for this checkout.

## Result status

Every mission, relay plan and twin run carries an `assessment` with one of three states, shown in the app and in every export:

- **Requirements met**: every requirement check passes, and it still passes with marginal samples (within 3 dB of the RF threshold or 0.25° of the horizon) counted as lost.
- **Requirements failed**: a check fails even when samples with unknown terrain are counted as available.
- **Insufficient evidence**: the data cannot decide it: the answer changes with marginal or missing samples, or the terrain is coarser than 20 m.

Every status is decided *under the assumptions listed with it*: radio budgets, batteries, and the assumed thermal sinks and dust event. Those thermal and dust values are planning envelopes, not calibrated measurements, and each result says so. Mission results take the worst of the four assumed environment cases (nominal, cold, dust, cold with dust); digital-twin results are scenario outcomes under the twin's equipment values.

Each assessment names what it covers (`subject`), whether it is a deterministic *calculated window* or a *scenario outcome*, the hazards evaluated, the hazards **not** evaluated (equipment cold limits, dust, space weather, ground-station scheduling, failure rates, orbiting relays) and the assumed parameters. None of it is a forecast or a failure probability.

## Demonstration

1. Open **Explore the Moon**. Rotate or zoom the globe, click anywhere to drop a red pin, then choose **Explore this site in 3D** from the pin card. Click an existing pin to reopen its card. The 3D view uses the best available LOLA terrain at that location, geographically aligned LROC WAC imagery, and polar NAC mosaics where covered. It labels the imagery and terrain source resolution, falls back to the bundled lunar map if live imagery fails, and starts at a low oblique angle with an **Overview** option. Its subtle close-up grain is illustrative, not measured relief; no mission runs automatically.
2. Choose **Set up my mission**, enter the region, UTC dates, and communication goals, then select **Calculate network**. Haworth in November 2026 is the reference research scenario.
3. **See your network** opens the calculated landing terrain in 3D. Inspect recommended towers, change the selected time, or switch to Whole Moon for context.
4. Continue to **Understand blackouts**. Select an outage to inspect terrain clearance, Earth geometry, and RF margin together.
5. Continue to **Test resilience**. Disable a selected relay and inspect coverage and blackout changes. Return to setup to change parameters; recalculate to unlock updated analysis.
6. Use **Optional analysis tools** for region comparison, individual tower details, sensitivity, and data sources. Export JSON, CSV, or a printable report from the header.

The guided flow and product priorities are documented in [PRODUCT_FLOW.md](PRODUCT_FLOW.md). The original visual direction remains in [UI_REDESIGN_PLAN.md](UI_REDESIGN_PLAN.md). The workspace is a full-screen **CesiumJS** Moon (1,737,400 m radius, no Earth imagery, atmosphere or Cesium ion). **Map** mode is orthographic with even lighting; **3D** adds perspective, tilt, true-scale (1×) terrain with vertex normals, terrain shadows and *Interactive lighting* — dragging sweeps a directional light across slopes. It is an exploration aid and never affects mission dates, solar geometry or communication results. Imagery streams as tiles from the public LROC WAC WMS (`luna_wac_global` in Map mode, `luna_wac_normalized_reflectance` in 3D, 100 m/pixel source limit); the bundled color map is an explicitly labelled lower-detail fallback if the service fails. Elevation comes from the read-only `GET /api/terrain/tile` endpoint (`/api/terrain/info` lists sources, units and region footprints): the global LOLA grid everywhere, LOLA 240 m and 20 m DEMs around Haworth, Nobile Rim 1 and Malapert Massif. The browser builds the Cesium quantized-mesh tiles and normals itself (`frontend/terrain-mesh.ts`). The Three.js site explorer requests a local grid through `GET /api/terrain/site`, including at the poles and longitude seam. Its view area follows terrain quality: 14 km for 20 m data, 90 km for 240 m data and 350 km for the global grid. Mission calculations and relay placement still require one of the three 20 m regions. Detail stops at each source's resolution and the status bar reports imagery, terrain and screen resolution separately. `npm run build` bundles Cesium and copies its workers/assets to `web/cesium`. Tests: `npm run test:terrain` and `python -m unittest tests.test_terrain_tiles`.

## ORA relay lab

Open [the ORA lab](http://127.0.0.1:8765/ora.html) from the MOONMESH menu. ORA places relays for a lunar mining site on real terrain, tests the network against node failures, runs it through time as a digital twin, sizes batteries for the lunar night, and takes the same graph engine underground. See [docs/ORA.md](docs/ORA.md) for what is built and the measured results, and [docs/ORA_TRAINING.md](docs/ORA_TRAINING.md) for the training procedure and datasets.

```shell
python -m ora.data --earth
python -m ora.train
python -m ora --help
```

## Night temperature and dust checks

The main planner evaluates nominal, cold-night, dust-exposure and combined cases. Each relay and the lander must have power and thermal survival before a communication interval counts. All four cases feed relay selection and optional N−1 checks. The combined case drives the main timeline; the case table shows the other outcomes. Open **Expert settings → Night temperatures and dust exposure** to configure thermal sinks, hardware heat losses, heater power, lander battery and the local dust event. Dust accumulates during the event, persists until cleaned and reduces solar generation. RF propagation does not receive an unsupported dust penalty.

These are assumed engineering envelopes: no Diviner temperature products or calibrated hardware measurements are loaded. The results identify modeled limits met under assumptions, rather than validated survival. JSON/CSV exports include case outcomes and per-node temperature, charge, heater, dust and availability histories. See [the implementation plan and status](NIGHT_TEMPERATURE_DUST_PLAN.md) for scope and remaining calibration work. Run `python -m unittest tests.test_environment -v` for the environmental regression tests.

## Dynamic cost model

The **Cost** tab prices the network from what it is made of, instead of one price for every node ([`moonmesh/costmodel.py`](moonmesh/costmodel.py)). The flat estimate ($100,000 per node, `moonmesh/costs.py`) is still shown as a reference and still limits the optimizer's budget; the dynamic figure never changes which relays are chosen.

| Driver | What it changes |
| --- | --- |
| Number of nodes and their role | Every node is priced separately; a gateway adds Earth-link communications cost. Fixed programme costs are shared, so cost per node falls as the network grows. |
| Longest link | Radios and antennas scale with the square root of the longest link (floor 0.75×, cap 3×). |
| Traverse distance, slope, route check | Installation grows per kilometre carried and with slope, plus an uplift for routes that cross steep or unverified ground. |
| Mast height, battery, solar, heater | Structure scales with mast height^1.5; batteries, solar and thermal with their sizing. |
| Area | Site survey per km² (with a floor); only for a drawn area. |
| Mission length | Contingency grows with days up to a cap, plus a bump for steep or unverified routes. |
| Relay satellite (optional) | Altitude sets orbital speed (√(μ/r), with the gravity you choose), the insertion burn (√(v∞² + 2μ/r) − √(μ/r)) and station-keeping. The rocket equation turns Δv into propellant and delivered mass. The elevation mask and beam width set how much of the time one satellite sees the area, which sets how many are needed for the coverage target (best case, capped at 12). |

A node at the reference conditions (5 m mast, 2 km link, 288 Wh, 18 W, 4 W heater, no traverse) costs exactly the flat $100,000. **Every coefficient is an illustrative planning value, not a quote or a market price**: they are listed in the response, in the workbook's *Cost Coefficients* sheet and in `Coefficients`, and any can be overridden (`coefficients` in the request). What the model adds is how the cost moves when the scenario changes, not a more certain total. It excludes the lander, launch of the surface payload and ongoing surface operations.

`POST /api/cost/estimate` takes `{result, area?, scope, satellite?, coefficients?}`. The satellite uses the orbit controls (altitude, gravity, track heading, beam, mask) plus a coverage target. The Excel download adds **Dynamic Cost** (every line with its driver, editable unit prices, formulas and category subtotals), **Cost by Node**, **Cost Drivers** (including the satellite derivation) and **Cost Coefficients**, and the Summary shows both totals. Run `python -m pytest tests/test_costmodel.py`.

## Model and validation

- Terrain uses [LOLA PDS products](https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/) at 20 m locally and 240 m farther out. Horizon rays include lunar curvature, sample every 2° and extend up to 150 km. Missing terrain is treated as uncertain and excluded from credited availability.
- [JPL NAIF kernels](https://naif.jpl.nasa.gov/naif/data_generic.html) provide Earth and Sun center geometry in a DE440 mean-Earth frame aligned to DE421. The test suite compares Earth and Sun azimuth, elevation and range with saved [JPL Horizons](https://ssd-api.jpl.nasa.gov/doc/horizons.html) observer ephemerides for all three sites every 6 h over November 2026 and March 2027 (1,452 samples in `tests/data/horizons`). Elevations agree within 0.001° and azimuths within 0.006°. The remaining differences are expected: Horizons reports apparent DE441 values, so Sun azimuth includes about 0.006° of stellar aberration and Earth range is about 36 km longer from light-time correction. Run `python -m moonmesh.validation` to print the comparison, or add `--refresh` to download the references again.
- The sample locations are approximate points from the checked-in research, within regions discussed in [NASA's 2024 announcement](https://www.nasa.gov/news-release/nasa-provides-update-on-artemis-iii-moon-landing-regions/). The analysis uses the selected point and lets experts test shifts or enter custom coordinates within the local DEM window.
- The relay model estimates free-space RF feasibility, straight-line deployment, sampled surface line of sight and coupled charge/discharge, heater demand and thermal survival under explicit environmental assumptions. In the current baseline, each relay supplies its own lander → relay → Earth two-hop path; the planned satellite architecture adds a relay → satellite → Earth backhaul. Results depend on the assumed ground receiver, illumination and terrain sampling. N−1 removes one relay at a time and retains shared Earth receiver assumptions.
- [LunarRF](https://arxiv.org/abs/2610.34554) provides related work on pole-wide terrain-aware surface coverage. MOONMESH focuses on time-dependent DTE outages and site-specific relay planning.

```shell
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
MOONMESH_REQUIRE_DATA=1 python -m pytest tests -q   # release check: a test skipped for missing data fails instead
npm run build                                       # type-checks and bundles the frontend
npm run test:terrain
```

Some tests use the FastAPI test client, which needs `httpx` (in `requirements-dev.txt`).

The existing exploratory scripts and generated studies are in `analysis/`. The application prepares datasets for its three supported sites.
