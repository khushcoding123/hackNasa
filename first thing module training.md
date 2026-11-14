# First thing: module training — MOONMESH

Reviewed: 2026-11-14. Repository baseline: `991e4b2afae60219ea7891ddfb903aea48a5dfaa` on `main`.

## 1. Recommendation and scope

MOONMESH should retain its deterministic NASA-data science engine as the source of final recommendations. The first useful optional ML module is a **relay-candidate ranking surrogate**: predict which candidate relays are likely to recover direct-to-Earth outages, then verify candidates with the existing geometry, RF, power and network calculations.

Training is a proposed extension, not existing functionality. No model was trained or benchmarked for this document. For the weekend submission, prioritize a validated reference scenario and reproducible data over adding ML. The ideation brief explicitly identifies custom training as a feasibility risk. ML is useful only if measured speed or search quality improves; a physics-based solution already provides meaningful NASA data use.

## 2. Specifications and current progress

Reviewed sources: [ideation brief](NASA_Space_Apps_Ideation_Brief_for_AI.docx), [deliverables](MOONMESH_DELIVERABLES.md), [README](README.md), `lunar_core/kernels.py`, `moonmesh/{data,models,science,network,server}.py`, `frontend/app.ts`, `tests/test_moonmesh.py`, and `analysis/moonmesh_relay_gate.json`.

| Area | Evidence in current implementation | Remaining work / limits |
| --- | --- | --- |
| NASA terrain ingestion | Downloads LOLA IMG/LBL products; prepares near/far NPZ windows for three sites | Record hashes, validate labels automatically, document frame approximation |
| Sun/Earth geometry | SPICE kernels, time-dependent topocentric azimuth/elevation/range | Existing Horizons reference covers one timestamp; expand independent comparisons |
| Terrain and illumination | Multiresolution horizons, curvature, obstruction attribution, uncertain samples | Test real transitions at finer azimuth/time spacing; DEM spacing is not terrain accuracy |
| Site comparison | Haworth, Nobile Rim 1 and Malapert; comparison API and frontend | Coordinates are approximate research points, not official landing points |
| Relay screening | Up to 15 local peaks, slope/distance/separation rules, sampled surface LOS | Candidate catalog is restricted; straight-line separation does not establish a traversable route |
| RF and power | Separate surface/Earth budgets, rate-adjusted sensitivity, battery/charging simulation | Simplified fixed receiver and constant-load battery; no thermal degradation or station scheduling |
| Optimization/resilience | Search by relay count, infeasible fallback, N−1 evaluation, failure states | Minimum is among screened candidates only; shared receiver failures are outside N−1 |
| Product workflow | Timelines, maps, blackout explanation, sensitivity, disable-relay controls, JSON/CSV/print paths | Code presence is not completed usability or live-demo validation |
| Tests | Projection, terrain, RF, battery, outages, selection and geometry tests exist | Real-data tests skip without prepared data; this review did not run tests or build |
| ML | No ML dependencies, training scripts, dataset splits or model artifacts found in reviewed tree | All training steps below are new proposals |

The default mission is Haworth, 2026-11-01 through 2026-12-01 (exclusive end), sampled every 30 minutes: 1,440 timestamps. Defaults include 95% availability, 24-hour maximum blackout, two relays, 8 km deployment radius, 15° slope limit, 2 m lander antenna, 5 m relay mast and 48-hour battery endurance starting empty.

The deliverables checklist remains unchecked; it must not be interpreted as a measured completion percentage. Historical `analysis/` results are exploratory outputs, not current application acceptance results. For example, the relay gate reports geometry-only improvement at Haworth and no surface-LOS candidates at its Nobile point. These are not measured powered/RF network performance.

## 3. Challenge alignment and release requirements

The brief identifies **Lunar Landing Site Navigator** as a relevant 2026 challenge. Proposed alignment: help a mission planner choose a landing point and relay configuration using terrain-dependent illumination, communication outages and resilience.

**Official fit remains unverified.** The challenge index at https://www.spaceappschallenge.org/2026/challenges/ returned HTTP 503 during this review. The repository does not provide a selected challenge's exact text or final challenge URL. Before submission, save that URL and map each required deliverable to a demonstrated feature; do not claim compliance based solely on the title.

