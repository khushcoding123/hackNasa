# Hazard response (research / experimental)

A data-driven estimate of how cold the terrain around each candidate relay is, shown **next to** the physics results in the dashboard
(Analysis menu → *Hazard response*). It does not choose relays, does not feed the requirements status and does not change any mission result.

## Scope, and what was left out

"Hazard response" here means one thing for which real, openly licensed data exists: **cold exposure of the terrain a relay sits in**
(the night-time power and thermal-survival hazard). The model predicts, for a ~500 m terrain facet in the south polar cap (80-90 S):

| output | meaning |
|---|---|
| annual mean temperature (K, 80% range) | Diviner-derived annual average temperature at 2 cm depth |
| annual maximum temperature (K, 80% range) | Diviner-derived annual maximum surface temperature |
| cold-trap probability | probability that water ice is stable at the surface (PRP `ice_depth == 0`) |

Inputs are terrain features computed from the LOLA 240 m DEM only. The dashboard shows these beside the physics already computed for the
mission (sunlight share, minimum node temperature in the cold + dust case, coverage lost if the relay fails) and reports the rank
correlation between the physics sunlight share and the model's predicted maximum temperature as a consistency check.

Hazards deliberately **not** modelled, because no suitable real label exists for them in the polar cap:

| hazard | what I found |
|---|---|
| rocks / boulders, rough terrain | Diviner rock abundance (Bandfield et al. 2011) is only gridded 70 S to 70 N (`data_derived_ghrm`, 3.3 GB per layer, LRO-L-DLRE-5-GDR-V1.0), so it does not cover the 80-90 S cap. No LROC boulder catalogue was located or downloaded. Slope from LOLA is a deterministic function of the DEM, so a "model" of it would only relearn a formula. The existing physics slope screening in the mission result remains the hazard source. |
| dust | No measured polar dust-deposition data was found. Dust stays a user assumption in `moonmesh/environment.py`. |
| relay failure | Failures are simulated exactly by the existing network code (`failures`, `failure_states`). A model trained on our own simulations would be a surrogate with simulated labels and no new information, so it was not built. The table lists coverage lost per relay failure beside the thermal estimate. |
| night-time minimum temperature, hourly temperature | Real Diviner bolometric temperature binned by local time exists (PCP, `data_derived_pcp`, 240 m, 192 local-time bins per season, 59-byte ASCII rows, about 200 MB per bin file). It was surveyed but not used: reaching a per-pixel night minimum needs all bins (several GB). A future extension. |

## Data provenance

| source | what it is | URL | licence / terms |
|---|---|---|---|
| LRO Diviner Polar Resource Product, south (`DLRE_PRP_SOUTH.TAB`, 604,800,210 bytes; dataset `LRO-L-DLRE-5-PRP-V2.0`, version 2; producer D. A. Paige, UCLA) | labels. Thermal-model fits to the first year of Diviner polar observations on a 2,880,000-triangle mesh (about 500 m), derived from the Kaguya DEM. Columns used: `tri_clon`, `tri_clat`, `temp_avg` (K, annual mean at 2 cm), `temp_max` (K, annual maximum at the surface), `ice_depth` (m; -999 = deeper than 2.8738 m; 0 = ice stable at the surface). `tri_calt` is used only as a registration check | https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_prp/dlre_prp_south.tab (label `.lbl`, `.xml` and `prpds.cat` alongside) | NASA Planetary Data System, public data. The label and catalog state no licence restriction. Citation read from the PDS bundle record (`bundle.xml`, fetched 15 Nov 2026): Paige, D.; Sullivan, M.; Williams, J-P. (2022), Lunar Reconnaissance Orbiter Diviner Derived Data Products, NASA PDS, doi:10.17189/wj0s-w188 (bundle `urn:nasa:pds:lro_diviner_derived1`). The record states no licence text. Earlier catalog suggestion: Paige et al., LRO DLRE Level 5 PRP V2.0, NASA PDS, LRO-L-DLRE-5-PRP-V2.0, 2018 |
| NASA LOLA `LDEM_75S_240M.IMG` | inputs. 240 m south polar DEM, int16 in 0.5 m units, nodata -32768/-32767 | https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/ (already in `.moonmesh-data/terrain`, read-only, not downloaded again) | NASA PDS, public data |

Labels are **real**, not simulated, but they are a model product (a thermal-model fit to Diviner data), not a raw measurement. File hashes are written to
`data/derived/datasets/hazard_thermal_v1.json`.

