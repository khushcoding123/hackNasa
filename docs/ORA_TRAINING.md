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
5. **Use.** `ora/scenario.py` loads the siting prior, scores every candidate, and passes the scores to the optimizer as path costs and tie-breaks. If the model file is missing, the app falls back to a terrain heuristic and says so.

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

### Extending the training

- Add tiles to `EARTH_TILES` in `ora/data.py`. More low-population mountain regions reduce the pull of roads and towns.
- Add lunar sites to `ORA_SITES` in `ora/data.py` (anything south of 85°S is in the terrain product already on disk).
- Add "coverage gained per site" against random and greedy baselines on a held-out lunar site. This is the comparison the plan asks for and is not yet written.

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
