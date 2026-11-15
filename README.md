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

## Demonstration

1. Open **Explore the Moon**. Rotate or zoom the globe, click anywhere to drop a red pin, then choose **Explore this site in 3D** from the pin card. Click an existing pin to reopen its card. The 3D view uses the best available LOLA terrain at that location, geographically aligned LROC WAC imagery, and polar NAC mosaics where covered. It labels the imagery and terrain source resolution, falls back to the bundled lunar map if live imagery fails, and starts at a low oblique angle with an **Overview** option. Its subtle close-up grain is illustrative, not measured relief; no mission runs automatically.
2. Choose **Set up my mission**, enter the region, UTC dates, and communication goals, then select **Calculate network**. Haworth in November 2026 is the reference research scenario.
3. **See your network** opens the calculated landing terrain in 3D. Inspect recommended towers, change the selected time, or switch to Whole Moon for context.
4. Continue to **Understand blackouts**. Select an outage to inspect terrain clearance, Earth geometry, and RF margin together.
5. Continue to **Test resilience**. Disable a selected relay and inspect coverage and blackout changes. Return to setup to change parameters; recalculate to unlock updated analysis.
6. Use **Optional analysis tools** for region comparison, individual tower details, sensitivity, and data sources. Export JSON, CSV, or a printable report from the header.

The guided flow and product priorities are documented in [PRODUCT_FLOW.md](PRODUCT_FLOW.md). The original visual direction remains in [UI_REDESIGN_PLAN.md](UI_REDESIGN_PLAN.md). The workspace is a full-screen **CesiumJS** Moon (1,737,400 m radius, no Earth imagery, atmosphere or Cesium ion). **Map** mode is orthographic with even lighting; **3D** adds perspective, tilt, true-scale (1×) terrain with vertex normals, terrain shadows and *Interactive lighting* — dragging sweeps a directional light across slopes. It is an exploration aid and never affects mission dates, solar geometry or communication results. Imagery streams as tiles from the public LROC WAC WMS (`luna_wac_global` in Map mode, `luna_wac_normalized_reflectance` in 3D, 100 m/pixel source limit); the bundled color map is an explicitly labelled lower-detail fallback if the service fails. Elevation comes from the read-only `GET /api/terrain/tile` endpoint (`/api/terrain/info` lists sources, units and region footprints): the global LOLA grid everywhere, LOLA 240 m and 20 m DEMs around Haworth, Nobile Rim 1 and Malapert Massif. The browser builds the Cesium quantized-mesh tiles and normals itself (`frontend/terrain-mesh.ts`). The Three.js site explorer requests a local grid through `GET /api/terrain/site`, including at the poles and longitude seam. Its view area follows terrain quality: 14 km for 20 m data, 90 km for 240 m data and 350 km for the global grid. Mission calculations and relay placement still require one of the three 20 m regions. Detail stops at each source's resolution and the status bar reports imagery, terrain and screen resolution separately. The site explorer draws one continuous terrain mesh (a finer centre grid merged into the wider one) and requests its imagery as local orthographic WMS images (`IAU2000:30166`; the server's `AUTO:42003` uses Earth's radius and would not match the terrain). Large images arrive as a quick preview and then full size, fetched as tiles with timeouts because the server's render time grows with image size; if a step fails, the previous imagery stays and the view offers a retry. Its imagery label gives the resolution actually shown, never finer than the source. `npm run build` bundles Cesium and copies its workers/assets to `web/cesium`. Tests: `npm run test:terrain`, `npm run test:site` and `python -m unittest tests.test_terrain_tiles`.

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

## Model and validation

- Terrain uses [LOLA PDS products](https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/) at 20 m locally and 240 m farther out. Horizon rays include lunar curvature, sample every 2° and extend up to 150 km. Missing terrain is treated as uncertain and excluded from credited availability.
- [JPL NAIF kernels](https://naif.jpl.nasa.gov/naif/data_generic.html) provide Earth and Sun center geometry in a DE440 mean-Earth frame aligned to DE421. The test suite compares Earth and Sun azimuth, elevation and range with saved [JPL Horizons](https://ssd-api.jpl.nasa.gov/doc/horizons.html) observer ephemerides for all three sites every 6 h over November 2026 and March 2027 (1,452 samples in `tests/data/horizons`). Elevations agree within 0.001° and azimuths within 0.006°. The remaining differences are expected: Horizons reports apparent DE441 values, so Sun azimuth includes about 0.006° of stellar aberration and Earth range is about 36 km longer from light-time correction. Run `python -m moonmesh.validation` to print the comparison, or add `--refresh` to download the references again.
- The sample locations are approximate points from the checked-in research, within regions discussed in [NASA's 2024 announcement](https://www.nasa.gov/news-release/nasa-provides-update-on-artemis-iii-moon-landing-regions/). The analysis uses the selected point and lets experts test shifts or enter custom coordinates within the local DEM window.
- The relay model estimates free-space RF feasibility, straight-line deployment, sampled surface line of sight and coupled charge/discharge, heater demand and thermal survival under explicit environmental assumptions. In the current baseline, each relay supplies its own lander → relay → Earth two-hop path; the planned satellite architecture adds a relay → satellite → Earth backhaul. Results depend on the assumed ground receiver, illumination and terrain sampling. N−1 removes one relay at a time and retains shared Earth receiver assumptions.
- [LunarRF](https://arxiv.org/abs/2610.34554) provides related work on pole-wide terrain-aware surface coverage. MOONMESH focuses on time-dependent DTE outages and site-specific relay planning.

```shell
python -m unittest discover -s tests -v
npm run build
```

The existing exploratory scripts and generated studies are in `analysis/`. The application prepares datasets for its three supported sites.