Registration check (LOLA elevation sampled at each facet centre against the PRP's Kaguya altitude): correlation 0.9996, median offset -4.8 m,
5th to 95th percentile -96 m to +86 m. The two DEMs agree well enough for a 500 m facet join.

## Reproduce

All commands use the training Python and run from the repository root. `--help` works on both scripts. The scripts add the repo and `training/src` to `sys.path`.

```powershell
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/18_build_hazard_dataset.py --download   # about 5 min; --download only fetches missing PRP files
& C:\moonmesh-data\venv\Scripts\python.exe training/scripts/19_train_hazard.py                      # about 15 min
```

* Config: `training/configs/hazard_thermal.yaml` (seed 42, split, buffer, hyperparameters). Both scripts log the git commit.
* Dataset: `<data root>/derived/datasets/hazard_thermal_v1.npz` and `.json` (gitignored).
* Model: `.moonmesh-data/models/ora_hazard_thermal.joblib` (gitignored; registered in `ora/status.py` `MODELS`, described in `ora/model_card.json` section `hazard_thermal`, with its SHA-256).
* Report: `<data root>/reports/hazard_thermal/report.json` and `report.md`.
* Code: terrain features and the saved model class are in `ora/hazard.py` so training and the app use identical inputs; the dataset and training code are in `training/src/moonmesh_train/hazard_dataset.py` and `hazard_model.py`. API: `ora/hazard_api.py`. UI: `frontend/hazard.ts`.

## Model

* Inputs (`ora.hazard.FEATURES`, 20 columns, DEM only): distance from the pole, elevation, slope and its radial/tangential parts, slope variability, topographic position at 1/3/10 km, 5 km relief,
  horizon angle statistics from 16 azimuths to 30 km (curvature included), the horizon toward the equator and toward the pole, and the share of the horizon below 1, 3, 5 and 7 degrees. No longitude, no
  label columns, no LunarRF layers, no line-of-sight outputs.
* Estimators: scikit-learn `HistGradientBoostingRegressor` for the two temperatures (point value plus 10% and 90% quantile models), `HistGradientBoostingClassifier` for cold-trap.
  Trained from scratch on 400,000 facets drawn from the training blocks; no pretrained weights.
* Choices made on **validation** blocks only: which inputs (`all`, `no_elevation`, `local_only`; absolute elevation was dropped because together with distance from the pole it identifies a place),
  tree size, and a widening factor for the 80% range (1.3 for mean, 1.1 for maximum).
* The 80-90 S cap holds 2,261,956 facets. Whole 60 km blocks go to train (899,331 facets), validation (479,544) or test (400,309, 19 blocks); 482,772 facets
  within 15 km of a held-out block, or in tiny blocks, are excluded from training.

## Held-out evaluation (test blocks, scored once)

| output | model | best simple baseline | 80% range contains truth |
|---|---|---|---|
| annual mean temperature | MAE 8.9 K (95% block bootstrap 8.1-9.7), R² 0.87 | ridge regression on all features: MAE 12.1 K, R² 0.79 | 83% |
| annual maximum temperature | MAE 14.8 K (12.4-16.6), R² 0.74 | ridge regression on all features: MAE 23.2 K, R² 0.53 | 79% |
| cold-trap probability | ROC-AUC 0.963, average precision 0.195, Brier 0.00956 | prevalence 0.0107 (distance-from-pole-only model: AP 0.010; prior Brier 0.01062) | n/a |

Other baselines it must beat and does: the train mean (MAE 27.7 K and 34.3 K) and a distance-from-pole-only model (26.0 K and 34.3 K). Errors by terrain regime and per block are in the report
(for example the maximum-temperature MAE grows from 11 K on slopes under 5° to 19 K above 20°, and per-block MAE ranges from 7.6 K to 24.3 K, so the average hides real spread).

What this does not show:
* Cold-trap probability is weak in absolute terms (average precision 0.195 means most high-probability facets are not cold traps) and is not calibrated across blocks: the cold-trap share was 0.4% in validation blocks and 1.1% in test blocks.
* No held-out test facet lies within 60 km of the pole, so accuracy close to the pole was not measured (also stated in `limits` in the model card). The retraining that added this line reproduced the same model hash, so the training is deterministic.
* The first attempt used terrain features without slope direction or the equatorward horizon and was **worse than a linear model** (test R² 0.18 for the mean, 0.12 for the maximum; cold-trap did not beat the baselines).
  Adding those physically motivated inputs fixed it. The first report is kept in `data/reports/hazard_thermal_v1_first_features/`. Features were added after seeing that result, which used the test blocks to
  judge one design decision; the selection between feature sets and all hyperparameters used validation blocks only, but the test number is therefore not a fully untouched estimate.

## Limits shown in the dashboard

* Target is a thermal-model fit to Diviner observations on ~500 m facets, not a raw measurement and not a night-time minimum.
* A facet is far larger than a relay. Metre-scale shadows and boulders are not seen.
* Inputs: LOLA 240 m DEM only. No mission dates, illumination time series or hardware.
* Valid only in the 80-90 S cap; elsewhere the API returns `in_domain: false` and no number.
* The "rank among polar facets" is relative to the training data, not a survival limit. No temperature thresholds were invented.
* Errors in the PRP (itself built on the Kaguya DEM) are inherited.

## API

* `GET /api/hazard/model`: label, loaded status and hash, the model-card section, feature list.
* `POST /api/hazard/thermal`: `{"points": [{"id", "lat", "lon", "sunlight_pct"?}]}` (1 to 200 points). Returns per point `in_domain`, `temp_avg_k` and `temp_max_k` (`value`, `low`, `high`, `percentile`),
  `cold_trap_probability` and the input features, plus the model's held-out evaluation. 503 if the model is not trained; the rest of the app is unaffected.

## Open questions

1. ~~Citation wording for the PRP~~ Resolved: use the PDS bundle citation above. The PDS record still has no explicit licence text, so none is claimed.
2. Whether to ingest the Diviner PCP bolometric bins for a night-minimum target (direct observations rather than a model product).
3. A metre-scale relay hazard (boulders, local shadow) has no polar label; LROC NAC boulder data would be the next place to look.
4. PRP labels within a few km of the pole are mostly very high (median maximum about 285 K on steep equator-facing slopes). A rough estimate of sun-facing flux gives about 300 K, so they look plausible, but this was not checked against the raw Diviner data.
