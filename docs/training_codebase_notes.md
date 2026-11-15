# Training codebase notes

Source inspection at commit `19d0cbeab8ad5b61d2f7eab52f6f0698e48f6e3e`. Line numbers below refer to the unchanged source files at that commit. The repository-root [training specification](../MOONMESH_TERRAIN_COVERAGE_TRAINING.md) governs future work; [CODEX_PROMPTS.md](CODEX_PROMPTS.md) lists the tasks. These notes describe implemented behavior, not independently verified source-product metadata.

Read in full: the specification, README.md, scripts/start.ps1, requirements.txt, moonmesh/models.py, moonmesh/data.py, moonmesh/science.py, moonmesh/network.py, lunar_core/terrain.py, lunar_core/horizon.py, lunar_core/geometry.py, lunar_core/visibility.py and tests/test_moonmesh.py. Also inspected lunar_core/kernels.py to resolve the download URLs imported by data.py.

The existing engine is deterministic physics. There is no trained model to reuse. Future labels from this engine and LunarRF must be marked **simulated**.

## Surface-to-surface chord

`surface_path(grid, first, second)` is in `moonmesh/science.py:82-95`. Its endpoints are `Observer` instances (`science.py:21-26`) with latitude, longitude, ground elevation and mast height.

- Lines 83-87 compute the initial great-circle bearing and central angle using the spherical cosine rule, clipping its cosine to [-1, 1]. Returned distance is the spherical surface arc `angle * MOON_RADIUS_M`, converted to km, not the straight chord length.
- Line 88 chooses `N = max(3, ceil(distance / grid.pixel_size_m) + 1)` equally spaced fractions from 0 to 1 and removes both endpoints. Thus there are `N - 2` interior samples, with along-arc spacing `distance / (N - 1)` no greater than one grid pixel; even a very short path has one interior sample. This is not adaptive ridge sampling.
- Line 89 uses `radial_points` to walk the great circle, projects those locations with `polar_stereographic_south`, and samples elevations using `TerrainGrid.sample` (bilinear interpolation; see below).
- Line 90 sets endpoint radii to `r1 = R + first.ground + first.mast` and `r2 = R + second.ground + second.mast`. The spherical Moon radius `R = 1,737,400 m` comes from `lunar_core/terrain.py:7`. Mast heights are above each endpoint's local ground, not above the reference sphere.
- Line 92 intersects the straight endpoint chord with the planetocentric radial ray at each fraction `f` of central angle `a`:

  `chord_radius(f) = r1*r2*sin(a) / (r2*sin(a*(1-f)) + r1*sin(a*f))`.

- Lines 93-95 return `(None, distance_km)` if any sampled terrain is nonfinite. Otherwise LOS is true only if **every** chord radius is strictly greater than `R + sampled_height`. Equality is obstructed; there is no tolerance or explicit surface clearance margin.

`scenario` supplies the lander's sampled ground and `mission.observer_m` (`science.py:171-178`), then each relay's candidate elevation and `mission.mast_m` (`science.py:185-186`). It uses only the supplied near grid for this path; it does not fall back to the far DEM. The `mission.clearance` angular setting and `dem_bias_m` are used by the sky-horizon calculation, not by `surface_path`.

Not implemented here: minimum signed clearance output, controlling obstruction coordinates/distance, explicit endpoint validity checks, a partial-profile quality record, Fresnel clearance, diffraction, or a special case for coincident endpoints. At zero central angle the chord formula divides 0 by 0; there is no explicit unknown return for that degeneracy. The return value contains only LOS/unknown and arc distance. Future training code must expose richer diagnostics separately without treating these full-trace outputs as model inputs.

## PDS decoding and downloads

`moonmesh/data.py:11-19` defines an app-local cache at `<repo>/.moonmesh-data`, the LOLA URL prefix, product dimensions/resolutions, and three research points. It does **not** consult `MOONMESH_DATA_ROOT`; that variable is the new training data-root convention, not current app behavior.

