# MOONMESH Haworth surface coverage

**Release decision: ship the deterministic map.** The learned LightGBM surrogate
fails three of four frozen acceptance criteria. The authoritative decision is
`<data-root>/reports/eval/final/decision.json`: `ship: deterministic`, `model: null`,
24 evaluated scenarios, `final_subset: true`. This card describes the trained
surrogate and the deterministic predictor actually exposed in the app and demo.
All labels, predictions and LunarRF comparisons are **simulated**.

Paths below are relative to `MOONMESH_DATA_ROOT` (default `<repo>/data`; this run
used `C:\moonmesh-data`). Source/configuration files live in this repository.

## Data and reuse conditions

| Source | Role | Recorded license / unresolved conditions |
|---|---|---|
| NASA GSFC LOLA PGDA 90, LDEM_80S_20MPP_ADJ | Primary 20 m DEM; derived slope, relative elevation and roughness | License: null (no license text on https://pgda.gsfc.nasa.gov/products/90, checked 2026-11-15). Its Data Usage Policy asks users to cite Barker et al. 2023, Planet. Sci. J. 4, 183, doi:10.3847/PSJ/acf3e1 (data doi:10.60903/gsfcpgda-lola-spole). Do not infer a license from NASA attribution. |
| NASA GSFC LOLA PGDA 78, Haworth_final_adj_5mpp_surf.tif | Native 5 m cross-source sensitivity and aligned 20 m terrain | License: null (no license or citation text on https://pgda.gsfc.nasa.gov/products/78, checked 2026-11-15; the page defers to a product README not yet read). |
| NASA LOLA PDS LDEM_85S_20M and LDEM_75S_240M | Existing read-only app terrain cross-check | License: null (the PDS labels carry no license text); PDS label documents spherical reference and encoding. |
| WPI LunarRF atlas v2.0, Zenodo record 23301640 | Haworth 20 m simulated atlas comparison and terrain context; not learned inputs | CC BY 4.0 recorded in `training/data_manifest.json`; retain WPI/LunarRF attribution and release citation. |

Product URLs, hashes, provider/version, acquisition gaps and verification state are
in `training/data_manifest.json`, `reports/inventory.json` and `reports/inventory.md`.
Alignment details are in `reports/align/report.md` and
`derived/terrain/haworth/grid.json`. Primary terrain exactly matches LunarRF on
joint pixels; tested terrain registrations are zero shift. The master grid has
4809 rows × 4859 columns, 20 m pixels, lunar south polar stereographic radius
1,737,400 m. PGDA vertical reference remains null / TODO: the PGDA 90 and 78 product pages (checked 2026-11-15) give heights in metres in the DE421 MOON_ME frame but no reference radius. Quality layers (observation
counts, uncertainty, effective resolution) were unavailable. This step performed
no new source downloads or external license verification; redistribution requires
resolving the stated license gaps.

## Generator, inputs and outputs

`training/src/moonmesh_train/los.py` traces the straight antenna-to-antenna chord
over a spherical Moon, bilinear terrain and 10 m path sampling in exact fast mode.
Known terrain must support the entire path; missing/truncated/out-of-range paths
are unknown. Curvature is included; refraction is omitted. Signed minimum clearance
and obstruction coordinates/distance remain diagnostic targets, never model inputs.
`rf.py` uses FSPL and ITU-R P.526 single knife-edge diffraction from the maximum
knife-edge parameter. This omits multiple-edge propagation, reflection, scattering,
polarization/antenna patterns, environmental and hardware variability.

The reference profile is fixed in `training/configs/radio_profiles.yaml`: 2.4 GHz,
33.01029995663981 dBm TX power, 4 dBi gains at each end, 3 dB fixed loss,
−105 dBm receiver sensitivity, required margin 3 dB and 8000 m maximum range.
Pilot TX heights are 5/10/20/50 m; RX height is 2 m. LOS and RF service differ:
an occluded link may retain usable diffracted RF power. Terrain-caused RF outage
means the terrain-aware link fails while its matched free-space margin passes.
Exports retain LOS, continuous RF margin, class and valid mask, elevation/slope,
georeferencing, source checksums, complete scenario, code identity and assumptions.
Unknown LOS/class is 255; continuous unknown is NaN.

The surrogate trains from scratch: LOS classifier, Huber clearance regressor and
Huber RF-margin regressor, LightGBM with validation early stopping. Inputs are
endpoint geometry/heights/frequency, terrain derivatives at TX/RX, a 360-bin TX
horizon and a coarse 16-point terrain profile. Full-trace obstruction/clearance,
IDs, split columns, sample weights and LunarRF output/count layers are excluded
by the explicit allowlist. No pretrained weights were used. Model configuration
and feature provenance are in `training/configs/tabular_haworth.yaml`,
`reports/models/tabular_val.md` and `reports/models/tabular_audit.json`.

## Splits and metrics

Seed 42; geography grouped into 5 km occupied blocks, all scenarios of one TX
share membership. An 8 km training buffer removes three placements. Retained
TX counts are 106 train / 21 validation / 20 test, with 76/17/17 occupied blocks.
Minimum train-to-held-out separation is 8649.185 m. This is not complete terrain
isolation: receiver footprints retain 21.37 km² overlap, 1.2022% of the test union.
Only five retained TX are deployable under the sampled flag, all in training.
There are 588 scenarios and 11,760,000 receiver samples, using 15,000 uniform and
up to 5000 boundary draws per scenario. Uniform-only covered prevalence is
0.554711 train / 0.505824 validation / 0.508210 test. Invalid paths are excluded
from targets; unknown counts include out-of-range rectangle corners.
Evidence: `training/splits.json`, `reports/splits/report.md`,
`reports/dataset/report.md`, `reports/dataset/audit.json`.

Frozen `training/configs/acceptance.yaml` SHA256:
`74401cb0220c2edc1f06594fe8345b01ddf7f7f9eaec31cee9fcaf344797877b`.
False-covered fraction divides false-covered area by true-dead area; acceptance
uses the worse terrain/RF result. Boundary p95 uses the worst scenario. Near
threshold means true margin within ±3 dB of required margin.

| Criterion | Required | Validation LightGBM | Final LightGBM | Final deterministic |
|---|---:|---:|---:|---:|
| False-covered fraction | ≤0.01 | 0.0158186 FAIL | 0.0118877 FAIL | 0 PASS |
| Boundary p95, m | ≤60 | 715.461 FAIL | 374.326 FAIL | 0 PASS |
| Near-threshold margin MAE, dB | ≤2 | 0.795152 PASS | 0.753960 PASS | 0 PASS |
| End-to-end speedup | ≥10 | 0.0641861 FAIL | 0.0825257 FAIL | 1 FAIL |

Final distance-only baseline also fails all four gates (false-covered fraction
0.921565, boundary p95 undefined, near-threshold MAE 26.8041 dB, speedup 5.92009).
Deterministic agreement with its own labels is a consistency check, not independent
accuracy validation. The speedup gate concerns acceleration and is not met by the
chosen fallback. No learned predictor passed all requirements.

Final evaluation covers 24 of 80 test scenarios, six TX with all four heights;
selection used fixed seed and terrain strata only. No test tuning was performed.
Validation also evaluated a 24-scenario subset. Full per-case/class/stratified
metrics, maps and runtimes are in `reports/eval/haworth_val_step11_full/` and
`reports/eval/final/`, including `summary.md`, `metrics.json`, `raw_metrics.csv`,
`stratified_metrics.csv` and `runtime.csv`. Area and boundary distances account
for local polar stereographic scale. Native 5 m sensitivity has LOS flip fraction
0.00475007 over five validation scenarios at only two overlapping TX; 2.5 m tracing
there differs from the primary 10 m spacing, so it combines source/sampling effects.

## Runtime and benchmarks

Final warm end-to-end CPU means: deterministic 8684.081 ms/scenario, distance
1466.883 ms, LightGBM 105228.815 ms. Validation means were 3393.285 / 822.708 /
52866.353 ms respectively. These include raster reads, feature computation,
all heads and post-processing. Each predictor has one application-cold and five
warm measurements. OS cache was not flushed; Python startup is excluded.
The app reuses a bounded physical cache; threshold changes reclassify continuous
margin. The offline demo caches two complete native exports and embeds display
layers/profiles for immediate switching without computation or network access.

Experiment A (`reports/exp_a/report.md`, `metrics.json`, `validation.json`)
aggregated 267 atlas masts at 1.8 GHz, TX 4 m / RX 1.5 m, 22,040 m range.
Any-LOS IoU is 0.994663; no-reflection best-server LOS IoU 0.934547;
no-reflection coverage IoU 0.952359, false-covered area 34.4496 km²,
median power difference −0.0101 dB, MAD 0.2462 dB. Reflection comparison coverage
IoU is 0.958383. Disagreements cluster around rims/walls and serving sectors;
near-zero median gives no evidence of a global budget offset. LunarRF's
delta-Bullington/reflection behavior differs from our single-edge method. Exact
LOS/count/activation semantics and vertical datum remain unresolved. Fixed loss=0
and required margin=0 in the LunarRF profile are documented assumptions.
Experiment A areas use projected pixels without spherical correction; do not
silently equate them with final evaluation areas. This is simulator consistency.

Published NASA Tall Lunar Towers, NASA viewshed trade, JPL rapid-path and NASA/Nokia
benchmarks were **not reproduced in the available run**: `reports/benchmarks/`,
`reports/pipeline/step_12.json`, the benchmark script and reference PDFs are absent.
Matched coordinates, terrain, extent, area method and radio configuration have
not been extracted/verified; no numeric reproduction or percent difference can
be claimed. Spec examples are not measured findings. This reporting step does not
backfill earlier benchmark work. Cross-site labels, evaluation and rotations are
also absent (`reports/sites/` and step 14 status missing).

## Limits and operation outside the evaluated domain

This is a Haworth simulator-based planning demonstration, with no measured lunar
drive-test validation. Learned results generalize poorly at boundaries and are
slower than the deterministic reference. Out-of-site behavior is untested; Malapert
and Nobile have no integrated terrain. The app reports missing data rather than
downloading it. Inputs outside evaluated heights, RX height, profile or range,
or a transmitter whose 8 km range is not fully on prepared terrain (the rule that
placed every training and test tower), explicitly select deterministic fallback. Explicit model requests also fall back because
the learned predictor was rejected. A deterministic fallback is not evidence of
out-of-domain accuracy. U-Net was recommended but deferred in the earlier time-boxed
run (`reports/eval/unet_decision.json`); no U-Net result exists.

Terrain strata are sampling definitions rather than reviewed semantic landforms.
Quality-layer strata and uncertainty calibration are absent. Sampling convergence
and source sensitivity do not cover all unresolved propagation or vertical-datum
errors. A display sampled every several cells can hide narrow holes; inspect native
exports. Surface RF does not determine DTE visibility, battery/power, mission-date
availability or relay robustness. Start the offline walkthrough with `docs/DEMO.md`;
the full eight-item acceptance audit is `reports/acceptance_checklist.md` under the
data root. Unresolved source license metadata prevents declaring every spec
acceptance checkbox passed even though the step 17 documentation/demo gates pass.
