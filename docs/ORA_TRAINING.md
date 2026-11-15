# ORA training procedure and datasets

How the two ORA models are trained, what each file in the team's `datasetsnetworknodes` folder is used for, and which outside datasets could extend the work.

## Procedure

Each step is a command. Re-running with the same seed gives the same models.

1. **Get the data.**

   ```bash
   python -m moonmesh.data
   python -m ora.data --earth
   ```

   This prepares 20 m lunar terrain windows for five sites and downloads the Earth training data into the git-ignored `.moonmesh-data/` folder.
2. **Train.**

   ```bash
   python -m ora.train
   ```

   - **Siting prior.** For each of six 1° Earth tiles, tower cells are positives and eight times as many random cells are background. Features per cell: slope, topographic position at 300 m, 1 km and 3 km, local relief, relative elevation, distance to the nearest ridge, and the share of an 8 km disc the site can see. Absolute elevation is left out because it does not transfer between bodies. Model: gradient-boosted trees with balanced classes.
   - **Coverage surrogate.** 1,500 cells are sampled from each of five lunar sites. The label is the exact viewshed share computed by line of sight. Features are the cheap ones above.
3. **Validate.** Both models use spatial cross-validation: one whole region or site is held out per fold. Random splits would leak, because neighbouring cells are near-duplicates.
4. **Read the model card.** `ora/model_card.json` records the date, seed, features, counts, per-fold metrics and limits. The app shows it under Models & limits.
5. **Use.** The app no longer ranks lunar relay candidates with these models. `ora/scenario.py` ranks candidates by their computed 8 km viewshed share and passes those scores to the optimizer as path costs and tie-breaks, because that computation beats the models against dense line-of-sight truth and costs no more ([ACCURACY.md](ACCURACY.md)). The models remain for research, benchmarks and the stress suite. `python -m ora accuracy` trains two newer models (a view estimator and a gain selector) on development windows and scores them on untouched windows.

### Measured (seed 0)

| Held-out Earth region | Towers | AUC | Towers in the top 10% | View-only baseline AUC |
| --- | ---: | ---: | ---: | ---: |
| N47W122 (Cascades) | 54 | 0.888 | 43% | 0.832 |
| N44W122 (Cascades) | 72 | 0.785 | 36% | 0.831 |
| N35W116 (Mojave) | 23 | 0.719 | 17% | 0.662 |
| N35W118 (Mojave) | 31 | 0.831 | 39% | 0.747 |
| N39W107 (Rockies) | 51 | 0.867 | 41% | 0.810 |
| N38W108 (Rockies) | 45 | 0.937 | 64% | 0.965 |
| **Mean** | | **0.84** | **40%** | 0.81 |

The model beats the view-only baseline in four of six regions and loses in two, so most of its skill is "towers stand where they can see far". Coverage surrogate: rank correlation 0.61 and R² 0.18 on held-out lunar sites.

### Update after the training prompts (seed 0)

All numbers come from `python -m ora.train`, `python -m ora benchmark` and `python -m ora tall-towers`.

- **Siting prior, 14 usable Earth tiles with skyline features** (tiles with fewer than 15 usable towers are no longer trained on or scored). Leave-one-tile-out mean AUC 84.5%, towers in the top 10% of cells 43%, no better than the earlier 12-tile run (85.5%, 50%) because the tile set changed; the sealed-tile test below is the figure to trust. Weak tiles: N36W112 (AUC 62%) and N33W111 (78%).
- **Coverage surrogate, rebuilt on 86 lunar windows.** The 85 S LOLA product spans 151 km either side of the pole; a lattice of 20 km windows covers it (`ora/lunar_windows.py`). 16 windows (four 2 x 2 blocks) are sealed as a test set, and every window touching them is dropped from training, leaving 86. 600 cells per window (51,600 in all), labelled by exact viewshed. New features: the visible share out to 1, 2 and 4 km (free from the same sweep as the label) plus the six skyline summaries. Spatial cross-validation on 60 km blocks of the training windows: R² 69%, Spearman 83%. Site-level features (mean slope, elevation spread) were tried and dropped because they lowered R².
- **Marginal-gain model** (`ora_gain_model.joblib`): predicts the coverage a candidate would add given the relays already chosen, trained on greedy and random rollouts of the exact line-of-sight matrices.
- **Selecting sites** (`python -m ora benchmark`, sealed windows): see the stress report below. A top-k ranking by single-site score is not enough; selection must be coverage-aware.
- **Tall Lunar Towers** (`docs/figures/ora_tall_towers.json`, `python -m ora tall-towers`). Not reproduced. The paper reports 14.7% (10 m tower) and 25.9% (50 m tower) of a 20 km radius at the ridge centre, about 40% for an optimal spot. At the centre of our Connecting Ridge window we get 2.9% and 10.4%; the best cell within 8 km gives 28.6% and 34.8%. A 50 m tower covering more than a 10 m tower, and no single tower getting past roughly 40%, agree with the paper. The absolute numbers depend on where the centre is, which our approximate site point does not fix.