`crop(site, product, radius)` (`data.py:35-45`) opens a square, headerless memory map with signed little-endian 16-bit dtype `<i2`, read-only mode, and dimensions taken from the hardcoded `PRODUCTS` dictionary:

| Product | Shape | Pixel size |
| --- | --- | --- |
| `LDEM_85S_20M` | 15168 x 15168 | 20 m |
| `LDEM_75S_240M` | 3812 x 3812 | 240 m |

The code assumes the grid is centered on the projected pole: `edge = dimension * pixel / 2`, left edge `-edge`, top edge `edge`. It projects the site, floors/ceils a square window of the requested half-width and clips indices to the raster (`data.py:39-43`). Line 44 maps raw `-32768` and `-32767` to NaN, multiplies all other raw values by **0.5 m**, and stores float32 heights. No additional vertical offset is applied. Line 45 returns heights plus `x_left`, `y_top` and `pixel_size_m`.

The `.LBL` files are downloaded but **never parsed** by this module. Dimensions, byte order, 0.5 m multiplier and both sentinel values are implementation assumptions, not per-file metadata validation. Product-declared vertical datum: `null` — TODO: inspect the cached labels before using these assumptions as training metadata. Product-declared license/reuse terms: `null` — TODO: inspect source documentation. Checksums: `null` — TODO: compute and record them during training inventory; this downloader does not calculate them.

`download(url, path, size=None)` (`data.py:22-32`) reuses an existing file when no size is specified, or when its size equals the supplied expected size. Otherwise it streams `urlopen(..., timeout=90)` into a `.partial` file, checks size if supplied, then replaces the destination. There is no checksum verification. `main()` (`data.py:48-60`) downloads kernels and PDS sources and rewrites compressed terrain crops, so do not run it during read-only cache reuse.

The exact PDS URLs assembled in `main()` (`data.py:51-53`) are:

- https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/LDEM_85S_20M.LBL
- https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/LDEM_85S_20M.IMG
- https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/LDEM_75S_240M.LBL
- https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/LDEM_75S_240M.IMG

IMG expected size is `dimension**2 * 2` bytes. Labels have no expected size supplied. Kernel downloads (`data.py:49-50`) use `KERNELS` imported from `lunar_core/kernels.py:7-14`:

- https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls
- https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/satellites/moon_de440_250416.tf
- https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/moon_pa_de440_200625.bpc
- https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp

Kernels go under `.moonmesh-data/kernels/`; PDS pairs and crops go under `.moonmesh-data/terrain/`. For each site, `main()` writes `{site}_near20km.npz` using the 20 m product with a 20,000 m half-width, and `{site}_far.npz` using the 240 m product with a 180,000 m half-width (`data.py:54-59`). These are square windows clipped to product bounds, not circular coverage guarantees.

## Defaults in models.py

`Radio` (`moonmesh/models.py:7-15`) requires every radio field except `rate_kbps`, whose default is 128. `Mission` creates the following two complete profiles (`models.py:39-40`); units follow the arithmetic in `network.link_margin`.

| Radio field | Earth profile | Surface profile | Unit |
| --- | ---: | ---: | --- |
| `ghz` | 8.4 | 2.4 | GHz |
| `watts` | 20 | 2 | W |
| `tx_gain` | 30 | 4 | dBi |
| `rx_gain` | 68 | 4 | dBi |
| `loss` | 3 | 3 | dB |
| `sensitivity` | -115 | -105 | dBm at 128 kbps |
| `margin` | 3 | 3 | dB required |
| `rate_kbps` | 128 (Radio default) | 128 (explicit) | kbps |

Radio validation bounds (`models.py:8-15`): frequency `(0, 100]`, power `(0, 100000]`, each gain `[-30, 120]`, sensitivity `[-200, 0]`, loss `[0, 100]`, required margin `[0, 60]`, and rate `(0, 10000000]`, in the units above. There are no antenna patterns, polarization, receiver noise temperature, bandwidth, material properties or diffraction settings in these models.

All `Mission` defaults are recorded below to include geometry, time and related operational constraints:

