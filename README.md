# MOONMESH

MOONMESH evaluates landing-point sunlight, direct-to-Earth communication and solar-powered surface relay options using NASA LOLA elevation data and JPL DE440 SPICE ephemerides.

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

1. Open **Explore the Moon**. Rotate or zoom the globe; no mission runs automatically.
2. Choose **Set up my mission**, enter the region, UTC dates, and communication goals, then select **Calculate network**. Haworth in November 2026 is the reference research scenario.
3. **See your network** opens the calculated landing terrain in 3D. Inspect recommended towers, change the selected time, or switch to Whole Moon for context.
4. Continue to **Understand blackouts**. Select an outage to inspect terrain clearance, Earth geometry, and RF margin together.
5. Continue to **Test resilience**. Disable a selected relay and inspect coverage and blackout changes. Return to setup to change parameters; recalculate to unlock updated analysis.
6. Use **Optional analysis tools** for region comparison, individual tower details, sensitivity, and data sources. Export JSON, CSV, or a printable report from the header.

The guided flow and product priorities are documented in [PRODUCT_FLOW.md](PRODUCT_FLOW.md). The original visual direction remains in [UI_REDESIGN_PLAN.md](UI_REDESIGN_PLAN.md). The workspace is a full-screen **CesiumJS** Moon (1,737,400 m radius, no Earth imagery, atmosphere or Cesium ion). **Map** mode is orthographic with even lighting; **3D** adds perspective, tilt, true-scale (1×) terrain with vertex normals, terrain shadows and *Interactive lighting* — dragging sweeps a directional light across slopes. It is an exploration aid and never affects mission dates, solar geometry or communication results. Imagery streams as tiles from the public LROC WAC WMS (`luna_wac_global` in Map mode, `luna_wac_normalized_reflectance` in 3D, 100 m/pixel source limit); the bundled color map is an explicitly labelled lower-detail fallback if the service fails. Elevation comes from the read-only `GET /api/terrain/tile` endpoint (`/api/terrain/info` lists sources, units and region footprints): the global LOLA grid everywhere, LOLA 240 m and 20 m DEMs around Haworth, Nobile Rim 1 and Malapert Massif. The browser builds the Cesium quantized-mesh tiles and normals itself (`frontend/terrain-mesh.ts`). Detail stops at each source's resolution and the status bar reports imagery, terrain and screen resolution separately. `npm run build` bundles Cesium and copies its workers/assets to `web/cesium`. Tests: `npm run test:terrain` and `python -m unittest tests.test_terrain_tiles`.

## Model and validation

- Terrain uses [LOLA PDS products](https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/) at 20 m locally and 240 m farther out. Horizon rays include lunar curvature, sample every 2° and extend up to 150 km. Missing terrain is treated as uncertain and excluded from credited availability.
- [JPL NAIF kernels](https://naif.jpl.nasa.gov/naif/data_generic.html) provide Earth and Sun center geometry in a DE440 mean-Earth frame aligned to DE421. The test suite compares Earth and Sun azimuth, elevation and range with saved [JPL Horizons](https://ssd-api.jpl.nasa.gov/doc/horizons.html) observer ephemerides for all three sites every 6 h over November 2026 and March 2027 (1,452 samples in `tests/data/horizons`). Elevations agree within 0.001° and azimuths within 0.006°. The remaining differences are expected: Horizons reports apparent DE441 values, so Sun azimuth includes about 0.006° of stellar aberration and Earth range is about 36 km longer from light-time correction. Run `python -m moonmesh.validation` to print the comparison, or add `--refresh` to download the references again.
- The sample locations are approximate points from the checked-in research, within regions discussed in [NASA's 2024 announcement](https://www.nasa.gov/news-release/nasa-provides-update-on-artemis-iii-moon-landing-regions/). The analysis uses the selected point and lets experts test shifts or enter custom coordinates within the local DEM window.
- The relay model estimates free-space RF feasibility, straight-line deployment, sampled surface line of sight and charge/discharge at constant load. Each relay supplies its own two-hop path. Results depend on the assumed ground receiver, illumination and terrain sampling. N−1 removes one relay at a time and retains shared Earth receiver assumptions.
- [LunarRF](https://arxiv.org/abs/2610.34554) provides related work on pole-wide terrain-aware surface coverage. MOONMESH focuses on time-dependent DTE outages and site-specific relay planning.

```shell
python -m unittest discover -s tests -v
npm run build
```

The existing exploratory scripts and generated studies are in `analysis/`. The application prepares datasets for its three supported sites.