### Fresh-data test (`python -m ora evaluate`, `docs/figures/ora_evaluation.json`)

Saved models loaded as they are and tested on data that training and tuning never read: eight Earth tiles (six with enough towers) and the 16 sealed lunar windows.

- **Siting prior:** 330 towers against 3,160 background cells: AUC 88.2% (view-only baseline 82.1%), 48.5% of towers in the top 10% of cells (chance 10%), 74.2% in the top 20% (chance 20%). It beats the view-only baseline in 4 of 6 scored tiles.
- **Coverage surrogate:** R² 71.1%, Spearman 82.3%, mean error 5.7 percentage points, 83.7% of predictions within 10 points. It finds 66% of the truly best-viewing quarter of cells (chance 25%). The earlier version scored R² 21% on four other new sites, so the extra windows and the multi-range view features were the fix.

### Stress report (`python -m ora stress`, `docs/figures/ora_stress.json`)

Nine evals on data that training never read: accuracy, calibration, physical sanity, terrain noise (independent and spatially correlated), coarser terrain, other mast heights, relay selection at three budgets, the siting prior on sealed Earth tiles with a shuffled-label control, and Earth-to-Moon transfer. Pass criteria were written before the first run. **First run: 19 of 21 checks passed. After the fixes below: 23 of 24.** Three criteria changed between runs, and this is what changed: the worst-window R² check (8%) was replaced by within-window rank agreement and error, because R² is meaningless in a window with little spread; two correlated-noise checks were added.

| Aspect | Before fixes | After fixes | Verdict |
| --- | --- | --- | --- |
| Surrogate accuracy (sealed windows) | R² 71%, Spearman 82%, error 5.7 points | R² 71%, Spearman 82%, error 5.7 points | pass |
| Calibration | slope 1.01, worst decile off 1.5 points | slope 1.00, worst decile off 2.3 points | pass |
| Worst single window | R² 8% (fail) | rank agreement 63%, error 6.4 points | pass on the replaced criteria; R² is still 2% in that window |
| Correlated height error 2 m / 5 m | not tested | R² changes by -1.0 / +2.2 points | pass |
| Independent height error 5 m | R² drops 39 points | R² drops 33 points (report only, no criterion) | still fragile |
| Coarser terrain 60 m | R² 62% | R² 61% | pass |
| Mast 3 m / 20 m | R² 47% / 61% | R² 60% / 68% | pass; the 3 m over-prediction is gone |
| Relay selection, 8 relays | shortlist then greedy 94% of exact, learned gain 83% | same: 94% and 83%, learned gain beats random in 100% of windows | pass |
| Siting prior, sealed Earth tiles | AUC 88.2%, top 10% holds 48.5% of towers | same, shuffled-label control 46.6% | pass |
| Earth prior on lunar cells | Spearman 18% | 18% | fail, handled by blending (below) |

What was done about each weak spot, decided on spatial cross-validation of the 86 training windows only (never the sealed ones):

1. **Earth prior on the Moon (Spearman 18%).** Unchanged as a model. In the app the siting score is now 80% the lunar surrogate rank plus 20% the Earth prior rank, and the method string says so. The stress check still fails because it tests the prior alone.
2. **Plain top-k is not a selector.** The app's coverage-first panel (`POST /api/ora/coverage`) shortlists 40 candidates with the surrogate and runs greedy on exact line of sight inside the shortlist. More rollouts and richer overlap features for the learned gain model were tried: 81.6% of exact greedy at baseline, 82.4% with both, 81.8% with six rollouts only. No meaningful gain, so the simpler version stays.
3. **Height errors.** Training now includes the windows re-run with correlated 2 m and 5 m height error. Correlated error is no longer a problem. Independent per-pixel error of 5 m (a harsher test than real DEM error) still costs 33 R² points; fixing that needs features that are smoothed over several pixels.
4. **Mast height.** Mast height is now an input, with training windows at 3 m, 10 m and 20 m.
5. **Peaks.** A loss weight of 1 + 3 x max(share - 0.4, 0) cut the error on cells that see 60% or more from 14 to 12.7 points, against 5 to 6 elsewhere. Still the weakest part.
6. **Low-variance windows.** Reported as within-window rank agreement and error instead of R².