| Field | Default | Meaning / validation | models.py line |
| --- | --- | --- | ---: |
| `site` | `haworth` | Research-site key | 19 |
| `start` | 2026-11-01 | Start date | 20 |
| `end` | 2026-12-01 | Exclusive end in `scenario` | 21 |
| `minutes` | 30 | Step; allowed 5, 15, 30, 60 | 22 |
| `availability` | 95 | Percent; [0, 100] | 23 |
| `blackout_hours` | 24 | Maximum blackout requirement; [0, 1440] h | 24 |
| `darkness_hours` | 72 | Maximum darkness requirement; [0, 1440] h | 25 |
| `max_relays` | 2 | Integer [0, 4] | 26 |
| `deployment_km` | 8 | Deployment search radius; [1, 8] km | 27 |
| `slope_limit` | 15 | Candidate slope limit; (0, 45] degrees | 28 |
| `observer_m` | 2 | Lander antenna above ground; [0.1, 100] m | 29 |
| `mast_m` | 5 | Relay antenna above ground; [0.1, 100] m | 30 |
| `clearance` | 0.1 | Sky-horizon angular clearance; [0, 10] degrees | 31 |
| `battery_hours` | 48 | Battery endurance; [0, 720] h | 32 |
| `initial_charge_pct` | 0 | Initial charge; [0, 100] percent | 33 |
| `charge_ratio` | 2 | Net sunlight charging rate relative to load; (0, 20] | 34 |
| `resilient` | false | Require performance under individual relay failure | 35 |
| `latitude` | null (`None`) | Optional custom latitude; [-90, -85] degrees | 36 |
| `longitude` | null (`None`) | Optional custom longitude; [-180, 360] degrees | 37 |
| `dem_bias_m` | 0 | Horizon terrain bias; [-30, 30] m | 38 |

`Mission.validate_mission` (`models.py:42-48`) requires 1-60 days and either both custom coordinates or neither. `scenario` uses UTC midnight and an exclusive end, and normalizes custom longitude modulo 360 (`science.py:169-176`). Site coordinates are not defined in models.py: `SITES` (`data.py:15-19`) provides Haworth (-86.8, 337.2), Nobile Rim 1 (-85.4, 37.3), and Malapert (-85.964, 357.681), in degrees latitude/east longitude. Moon radius and horizon sampling defaults are also outside models.py.

## Projection and terrain sampling

All of these functions are in `lunar_core/terrain.py`:

| Function | Lines | Behavior |
| --- | --- | --- |
| `polar_stereographic_south(lat, lon)` | 10-13 | Degree inputs; spherical south polar stereographic with `rho = 2R*tan((90+lat)/2)` (angle converted to radians), `x = rho*sin(lon)`, `y = rho*cos(lon)`, output meters. South pole is the origin; longitude 0 points along positive y, longitude 90 along positive x. |
| `polar_stereographic_south_inverse(x, y)` | 16-18 | `rho = hypot(x,y)`; latitude `degrees(2*atan(rho/(2R)))-90`, longitude `degrees(atan2(x,y)) % 360`. |
| `TerrainGrid.load(path)` | 28-31 | Reads NPZ arrays/fields `heights`, `x_left`, `y_top`, `pixel_size_m`. No CRS/datum metadata validation. |
| `TerrainGrid.sample(x, y)` | 33-37 | Broadcasts inputs, including scalars promoted to at least 1D. Pixel-center indices: `cols=(x-x_left)/pixel_size_m-0.5`, `rows=(y_top-y)/pixel_size_m-0.5`. Uses SciPy `map_coordinates`, `order=1` (bilinear), `mode="constant"`, `cval=np.nan`. |
| `radial_points(lat, lon, azimuth, distance_m)` | 40-45 | Spherical forward great-circle calculation, angle `distance_m/R`; degree latitude/longitude/bearing inputs, meter ranges, degree outputs, longitude modulo 360. Clips the latitude arcsine input to [-1, 1]. |