The supplied brief says Seattle allows four minutes plus one minute of questions and requires open source for global judging; deliverables suggest a three-minute walkthrough. Treat three minutes as the core demo within the brief's stated slot and verify current event rules. The repository is currently private and the reviewed tree contains no LICENSE. Public release and license selection remain separate team actions; this documentation commit does not change visibility or licensing.

## 4. Dataset catalog

### Required now: already referenced by the code

| Dataset / exact product | Variables, coverage and format | Use |
| --- | --- | --- |
| [LOLA LDEM_85S_20M label](https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/LDEM_85S_20M.LBL) / [IMG](https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/LDEM_85S_20M.IMG) | PDS dataset LRO-L-LOLA-4-GDR-V1.0, product V2.0; south-polar map labeled 85–90° S; 20 m/pixel at pole; 15,168 × 15,168 signed little-endian 16-bit samples | Near terrain, local slopes, local relay peaks and surface LOS; near horizon |
| [LOLA LDEM_75S_240M label](https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/LDEM_75S_240M.LBL) / [IMG](https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/LDEM_75S_240M.IMG) | Same dataset/version; south-polar map labeled 75–90° S; 240 m/pixel at pole; 3,812 × 3,812 signed little-endian 16-bit samples | Far terrain for horizon rays out to 150 km |
| [DE440s planetary SPK](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp) | Binary SPICE ephemerides; approximately 1849–2150; query actual loaded segment coverage | Earth/Sun positions and range; geometry inputs, not ML labels by themselves |
| [Lunar frame kernel](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/satellites/moon_de440_250416.tf) | Text FK, lunar reference-frame definitions | Establish MOON_ME geometry |
| [Lunar orientation PCK](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/moon_pa_de440_200625.bpc) | Binary time-dependent lunar orientation | Rotate inertial geometry into lunar body-fixed frames; inspect coverage |
| [Leap seconds](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls) | Text LSK | UTC/ephemeris-time conversion; record version and check for updates |
| [JPL Horizons](https://ssd.jpl.nasa.gov/horizons/) / [API documentation](https://ssd-api.jpl.nasa.gov/doc/horizons.html) | Requested observer ephemerides: angles, range, timestamps and conventions | Independent geometry comparison; preserve raw requests/responses |

Both LOLA labels were retrieved successfully on 2026-11-14. They report acquisition dates 2009-07-13 through 2017-02-02 and creation date 2017-06-15. These are static terrain products, **not observations of November 2026 conditions**. Their labels specify planetocentric latitude, east-positive longitude, polar stereographic projection, DE421 mean-Earth/polar-axis coordinates and 1,737,400 m reference radius.

Decode height in metres as `DN × 0.5`; planetary radius is height plus 1,737,400 m. Preserve unknown cells as missing, never zero elevation. The code masks −32768 and −32767; validate fill-value conventions against product documentation. Rectangular grid corners and advertised latitude coverage are not a guarantee of valid terrain at every cell.

The two IMG payloads total 489,192,736 bytes before kernels and derived crops. README estimates about 535 MB for initial source setup. Use `python -m moonmesh.data` to fetch the exact current sources; do not commit the ignored `.moonmesh-data/` payloads.

**Frame issue:** the application treats LOLA's DE421 ME frame and its DE440 ME geometry as coincident at DEM scale. Record this approximation and assess its effect near narrow horizon-clearance thresholds before calling the output validated.

### Optional extensions: not ingested or validated in this project

| Source | Relevant data / access | Appropriate extension and prerequisite |
| --- | --- | --- |
| [LROC PDS archive](https://pds.lroc.asu.edu/) | NAC imagery, derived terrain products and product labels; footprint, illumination and resolution vary | Inspect obstacles or terrain context; choose a specific overlapping product and co-register before training. An image's shadows are tied to its acquisition time |
| [Diviner archive](https://pds-geosciences.wustl.edu/missions/lro/diviner.htm) | Radiometry and derived thermal products, with product-specific time/spatial sampling | Thermal feasibility for relay power/hardware; select exact temperature product and reconcile local time, resolution and assumptions |
| [NASA candidate-region announcement](https://www.nasa.gov/news-release/nasa-provides-update-on-artemis-iii-moon-landing-regions/) | Context and region descriptions | Region naming/provenance; not pixel-level landing safety labels or communication truth |

LROC and Diviner archive pages returned HTTP 200 on the review date; no individual optional product was downloaded or scientifically validated. Keep these outside the first training module.

## 5. First training module: relay-candidate ranking

### Decision and target

Predict each candidate's **incremental recoverable outage fraction**, then rank candidates for exact evaluation. One training row represents one lander–candidate pair under one complete mission configuration.

Use the physics engine to generate:
- Primary target: `candidate.recovered / 100`, the fraction of mission samples where the relay's two-hop RF/LOS/power path works while direct service fails.
- Secondary diagnostics: candidate link availability, longest link outage, minimum battery reserve and unknown-terrain fraction.
- Separate validity flag: whether terrain and labels are sufficiently known. Unknown is not a measured failure label.

These are **simulator-generated labels**, not measured lunar radio performance. Fit a surrogate of this simulator; do not describe it as learning ground truth from NASA communications telemetry. Individual recovery cannot capture complementary pairs, so final subset selection must use full timelines and the existing optimizer.

### Inputs available before expensive evaluation

Use candidate elevation, local slope, lander–relay distance, relative elevation, bearing encoded as sine/cosine, lander/relay coordinates, antenna heights, radio parameters, clearance, terrain bias, battery endurance, initial charge, charge ratio, mission start and duration.

Include cheap terrain-validity summaries and date features. Record the cost of computing every feature. Do not supply `candidate.recovered`, temporal coverage, computed Earth/solar availability, battery reserve or final selection as inputs: those are targets or expensive post-evaluation outputs. A feature derived from the full physics calculation defeats an acceleration claim.

Start with the current screened peaks. Ranking those peaks can only speed evaluation; it cannot discover excluded terrain. Expanding to more candidates requires a separately versioned generator and a new quality comparison.

### Baselines and model choice

1. Compare against the existing height-based ordering, a simple distance/slope heuristic and exact evaluation of the complete current catalog.
2. Fit a small scikit-learn ExtraTreesRegressor on tabular features with fixed seed, e.g. 42. Treat estimator count/depth as validation-tuned parameters.
3. Consider gradient-boosted trees only if they improve held-out quality or latency. Deep learning, image segmentation and LLM fine-tuning are unnecessary for this first module.
4. If inference plus feature preparation does not reduce end-to-end latency, retain exact evaluation.

Scikit-learn is a proposed training-only dependency and is not currently in `requirements.txt`. Publish a locked training environment if implemented.

## 6. Construct a reproducible training set

1. Freeze the code SHA, raw-file hashes and all kernel versions. Run geometry/terrain validation before using the engine as a label generator.
2. Generate valid custom lander points within each prepared terrain window; reject missing cells and out-of-window points. Start with a small pilot of 30 missions per site to measure compute cost and target diversity, then scale only if useful.
3. Sample mission settings within `Mission` validation bounds: 1–60 day duration, 5/15/30/60 minute spacing, available ephemeris dates, mast heights, deployment radius, slope threshold, RF budgets and battery settings. Start around the November 2026 reference window; reserve later non-overlapping periods for testing.
4. Evaluate each mission once and save every candidate, including low-value and infeasible examples. Avoid sampling only successful optimized networks. Share cached terrain and horizons where valid.
5. For pilot generation, use the current `scenario()` result. A future dedicated label generator can avoid its map rendering and exhaustive network-search overhead.
6. Add deliberate near-boundary and perturbation scenarios, keeping all variants of a parent scenario in the same split. Store uncertainty separately from binary availability.
7. Save rows in Parquet plus a small committed manifest/sample. Keep full generated data outside Git when large.

Suggested schema:

```text
row_id, parent_scenario_id, spatial_block_id, site_id, candidate_id
code_sha, terrain_hashes, kernel_hashes, generator_version, seed
start_utc, end_utc, step_minutes, mission_config_json
lander_lat_deg, lander_lon_deg, relay_lat_deg, relay_lon_deg
distance_km, slope_deg, elevation_m, relative_elevation_m, bearing_sin, bearing_cos
feature_schema_version, terrain_known_fraction
target_recovered_fraction, target_link_availability_fraction
target_longest_outage_h, target_min_reserve_h, label_valid
```

Keep the full mission configuration and raw label timelines in companion files, even when scalar rows are used for training. Document percent-to-fraction conversions explicitly. Candidate IDs such as R01 are local to a scenario and must not be learned as meaningful identifiers.

## 7. Splits, evaluation and release gates

Never randomly split adjacent timestamps or candidate rows from the same mission. Nearby terrain windows and overlapping mission periods can make an apparently excellent model a memorizer.

- Group by parent scenario and spatial blocks with buffers large enough to prevent overlapping local feature patches. Keep all candidate rows and perturbations together.
- Run three leave-one-site-out evaluations. With only three sites, report wide uncertainty and each site's results separately.
- Add a temporal holdout with non-overlapping periods; report separately from spatial generalization. Horizon footprints can overlap even across nearby blocks, so disclose residual spatial dependence.
- Fit preprocessing and tune hyperparameters on training/validation partitions only. Freeze the final test set before tuning.

Report MAE in recovered fraction, rank correlation, recall of the exact best candidate within top-k, and end-to-end latency including features and fallback. For near-tied candidates use recovery regret as well as rank metrics. Compare downstream network availability, longest blackout, relay count and feasibility against complete exact evaluation under identical settings.

Proposed acceptance targets, **not achieved results**: best-candidate top-5 recall ≥95%, mean absolute recovery error ≤0.02, and ≥2× end-to-end speedup on held-out missions. Report counts and bootstrap intervals grouped by mission. The current 15-candidate catalog may be too small for acceleration to justify complexity.

Always verify chosen relays with exact physics. If a truncated candidate shortlist is used, describe results as “best among evaluated candidates”; it cannot retain the full-catalog minimum claim. For the existing “minimum among screened candidates” guarantee, evaluate the entire screened catalog. If the shortlist is infeasible, unknown, out of distribution or close to a requirement threshold, fall back to the complete catalog. Thresholds must be selected on validation data.

Independent physical checks remain necessary: multi-time/site Horizons comparisons with matched geometric/apparent conventions; terrain/frame checks; finer time/azimuth convergence around transitions; battery/RF boundary cases. Agreement with the training simulator is not independent scientific validation.

## 8. Implementation sequence and ownership

| Priority | Work / proposed artifact | Owner alignment |
| --- | --- | --- |
| P0 | Validate NASA labels, coordinate/frame assumptions and one reference mission; record hashes in `training/data_manifest.json` | Khush |
| P0 | Record exact challenge requirements, license decision and reproducible demo evidence | Team / Ayaan |
| P1 | Implement `training/generate_dataset.py` and grouped split manifest | Khush + Ayaan |
| P1 | Implement `training/train_ranker.py`; save pipeline, feature schema and model card | Ayaan, with science review |
| P1 | Implement `training/evaluate_ranker.py`; compare baselines, full optimizer and held-out subsets | Khush + Ayaan |
| P2 | Integrate optional candidate ranking with exact verification and fallback | Ayaan |
| P2 | Show physics provenance, surrogate use and honest selection scope in UI/report | Jeswanth |

These paths are proposed future files; this documentation change does not create a pipeline or modify the application. Store model version, source SHA, dataset/split hashes, training parameters, measured metrics, limitations and fallback rules in a model card.

Current setup and validation commands from the repository:

```sh
python -m pip install -r requirements.txt
npm ci
npm run build
python -m moonmesh.data
python -m unittest discover -s tests -v
```

Check Python 3.11+ and a usable Node environment first. Download failures or skipped real-data tests do not count as scientific validation.

## 9. Judge-facing demonstration and deliverable

Show a single decision: “At this research point and mission period, what outages remain after a feasible surface relay deployment, and what happens when one relay fails?”

Compare sites; inspect one terrain-caused blackout; show the exact two-hop/power constraints; optimize; disable a relay; export assumptions and source provenance. Use local prepared NASA data and a frozen JSON/report backup. If an ML surrogate is added, show measured acceleration and exact rechecking, with its model card. If the targets are unmet, demonstrate the existing physics engine without an ML claim.

Before submission, close the official challenge-fit gap, independently validate the reference scenario, run the project checks, confirm export/UI behavior, and resolve public-source licensing. No claim of global optimality, flight readiness, guaranteed service or verified novelty follows from this prototype.