Cross-validated surrogate before and after the augmentation: R² 69.0% to 68.7%, error 6.2 points both, so the extra training rows do not cost accuracy on clean terrain.

### UWB mine-tunnel dataset (downloaded, checksum verified)

Zenodo 10.5281/zenodo.18835598, CC BY 4.0, 645.5 MB. A straight 60 m cave tunnel in Slovenia: an anchor at the entrance, a tag moved from 1 m to 40 m in 1 m steps, 1.4 m antenna height, UWB channel 4 (about 4.0 GHz) and channel 7 (about 6.5 GHz), 200 repeats per file. It also holds a LiDAR point cloud and meshes of the tunnel and CloudRT ray-tracing path loss for the same links. The zip is kept in the temp folder (`uwb_tunnel/uwb.zip`), outside the repo. `python -m ora.fit_uwb <zip>` fits the model and writes `docs/figures/ora_uwb_fit.json`.

- **Measured: the model could not be fitted.** The receiver-reported signal strength is almost flat: about -80.5 dBm at 1 m and -83 to -86 dBm from 10 to 40 m, while free space alone would lose about 32 dB over that span. The reported range follows the true distance, so the records are valid; the signal strength does not behave like distance-limited loss in this tunnel (the dataset does not say whether that is strong guided and reflected energy or receiver gain adjustment). Fitting `rss = P0 - FSPL - alpha*d/100` gives a negative alpha (about -48 dB per 100 m on channel 4), which only says "flatter than free space". It is not a usable waveguide loss, so the `TunnelRadio` defaults are unchanged.
- **Simulated (ray tracing, not measurement):** over 3 to 40 m the loss rises about 34 dB, fitting a free-space term plus about 11.6 dB per 100 m on channel 4 (95% interval 5 to 18) and 9.4 dB per 100 m on channel 7 (2 to 17), RMS error about 2 dB. Treat it as an estimate for a rough wet-earth cave at 4 to 6.5 GHz, not for the 2.4 GHz model the app uses and not for the Moon.
- **What would fix it:** a transmit power and antenna gain for the dataset, or a run of your own ESP32 logger, so an absolute loss can be computed.

### Still not done

- **Tunnel radio model** is not fitted; see the UWB section above for why the downloaded measurements could not set it.
- **Real mine graph** is not loaded. The Hagerbach dataset is under a non-commercial rights statement (ETH); the Wadi Michmash cave survey is CC BY 4.0 on Zenodo (20410901). Neither has been downloaded or checked for a loadable format.
- **Module catalogue** values are still placeholders. Every entry now carries `assumed: true`, and `python -m ora parts` prints a warning. No sourced values were added because none were found; none were guessed.
- **LuGRE** was unpacked and read. It holds GPS and Galileo L1/E1 and L5/E5 raw observables and signal samples from the Blue Ghost 1 lander (15 January to 16 March 2025, CC BY 4.0), 154 telemetry and 60 signal-sample files. It is navigation data and says nothing about surface radio links, so nothing in the link model uses it.
- **Khush's coverage training spec** (`MOONMESH_TERRAIN_COVERAGE_TRAINING.md`, 12+ prompts with its own environment and downloads) was not started. It trains a different model (simulated coverage labels from a LunarRF dataset), so it does not duplicate the ORA surrogate, which is trained on exact viewshed of our own terrain.

### Extending the training

- Add tiles to `EARTH_TILES` in `ora/data.py`. More low-population mountain regions reduce the pull of roads and towns.
- Add lunar sites to `ORA_SITES` in `ora/data.py` (anything south of 85°S is in the terrain product already on disk).
- The "coverage gained per site" comparison now exists as `python -m ora benchmark`.

## The team's dataset folder

`Desktop/datasetsnetworknodes/` holds papers and data. Papers are not training data; they supply methods and benchmarks.

