# Step 12: Compare with the NASA and JPL studies

1. Download these PDFs into `docs/references/` with exactly these names:
   - `tall_lunar_towers_20230012634.pdf`: NTRS citation 20230012634. Get the PDF link from the JSON at https://ntrs.nasa.gov/api/citations/20230012634 (downloads[*].links.pdf, relative to https://ntrs.nasa.gov).
   - `nasa_viewshed_trade_20230014675.pdf`: https://ntrs.nasa.gov/api/citations/20230014675/downloads/20230014675%20Final%20Document%20121423.pdf
   - `jpl_rapid_low_cost_path.pdf`: https://www-robotics.jpl.nasa.gov/media/documents/A_Rapid_Low-Cost_Path_to_Lunar_Communication_and_Navigation.pdf
   - `nasa_nokia_surface_network_20240003815.pdf`: NTRS citation 20240003815, found the same way as the first.

   If a download fails, record it as a warning and continue with the others.
2. Extract the text with pypdf. Write `reports/benchmarks/configs.md`: for each study, list every configuration value it states, with page numbers, and write "NOT STATED" where absent. Don't guess. Values to look for:
   - tower coordinates and heights, receiver height
   - terrain product and resolution, analysis extent
   - area method, frequencies, propagation tool
3. Terrain for Connecting Ridge: LunarRF has a Connecting_Ridge region. Extract its context DEM with `00_download.py extract --site Connecting_Ridge --band 1p800GHz`. Then fetch a PGDA 90 window with `00_download.py pgda90-window --bounds-from <Connecting_Ridge__dem.tif> --buffer-m 25000`. Align it the same way as Haworth (reuse 02_align functions with a site option). Do the same for any other study site whose coordinates are stated and inside 80-90 S.
4. Write `training/scripts/11_benchmarks.py`, which reproduces each study with our deterministic code:
   - Tall Lunar Towers at Connecting Ridge with a 2 m receiver: 10 m and 50 m towers, compared with 185 km² and 325 km² of visible area.
   - The trade study's 4 m vs 20 m case.
   - JPL: a 15 m tx / 2 m rx viewshed, plus our FSPL + knife-edge path loss at 442.5 MHz and 2.245 GHz, if a profile can be built from stated values.
   - NASA/Nokia section 8.1.3: path gain vs antenna height at Connecting Ridge Site 1.
5. Write `reports/benchmarks/report.md` with our value, theirs, the % difference, and the likely reasons for any differences (terrain source and resolution, extent, area method, heights). Label everything as simulator-to-simulator consistency, not validation against measurements.

## Gates

1. `configs.md` exists. Each study is either reproduced, or explicitly marked not reproducible with the missing values named.
2. The Tall Lunar Towers comparison is reported with % differences, or it is explained why it could not be run.

Status file: `reports/pipeline/step_12.json`.
