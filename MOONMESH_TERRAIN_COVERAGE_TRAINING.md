# MOONMESH terrain-based coverage and dead-zone mapping

**Purpose.** Give Claude the project context, dataset links, scientific requirements, and acceptance criteria needed to produce a step-by-step plan for training a model that maps lunar south-pole terrain and predicts where terrain limits radio coverage.

**Primary feature.** Given lunar terrain, a transmitter location and antenna height, a receiving antenna height, and a defined radio configuration, map where the surface link has geometric line of sight, where terrain obstructs it, how much usable RF margin the propagation model predicts, and where coverage is unknown. If the transmitter changes location or height, the map must change accordingly. LunarRF, NASA tower studies, and JPL viewshed work are the main references for this feature.

**Status.** This is a training handoff and implementation specification. The linked archives are referenced by download URL. No raster payloads, generated datasets, or trained weights are bundled in this Markdown. Verify exact raster overlap and check licenses and labels during ingestion.

## 1. Project background

MOONMESH is a NASA Space Apps project for comparing lunar south-pole landing points and mission dates. Its wider workflow combines Sun and Earth geometry, terrain horizons, direct-to-Earth (DTE) blackout timelines, and solar-powered surface-relay planning. The intended user is a mission planner choosing where and when to operate, which terrain blocks communications, and where a relay might help. The official challenge asks for an intuitive comparison of sites and dates and visualization of Sun and Earth relative to the horizon. [Lunar Landing Site Navigator](https://www.spaceappschallenge.org/2026/challenges/lunar-landing-site-navigator/)

The current repository has a deterministic calculation path. It prepares NASA LOLA terrain windows, computes Sun/Earth geometry with JPL SPICE, draws terrain horizons, evaluates a sampled surface link, estimates RF margin and battery state, searches screened candidates, and compares the selected network before and after an individual relay failure. The attached module-training note proposes a relay-ranking surrogate. A spatial coverage model is a distinct upstream task and is the priority of this handoff. Its predictions could eventually assist relay screening after evaluation against the deterministic engine.

| Research site | Repository key | Latitude | East longitude |
| --- | --- | ---: | ---: |
| Haworth | `haworth` | −86.800° | 337.200° |
| Nobile Rim 1 | `nobile-rim-1` | −85.400° | 37.300° |
| Malapert Massif | `malapert` | −85.964° | 357.681° |

These coordinates are repository research points. Confirm their exact positions inside the archived footprints. The current application defaults to a 2 m lander antenna, 5 m relay mast, an 8 km deployment radius, and a 2.4 GHz surface link. These are app assumptions. Match each benchmark's own heights and band when reproducing it. The existing demo runs from 2026-11-01 to 2026-12-01 UTC, with an exclusive end and a 30-minute step. That date range is relevant to DTE geometry. A fixed surface-to-surface geometry map can be generated without a mission date. [Repository README](README.md) · [Mission defaults](moonmesh/models.py) · [Research points](moonmesh/data.py) · [Prior training note](first%20thing%20module%20training.md) · [Project deliverables](MOONMESH_DELIVERABLES.md)

## 2. Define exactly what the model predicts

| Output | Unit or class | Interpretation |
| --- | --- | --- |
| Terrain elevation and slope | m, degrees | Observed or derived GIS layers. Load these from the DEM when available. |
| Landform | Rim, wall, crater floor, ridge, plain, other, unknown | Optional semantic segmentation task. Use crater geometry, DEM derivatives, and imagery to create reviewed labels. |
| Geometric visibility | Clear, occluded, unknown | Whether the curved-body surface-to-surface ray clears the known terrain between antenna phase centers. |
| Minimum signed terrain clearance | m | Smallest modeled gap between the ray and terrain. Negative means a terrain intersection. Save obstruction distance and coordinates separately. |
| Received power or path loss | dBm or dB | Continuous RF target for a specified propagation model, frequency, and link geometry. Record which quantity is used and its convention. |
| RF link margin | dB | Predicted received power minus the profile-specific receiver sensitivity. Coverage requires the configured margin. |
| Operational coverage | Covered, below margin, unknown | Receiver can meet the declared link threshold under the modeled configuration. An occluded ray can have usable modeled power through diffraction. A clear ray can have inadequate margin. |
| Network coverage | Best transmitter, maximum or selected serving power, serving count | Aggregate from individual transmitter predictions under one documented serving rule and active set. Keep candidate IDs as metadata. |

Use `terrain_dead_zone` for terrain obstruction and `rf_dead_zone` for known insufficient service margin. For causal attribution, compare terrain-aware RF with a matched unobstructed reference: a terrain-caused RF outage is a failed terrain-aware link that would meet threshold in the unobstructed model under identical endpoints and radio settings. An RF model that only applies free-space loss and a binary LOS gate can produce a planning proxy. Record that simplification with the label. Keep nodata, truncated profiles, and invalid scenarios out of both positive and negative training classes.

### Minimum inputs and feature behavior

- A mapped area with a valid lunar projection and georeferenced DEM.
- Transmitter coordinates and height above local ground, receiver height above local ground, and the allowed search radius. Use distance and bearing from the transmitter for each receiver. Encode circular angles with sine and cosine when useful.
- Carrier frequency, transmit power, transmitter and receiver gains, losses, required margin, and receiver sensitivity. Include link direction and antenna patterns if supported. Store a validated complete profile with each scenario.
- DEM and nodata mask, optionally with slope, relative elevation, curvature, local relief, roughness, effective resolution, observation counts, and data-source confidence. A local receiver patch can miss the blocking ridge between the endpoints; include the profile or sufficient path context.
- Coverage for a novel transmitter must be evaluated on held-out transmitter locations. An atlas-only model that sees a DEM and predicts best coverage under one fixed mast catalog supports that fixed-network task.
- Power, battery, DTE, scheduled station availability, and orbital relays are downstream or separate extensions. The first training target is the spatial surface link.

## 3. Dataset catalog and download links

### Coverage, labels, and ephemerides

| Dataset | Contents and role | Download or reference |
| --- | --- | --- |
| **LunarRF atlas v2.0** | WPI simulation for 80–90°S across 1.8, 2.58, 3.65, 5.855, and 25.375 GHz. Contains terrain context, best power, serving-mast information, LOS layers, 11,827 mast candidates, validation records, and sensitivity runs. Public files contain 160 m pole mosaics and 20 m region windows. The authors list full pole-wide 20 m tiles on request. The record is CC BY 4.0. | [Release](https://zenodo.org/records/23301640) · [Paper](https://arxiv.org/html/2610.34554v1) · [Manifest](https://zenodo.org/records/23301640/files/MANIFEST.txt?download=1) · [Checksums](https://zenodo.org/records/23301640/files/SHA256SUMS?download=1) |
| **LunarRF regional archives and context** | Matched regional terrain, best-power, best-mast, LOS and serving-count layers. Fetch Haworth first, then Malapert and Nobile. Read the manifest before selecting bands or extracting files. | [Terrain context](https://zenodo.org/records/23301640/files/lunarrf_atlas_context_v2.0.tar?download=1) · [Haworth, part 1](https://zenodo.org/records/23301640/files/lunarrf_atlas_regions_20m_part1_v2.0.tar?download=1) · [Malapert, part 2](https://zenodo.org/records/23301640/files/lunarrf_atlas_regions_20m_part2_v2.0.tar?download=1) · [Nobile Rim 1, part 3](https://zenodo.org/records/23301640/files/lunarrf_atlas_regions_20m_part3_v2.0.tar?download=1) |
| **NASA LOLA Average Earth Visibility** | Fraction of modeled hourly samples over about 18.6 years with any part of the Earth disk visible. Use for a long-term DTE geometry cross-check. Surface coverage labels and mission-date blackout intervals require their own inputs. | [Catalog](https://pgda.gsfc.nasa.gov/products/69) · [75–90°S, 120 m IMG](https://imbrium.mit.edu/EXTRAS/ILLUMINATION/IMG/AVGVISIB_75S_120M_201608_EARTH.IMG) · [120 m label](https://imbrium.mit.edu/EXTRAS/ILLUMINATION/IMG/AVGVISIB_75S_120M_201608_EARTH.LBL) · [85–90°S, 60 m IMG](https://imbrium.mit.edu/EXTRAS/ILLUMINATION/IMG/AVGVISIB_85S_060M_201608_EARTH.IMG) · [60 m label](https://imbrium.mit.edu/EXTRAS/ILLUMINATION/IMG/AVGVISIB_85S_060M_201608_EARTH.LBL) |
| **JPL NAIF SPICE and Horizons** | Mission-date geometry, time conversion, and lunar frame definitions. Use only for the optional DTE and solar tracks. Horizons is a comparison interface for matched geometry conventions. | [DE440s](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp) · [Frame kernel](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/satellites/moon_de440_250416.tf) · [Orientation kernel](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/moon_pa_de440_200625.bpc) · [Leap seconds](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls) · [Horizons API](https://ssd-api.jpl.nasa.gov/doc/horizons.html) |
| **RadioLunaDiff** | Synthetic elevation plus ray-traced RF examples at 415 MHz and 5.8 GHz. Useful for designing an individual-transmitter model or testing pretraining. The public repository exposes code and examples. A download of the full training corpus was unverified. | [Project](https://radiolunadiff.github.io/) · [Code](https://github.com/anderspearson206/RadioLunaDiff) · [Paper](https://arxiv.org/abs/2509.14559) |

**LunarRF label guidance.** Start at 1.8 GHz with Haworth's `Haworth__best_prx_1p800GHz.tif`, `Haworth__best_id_1p800GHz.tif`, `Haworth__best_los_1p800GHz.tif`, `Haworth__count_1p800GHz.tif`, and `Haworth__los_count.tif`. Confirm numeric units, sentinel values, threshold, projection, and LOS meaning from the run metadata. The best-server LOS flag describes the selected link. LOS count can indicate whether a different candidate is visible. Confirm the active-set and aggregation semantics before interpreting either. The 160 m best-power mosaics use block maxima, which can hide small holes. Use the 20 m layers for dead-zone boundaries. The atlas training inputs must exclude best-mast ID, LOS counts, serving counts, and similar simulated outputs if those are targets or if the model would lack them at inference. [LunarRF methods](https://arxiv.org/html/2610.34554v1) · [File manifest](https://zenodo.org/records/23301640/files/MANIFEST.txt?download=1)

### Terrain, imagery, and landforms

| Dataset | Contents and role | Download or reference |
| --- | --- | --- |
| **NASA GSFC orbit-adjusted LOLA topography, PGDA 90** | Regional 20 m DEM for 80–90°S with slope, laser observation counts, height error and effective resolution layers. Use this as the regional terrain source. Keep native quality layers with the pixels. | [Catalog](https://pgda.gsfc.nasa.gov/products/90) · [20 m DEM](https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/LDEM_80S_20MPP_ADJ.TIF) |
| **NASA GSFC LOLA 5 m site terrain, PGDA 78** | Targeted DEM, slope, uncertainty, observation counts and point clouds. Use for local terrain shape and coverage sensitivity at rims and narrow ridges. | [Catalog](https://pgda.gsfc.nasa.gov/products/78) · [Haworth](https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Haworth/Haworth_final_adj_5mpp_surf.tif) · [Nobile Rim 1, Site06](https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site06/Site06_final_adj_5mpp_surf.tif) · [Malapert Massif, Site23](https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site23/Site23_final_adj_5mpp_surf.tif) |
| **NASA LOLA PDS products in the app** | 20 m near terrain and 240 m far terrain. Reproduce this baseline before changing terrain inputs. Keep .LBL beside .IMG. | [20 m IMG](https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/LDEM_85S_20M.IMG) · [20 m LBL](https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/LDEM_85S_20M.LBL) · [240 m IMG](https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/LDEM_75S_240M.IMG) · [240 m LBL](https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/LDEM_75S_240M.LBL) |
| **LROC NAC stereo DTMs** | Local elevations from stereo imagery. Select by footprint and compare controlling obstructions with LOLA. Some products are aligned to LOLA. | [Footprints](https://data.lroc.im-ldi.com/lroc/view_rdr/SHAPEFILE_NAC_DTMS) · [Nobile–Malapert](https://data.lroc.im-ldi.com/lroc/view_rdr_product/NAC_DTM_NOBILE01) · [Northwest Malapert](https://data.lroc.im-ldi.com/lroc/view_rdr/NAC_DTM_MALAPERT01) |
| **LROC NAC and KPLO ShadowCam** | Optical mosaics for examining landforms in illuminated and shadowed terrain. Attach illumination metadata when using imagery as a feature. | [LROC controlled mosaics](https://data.lroc.im-ldi.com/lroc/view_rdr/SHAPEFILE_CONTROLLED_MOSAICS) · [ShadowCam mosaics](https://pds.shadowcam.im-ldi.com/derived/cmosaic/) |
| **Robbins crater database** | Large-crater geometries and sizes for landmark context and weak terrain labels. | [USGS download](https://astrogeology.usgs.gov/search/map/moon_crater_database_v1_robbins) |
| **Fassett south-pole crater catalog** | Denser automated crater detections with uneven coverage and reported statistical reliability above roughly 15 m. Build labels only in reviewed areas. | [GeoPackages and supporting files](https://zenodo.org/records/18674422) |
| **NASA–IBM SomBench** | Machine-learning-ready co-registered imagery and terrain modalities plus downstream crater benchmarks. A candidate for terrain perception pretraining. | [Hugging Face collection](https://huggingface.co/collections/nasa-ibm-ai4science/lunar-fm-ml-ready-benchmark-dataset-sombench) · [NASA technical record](https://ntrs.nasa.gov/citations/20260008699) |
| **JAXA Kaguya LALT south-pole elevation** | Alternate laser-altimeter terrain for a cross-source check of broad relief and horizon directions. | [JAXA DARTS grid](https://darts.isas.jaxa.jp/datasets/darts:sln-l-lalt-5-topo-gt-sp-num-v2.0/) |
| **NASA/JAXA SLDEM2015** | Mid-latitude DEM for geographic extensions. Its 60°S–60°N coverage excludes this pilot. | [Product catalog](https://pgda.gsfc.nasa.gov/products/54) |
| **USGS Unified Geologic Map of the Moon** | Global 1:5 million geologic units suitable for context. Operational landform boundaries require finer terrain sources. | [USGS GIS data](https://astrogeology.usgs.gov/search/map/unified_geologic_map_of_the_moon_1_5m_2020) |

### Reference studies and validation targets

- NASA Tall Lunar Towers reports 185 km² geometric coverage from a 10 m tower and 325 km² from a 50 m tower at the same Connecting Ridge placement and 2 m receiver height. Recreate matched terrain, extent, heights, and area calculation before comparing. NASA's landing-site trade study gives a second 4 m versus 20 m viewshed case at a different setup. [Tall Lunar Towers](https://ntrs.nasa.gov/citations/20230012634) · [NASA viewshed trade study](https://ntrs.nasa.gov/api/citations/20230014675/downloads/20230014675%20Final%20Document%20121423.pdf)
- JPL used 5 m LOLA terrain for QGIS viewsheds and separately generated FEKO path-loss results with a 15 m transmitter, 2 m receiver, and 442.5 MHz or 2.245 GHz. Use the public study as a configuration and method benchmark. Standalone RF rasters were unverified. [JPL study](https://www-robotics.jpl.nasa.gov/media/documents/A_Rapid_Low-Cost_Path_to_Lunar_Communication_and_Navigation.pdf)
- NASA/Nokia's Future Lunar Surface Network Study publishes path gain, SNR, and rate comparisons for alternate antenna heights at Connecting Ridge Site 1. Use section 8.1.3 for a propagation-model comparison. [NASA/Nokia report](https://ntrs.nasa.gov/citations/20240003815)
- Nokia IM-2 returned operational data from a brief power window. Treat the report as mission context. The research pass found no public, georeferenced south-pole drive-test corpus for independent validation of the modeled coverage. [Nokia mission results](https://www.nokia.com/newsroom/nokia-and-intuitive-machines-deliver-first-cellular-network-to-the-moon-achieve-some-key-mission-objectives/)

## 4. Step-by-step research and training plan

### Step 1. Fix the experiment before downloading large archives

1. Define one first experiment: Haworth, georeferenced 20 m receiver grid, one documented reference radio profile, and tower placement, height, and receiver height explicit in the input.
2. Keep a published LunarRF 1.8 GHz regional comparison in the pilot. A per-tower training task needs source conditioning and per-tower labels. Use the released atlas as a benchmark for the fixed-catalog task, or aggregate individually simulated towers under matched assumptions when comparing with it.
3. Pick the test output ahead of time: a LOS mask, a continuous clearance raster, a predicted RF margin raster, a dead-zone mask and an uncertainty mask. The deterministic LOS map is the minimum viable deliverable.
4. Define which modeling trade the team cares about: inference latency for a new tower, accuracy of the mapped boundary, or fast exploration over many tower positions. Measure all three when feasible.

### Step 2. Acquire and record sources

1. Get the LunarRF manifest and checksums, terrain context, Haworth region part 1, and Haworth PGDA 78 terrain and uncertainty files. Use only the necessary windows from PGDA 90. Download into an ignored local data directory.
2. Log each source's product ID, URL, version, acquisition and download dates when available, provider, checksum, size, license or reuse conditions, citation, geographic footprint, vertical datum, projection, units, nodata and processing level.
3. Use temporary files, validate size and checksums, then rename completed downloads. Retain originals. Give derivative datasets their own IDs and record the source IDs and generating command.
4. Print a one-page inventory with expected and observed bounds, data types, nodata counts, expected licenses, checked raster overlap, and reasons for excluding any layer. Stop only the affected training stage when a required source fails. For example, proceed with LOLA-generated LOS while resolving a missing LunarRF archive.

### Step 3. Align terrain and coverage grids

1. Use one explicit lunar south polar stereographic grid, with its lunar reference radius, frame, pixel origin, bounds, and axis order recorded. Normalize longitude for comparisons and exports. Preserve the source convention in metadata.
2. Read PDS labels for dimensions, byte order, multiplier, vertical reference, and fill values. The app currently decodes its PDS grids as signed little-endian 16-bit and multiplies valid heights by 0.5 m. Verify that rule per product. GeoTIFF scales and nodata have their own metadata.
3. Compare DEM elevations using a common vertical reference. Audit the approximation when mixing DE421-derived terrain and DE440-based mission geometry. Verify pixel centers, northing orientation, exact placement and three manually checked landmarks before generating labels.
4. Keep continuous variables as floating-point. Use suitable continuous resampling for elevation or power, and nearest neighbor or documented class aggregation for IDs and masks. Avoid resampling dBm by treating it as linear power. Apply a deliberate rule if aggregating RF data, such as area fraction or best power, and label that rule.
5. Use the 5 m DEM as a local terrain check or higher-resolution label generator. Keep a valid buffer around its footprint, harmonize overlap, and use a coarser exterior DEM for complete paths if justified. Preserve the source resolution map so a coarse-derived label is apparent.
6. Derive slope, local relief and curvature with declared neighborhood distances in meters. Add a nodata/interpolation mask. Inspect samples crossing map edges, polar seams, steep crater walls and the transition between terrain sources.

### Step 4. Generate individual-transmitter geometric labels

1. Sample transmitter candidates from diverse terrain: ridges, shoulders, plains, crater rims and floors. Include non-peak cases for training when plausible. Track which placements also satisfy deployment requirements. Generate candidates and splits before optimizing for coverage.
2. Sweep several antenna heights and receivers at matched ranges and bearings. Include representative positive and negative examples, plus links close to LOS and RF boundaries. Report how your sampling differs from natural deployment prevalence and preserve validation prevalence.
3. Reuse `moonmesh.science.surface_path` as the app baseline. For training, expose the minimum clearance and controlling coordinates, with the same antenna elevations and curved-body chord construction. Sample along the entire profile. Record sample spacing and source DEM at each section.
4. Validate exact cases with flat terrain, near and far endpoints, a single synthetic ridge, a ridge just below and then above the ray, equal and unequal antennas, an unknown cell, and an endpoint at a map edge. Compare finer spacing around the first real Haworth boundary. Flag paths that need terrain outside the available raster.
5. Save georeferenced `los`, `min_clearance_m`, `obstruction_distance_m`, and `valid_mask` layers. Keep separate metadata for rays that are clear, blocked, partially sampled, or outside the supported range. Record numerical tolerances and flag boundary samples for targeted review.

### Step 5. Generate RF labels and dead-zone masks

1. Start with a transparent free-space link budget and record received power, required sensitivity, available margin, and assumed losses. That gives an unobstructed reference for causal attribution.
2. Add a documented terrain propagation baseline, such as single-edge diffraction for a controlled proof of concept. For a stronger label generator, choose a tested multi-obstacle or ray-tracing method, pin its version, and declare reflection, diffraction, frequency, ground material, polarization and antenna assumptions.
3. Store path loss or received power continuously. Generate the covered/uncovered mask by thresholding the configured link margin. Retain geometric LOS alongside it. Save the specific threshold in the scenario metadata, rather than inheriting defaults across studies.
4. Keep Earth links and surface links in their own scenario namespaces. For the optional Earth extension, compute azimuth, elevation, and terrain-horizon clearance at each time, add station-aware RF if specified, and retain time-dependent outage intervals. Benchmark long-term visibility only under geometry comparable to NASA's average product.
5. Compare three hand-picked Haworth paths with the underlying terrain profile, including one clear, one ridge-blocked and one near-boundary case. Reproduce at least one study geometry before advertising agreement with published results. Agreement between simulators measures consistency under assumptions.

### Step 6. Design the training dataset and prevent leakage

Use a scenario per transmitter placement, terrain version, height pair and radio profile. Store compact indexed metadata in Parquet and map targets in GeoTIFFs, or use tiled arrays for training and retain GeoTIFF round-trips for review.

```text
Scenario
  scenario_id, parent_scene_id, site_id, spatial_block_id, transmitter_id
  source_product_ids, terrain_grid_id, generator_version, code_commit
  transmitter_x_m, transmitter_y_m, tx_height_agl_m, rx_height_agl_m
  frequency_ghz, tx_power_w, tx_gain_dbi, rx_gain_dbi, fixed_loss_db
  receiver_sensitivity_dbm, required_margin_db, max_range_m

Receiver or raster target
  scenario_id, receiver_x_m, receiver_y_m, distance_m, bearing_deg
  terrain_valid, label_valid, geometric_los, min_clearance_m
  obstruction_x_m, obstruction_y_m, obstruction_distance_m
  path_loss_db, received_power_dbm, link_margin_db, rf_covered
  terrain_caused_rf_outage, quality_flags
```

1. Group all versions of the same geographic patch and transmitter together. Set a spatial buffer at least as large as the feature footprints and account for shared transmitter-to-receiver terrain profiles. Keep augmentations, resolution variants and multiple frequencies for the same parent scene together.
2. For a small Haworth pilot, reserve buffered blocks and whole transmitters for local checks. Expand to Nobile and Malapert and rotate site holdouts. A full site holdout tests geographic transfer. A transmitter holdout at a known site tests placement generalization.
3. Keep held-out regions out of preprocessing fits, feature selection and threshold tuning. Publish the scenario IDs, split manifest, data hashes, and seed. Count unique transmitters, distinct spatial blocks, valid pixels, blocked pixels, boundary pixels, and missing terrain by split.
4. Exclude identifiers and completed physics outputs from the input if they leak the target. For example, a minimum clearance computed by exhaustive LOS tracing is already enough to decide geometric LOS. If the model uses that feature, charge its computation to inference and frame the task as RF estimation rather than acceleration of the geometry.

### Step 7. Train the simplest model that answers the question

| Stage | Inputs and outputs | Decision |
| --- | --- | --- |
| Deterministic baseline | Curved-body viewshed and a documented RF calculation | Establish the runtime, expected coverage, and boundary behavior. Use this as the ground reference for simulator imitation. |
| Simple learned baseline | Tabular distance, height, bearing, terrain features and inexpensive profile summaries. Classifier for LOS or RF coverage, regressor for clearance or RF margin. | Use a small tree model or similarly interpretable baseline. Compare accuracy and true end-to-end runtime. Include a distance-only or free-space baseline. |
| Spatial model | Aligned DEM patch, transmitter-location channel, height and RF conditioning, distance/bearing or route context, and validity mask. Outputs per-pixel clearance and RF margin with optional LOS supervision. | Consider a compact U-Net only after split quality and label generation are demonstrated. Ensure the model window captures terrain between endpoints and retain a margin-regression head if the threshold will be adjustable. |
| Optional landform model | DEM derivatives, co-registered imagery, reviewed segmentation masks | Start with terrain rules and annotated audit patches. Add SomBench pretraining if labeled imagery is the bottleneck. Use separate validity and class metrics. |

Mask unknowns in the loss and metrics. Start with robust regression for continuous values and class-weighted classification for imbalanced masks as needed. Tune calibration and coverage decisions on validation only. Preserve geographic and scenario splits for any later fine-tuning. For experiments using synthetic terrain, compare a real-LOLA-only training run with the same model initialized or augmented with synthetic examples, and test both on held-out real LOLA geography.

### Step 8. Evaluate maps before integrating

- Report dead-zone recall and precision, per-class IoU, area error, boundary distance in meters, and false-covered area. False-covered pixels are especially important for the planning decision. Report metrics separately for terrain obstruction and RF outage. For margin regression, report MAE, bias, and near-threshold errors.
- Report results by site, range, transmitter height, terrain type, sampling quality, and distance to the coverage boundary. Show representative true/false positives and negatives over the DEM and trace one contested path. Include the proportion classified as unknown.
- Evaluate a separate cross-source check using 5 m LOLA or overlapping NAC DTM terrain. Record disagreement attributable to terrain density, relief, footprint or registration. Treat shared LOLA inputs in LunarRF and MOONMESH as shared evidence.
- Benchmark CPU inference and end-to-end runtime with warm and cold caches, including acquisition only if it is part of the advertised runtime. Include raster reads, feature generation, postprocessing and any exact boundary verification. Compare against the deterministic baseline on identical windows.
- Publish raw evaluation tables and a map package. Choose an acceptance threshold from project needs before the final evaluation. If the learned model has no useful measured benefit, ship the deterministic map.

### Step 9. Integrate the map with the MOONMESH workflow

1. Use the existing map and mission UI as the entry point. Add a surface coverage mode with site and transmitter selection, antenna heights, radio profile, band and threshold. Show the active model, terrain source and resolution, map legend, validity mask and benchmark provenance. Keep site comparison and DTE timelines as related views.
2. Add independent layer toggles for terrain elevation, slope or landforms, geometric LOS, RF margin, covered/uncovered areas, known terrain issues, and optional serving count. Support click-to-inspect coordinates, terrain source, both antenna heights, obstruction profile, predicted margin and state. Use a distinct unknown color and pattern.
3. Cache deterministic and model results by terrain version, projection, grid extent, transmitter coordinates, both antenna heights, propagation model and radio configuration. A threshold-only change can reclassify an existing continuous margin layer. Serve stale-response protection when a user changes placement quickly.
4. Include loading, missing data, no-valid-pixels, no-link, and out-of-domain states. If the input is outside the trained range or close to a significant coverage boundary, use deterministic verification where supported. Restrict map claims to evaluated settings.
5. Export georeferenced raster layers and a scenario metadata sidecar. Include sources, checksums, display threshold, masks, units, nodata, model version, code version, quality fields and assumptions. Make a small offline Haworth scenario and screenshot for the demo.
6. When handing candidate links to relay optimization, check feasible links against the deterministic chain. The existing optimizer evaluates actual two-hop power and DTE timelines for each screened candidate. A static best-server coverage percentage cannot replace relay recovery over a mission.

## 5. Repository entry points, deliverables, and acceptance

| File or component | Reuse or proposed deliverable |
| --- | --- |
| [`moonmesh/data.py`](moonmesh/data.py) | Existing NASA PDS and SPICE download/crop entry point. Reuse the source conventions. Add versioned training data preparation separately when needed. |
| [`lunar_core/terrain.py`](lunar_core/terrain.py) and [`lunar_core/horizon.py`](lunar_core/horizon.py) | Existing projection, sampling and observer-horizon logic. The sky horizon and a finite surface-link path solve different geometries. |
| [`moonmesh/science.py`](moonmesh/science.py) and [`moonmesh/network.py`](moonmesh/network.py) | Existing surface chord, visibility, free-space RF, outage metrics and relay optimization baselines. |
| [`moonmesh/server.py`](moonmesh/server.py) and [`frontend/`](frontend/) | Existing configuration, mission, compare and sensitivity routes and map/timeline UI. A dedicated coverage route and typed response can be proposed after the pilot schema is stable. |
| Proposed `training/data_manifest.json` and `training/splits.json` | Versioned source catalog, generated sample inventory and grouped train/validation/test memberships. |
| Proposed training commands and artifacts | Reproducible download/preprocessing, labels, train, evaluate and export commands. Store model configuration, environment versions, small example, metrics and a model card. Keep large artifacts outside Git unless explicitly published. |

- [ ] Haworth DEM, context and benchmark rasters overlap on the intended grid. Source names, licenses, hashes, and nodata conventions are recorded.
- [ ] Map distinguishes elevation/landforms, geometric LOS, RF margin, RF dead zones, and unknown cells.
- [ ] A changed transmitter position or antenna height yields an explainable map change and stores the scenario configuration.
- [ ] Deterministic labels pass analytic geometry checks and real terrain inspection. Near-boundary convergence and sensitivity are recorded.
- [ ] Per-transmitter pilot labels preserve obstruction locations and continuous margins. A matched LunarRF comparison uses the same radio case, or reports the differences explicitly.
- [ ] Training, validation, and test sets group geography and transmitters, with published coverage prevalence and metrics.
- [ ] Learned and deterministic maps are compared using identical test cases. Exports preserve georeferencing, scenario, units, masks, and provenance.
- [ ] Demo starts at Haworth, picks a mast, shows why a crater or ridge loses coverage, changes the mast or location, and inspects the changed boundary. If using MOONMESH's existing mission flow, show how the validated surface hop feeds the separate relay timeline.

## 6. Copy-ready instructions for Claude

> Act as the ML and geospatial engineer for MOONMESH. Your task is to produce an executable, step-by-step training plan for the primary feature in this document: mapping lunar south-pole surface coverage and dead zones from terrain and a specified transmitter/receiver configuration. Use the named repository files and dataset links as evidence. Begin with Haworth and explain exactly which archives and raster layers to fetch, what each supplies, and how to verify footprints, units, projections, valid pixels, manifest entries and checksums.
>
> Give me two concrete experiments. First, reproduce a small fixed-network LunarRF comparison using matched Haworth terrain and published RF layers. Second, train a transmitter-conditioned model using per-tower geometric and RF labels generated from LOLA. Explain exactly how to create those labels and how to use LunarRF, NASA's tower studies, and the JPL viewshed analysis as benchmarks. Describe how to handle missing data, blockage versus diffraction, and RF margin thresholds. Include a deterministic viewshed and link-budget baseline, then specify which small learned model to try and what would justify a more complex model.
>
> For each stage give the inputs, outputs, runnable commands or precise pseudocode, desired directory structure, dependencies, data schema, expected sanity checks, and failure or fallback path. Include geographic and transmitter-based splits, held-out Nobile and Malapert evaluation, accuracy and runtime metrics, and what qualifies as a verified result. State what can run locally in a small pilot and what scales up. Report simulated labels as simulated, and identify any release assets or exact overlaps you could not verify. Keep direct-to-Earth blackouts, power, and relay ranking as downstream context. Where a choice depends on inspecting the files, give the inspection command and the decision rule instead of inventing metadata or metrics.