| File | Used for | Status |
| --- | --- | --- |
| `datasets/NAIF_SPICE_kernels/` (`de440s.bsp`, `moon_pa_de440_200625.bpc`) | Sun and Earth geometry for the twin | In use. The app downloads its own copies plus the leap-second and Moon frame kernels the plan lists as missing. |
| `datasets/LuGRE_lunar_GNSS/LuGRE.zip` | Measured GPS and Galileo signals at the Moon (CC BY 4.0) | Not used. It could support a navigation and timing layer, such as clock sync for duty-cycled relays. It does not describe surface radio links. |
| `papers/TerrainLOS_MASCOTS_2016.pdf` | The qualitative check that terrain lowers degree and disconnects graphs | Reproduced in `scenarios/earth_cascades_validation.yaml` and a unit test. |
| `papers/Schwarze_Structural_Robustness_of_Networks_2024.pdf` | Robustness metrics | Schneider R and critical fraction are implemented. |
| `papers/Gemmi_2022_Next_Generation_Wireless_Backhaul.pdf`, `papers/cISP_Speed_of_Light_ISP_2018.pdf` | Terrain visibility graphs and tower data on Earth | Method reference for the Earth training set. |
| `papers/Tall_Lunar_Towers_ASCEND_2023.pdf` | Viewshed benchmark at Connecting Ridge | Terrain prepared, benchmark not yet reproduced. |
| `papers/NASA_Glenn_Lunar_Surface_Relay_Terminal_2023.pdf` | Tower siting sensitivity at Malapert | Reference only. |
| `papers/LunaNet_Interoperability_Specification_v5.pdf` | Terminology for relay and service roles | Not yet reviewed against the app's wording. |
| `papers/MMS_High_Altitude_GPS_NTRS_20190027307.pdf` | High-altitude GNSS | Not used. |

## Data the app downloads

| Dataset | Source | Role |
| --- | --- | --- |
| LOLA 20 m and 240 m south polar terrain | NASA PDS (`imbrium.mit.edu`) | Line of sight, viewsheds, horizons |
| DE440 ephemeris and lunar frame kernels | JPL NAIF | Sun and Earth positions |
| Antenna Structure Registration | US FCC (`data.fcc.gov`), public record, 38 MB | Tower labels |
| 1 arc-second elevation tiles | AWS open terrain tiles, SRTM-derived | Earth terrain features. Not NASADEM, which needs a login; the source data is the same mission. |

## Outside datasets for underground work

Found with `python -m ora.literature` (OpenAlex). Only the catalogue records were read. Check each one's contents and licence before using it.

| Dataset or paper | Why it matters | Link |
| --- | --- | --- |
| UWB measurements and 3D model of a mine tunnel (2026, with a Scientific Data paper) | Measured radio propagation in a real mine tunnel. The closest thing to ground truth for the tunnel loss model. | https://doi.org/10.5281/zenodo.18835598 · https://doi.org/10.1038/s41597-026-08365-y |
| Underground Mine Dataset, Versuchsstollen Hagerbach (ETH Zurich, 2020) | A real underground mine recorded by robots. A possible source for a real tunnel graph. | https://doi.org/10.3929/ethz-b-000425328 |
| A Survey of Wireless Communications and Propagation Modeling in Underground Mines (2013) | Standard reference for tunnel propagation models and typical loss values. | https://doi.org/10.1109/surv.2013.031413.00130 |
| Channel modeling and analysis for wireless networks in underground mines and road tunnels (2010) | Multimode waveguide channel model. | https://doi.org/10.1109/tcomm.2010.06.080353 |
| Modeling and measurements for wireless communication networks in underground mine environments (2019) | Measured path loss in mines. | https://doi.org/10.1016/j.measurement.2019.106980 |
| Relay node placement for wireless sensor networks deployed in tunnels (2010) | Prior work on the placement problem itself. | https://doi.org/10.1109/wimob.2010.5644984 |
| Daisy chain navigation and communication in underground environments (2021) | Robots chaining relays underground. | https://doi.org/10.1109/aero50100.2021.9438304 |
| Lunar lava tube stability models (2026) | Geometry for lunar tunnel scenarios. | https://doi.org/10.5281/zenodo.20938737 |
| Wadi Michmash cave survey (2026) | A cave survey that may be loadable with `Mine.from_survey`, depending on its format. | https://doi.org/10.5281/zenodo.20410901 |

Searches for DARPA Subterranean Challenge data and LoRa tunnel measurements returned nothing useful in OpenAlex. The plan's statement that Subterranean Challenge teams used breadcrumb repeaters has not been checked against a source here.
