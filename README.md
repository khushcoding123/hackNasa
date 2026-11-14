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

1. Run the default Haworth mission for November 2026. This is the repository's research window.
2. Open **Sites** to compare Haworth, Nobile Rim 1 and Malapert Massif using the same period.
3. Open **Blackouts** and select a blackout start time to inspect the Earth elevation, terrain angle, RF margin and distance to the controlling horizon sample.
4. Set the availability, maximum blackout, mast, power and deployment constraints in **Expert settings**. Open **Relays** and optimize. The search returns the smallest screened configuration that meets the requirements, or the best evaluated configuration when the requirements are infeasible.
5. Open **Resilience** and disable a selected relay. Inspect the resulting timeline and the per-relay criticality table. Enable **Require N−1** and run again to optimize for the worst individual relay failure.
6. Run **Sensitivity analysis** or export the current mission. JSON includes the scenario, provenance and failure results. CSV includes the timeline and completed comparison or sensitivity runs. **Print report** opens a printable mission summary.

The interface follows **Mission → Sites → Blackouts → Relays → Resilience**. Sensitivity and Data & assumptions are secondary analysis tools. Configuration changes are labeled **Changes not applied** until you run the mission. Exports are in the header menu. See [UI_REDESIGN_PLAN.md](UI_REDESIGN_PLAN.md) for the visual and interaction specification.

## ORA relay lab

Open [the ORA lab](http://127.0.0.1:8765/ora.html) from the MOONMESH menu. ORA places relays for a lunar mining site on real terrain, tests the network against node failures, runs it through time as a digital twin, sizes batteries for the lunar night, and takes the same graph engine underground. See [docs/ORA.md](docs/ORA.md) for what is built and the measured results, and [docs/ORA_TRAINING.md](docs/ORA_TRAINING.md) for the training procedure and datasets.

```shell
python -m ora.data --earth
python -m ora.train
python -m ora --help
```

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