Rows increase toward decreasing y. Coordinates outside the pixel-center interpolation domain yield NaN; the sampler does not fill nodata or renormalize interpolation over valid neighbors. Every projection/geodesic above uses `MOON_RADIUS_M = 1_737_400` (`terrain.py:7`), with no ellipsoid or EPSG code declared.

For context, `horizon_mask` (`lunar_core/horizon.py:27-48`) is a separate sky-horizon calculation. Defaults: observer height 2 m, azimuth step 2 degrees, terrain elevation bias 0 m. It samples near terrain at near-grid spacing to 10 km and far terrain from `10,000 + far.pixel_size_m` to at most 150 km. It applies bias ramping to full strength over 1 km, includes spherical curvature, and marks an entire direction unknown when any sample is nonfinite. `HorizonMask.at` (`horizon.py:14-20`) interpolates azimuth bins cyclically; `obstruction_distance` (`horizon.py:22-24`) selects the nearest bin. These obstruction distances describe a sky horizon, not a finite surface link.

`horizontal_arrays` (`lunar_core/geometry.py:44-51`) subtracts an observer at radius `R + height_m` from SPICE body vectors and resolves east/north/up. `SurfacePoint.height_m` defaults to 0 (`geometry.py:23-26`). `body_vectors` (`geometry.py:36-41`) uses `MOON_ME` and aberration correction `NONE`; source-frame registration is an approximation stated in `science.py:148`. `apparent_semidiameter_deg` (`lunar_core/visibility.py:6-7`) computes a clipped arcsine angular radius, but `science.visibility` uses Earth/Sun center geometry, not disk visibility.

## Free-space RF and its use by science.py

There is **no locally defined free-space RF function in science.py**. It imports `link_margin` from `.network` (`moonmesh/science.py:18`); the implementation is `link_margin(radio, distance_km)` at `moonmesh/network.py:7-10`:

```text
FSPL_dB = 20*log10(distance_km) + 20*log10(frequency_GHz) + 92.45
sensitivity_dBm = radio.sensitivity + 10*log10(rate_kbps/128)
received_power_dBm = 30 + 10*log10(watts) + tx_gain + rx_gain - FSPL_dB - loss
available_margin_dB = received_power_dBm - sensitivity_dBm
```

Only available margin is returned. The required `radio.margin` is not subtracted inside this function; callers compare against it. `visibility` applies the Earth budget to SPICE range and requires both positive angular terrain clearance and `margin >= mission.earth_radio.margin` (`science.py:98-107`). `scenario` applies the surface budget to the **arc distance** returned by `surface_path` and requires LOS, sufficient surface margin, the relay Earth link and battery power (`science.py:185-190`). This is a free-space budget plus binary geometric gate, not terrain-dependent propagation loss. No reflection, diffraction, antenna pattern or distance-zero guard is implemented in `link_margin`.

The baseline unit test `NetworkTests.test_fspl_and_rate` (`tests/test_moonmesh.py:45-48`) checks a 1 km, 1 GHz, 1 W link with zero gains/loss and -100 dBm sensitivity: margin is 37.55 dB at 128 kbps and 27.55 dB at 1280 kbps.

## Environment and existing checks

`scripts/start.ps1:3-8` creates the app's own Python 3.13 venv under `.moonmesh-data/venv` and installs requirements there; lines 14-16 prepare data and launch the app. Do not use this script for training setup. `requirements.txt:1-5` lists numpy, scipy, spiceypy, fastapi and uvicorn, but no pytest or ML training framework. Training uses `C:\moonmesh-data\venv\Scripts\python.exe` and must leave the app cache read-only.

`TerrainTests` (`tests/test_moonmesh.py:19-41`) checks projection round-trip, curved horizon/missing bins and a synthetic ridge. `NetworkTests` (lines 44-74) checks RF/rate, outage metrics, battery, relay failure/optimization and temporal convergence. `DataTests` (lines 77-114) checks validation and missing kernels, plus cache-dependent real-scenario and SPICE tests. This file contains no direct `surface_path` analytic tests; those are future training work. Existing checks use unittest; no `training/tests/` directory exists at this inspection.
