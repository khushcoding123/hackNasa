# Step 14: Test on new sites (Malapert and Nobile Rim 1)

1. Data:
   - Run `00_download.py sites` to see which LunarRF region archives contain Malapert_Massif and Nobile_Rim_1. Malapert spans parts 1 and 2.
   - Download the parts you need with `00_download.py lunarrf --parts ...`.
   - Extract both sites with `extract --site <name> --band 1p800GHz`.
   - Fetch a PGDA 90 window for each with `pgda90-window --bounds-from <site best_prx tif> --buffer-m 25000`.
   - Download PGDA 78 with `pgda78 --site malapert` (Site23) and `pgda78 --site nobile-rim-1` (Site06).
2. Alignment: run the step 04 alignment (`02_align.py`) for each site, adding a --site option if needed. Registration must pass.
3. Sampling and labels: sample transmitters with the same rules as step 08 (75 per site is enough), and generate labels with the step 09 builder.
4. Cross-site test: evaluate the Haworth-trained LightGBM (and the U-Net, if it exists) on both sites without retraining, using `10_evaluate.py`.
5. Rotations from `training/splits.json`:
   - train on Haworth + Malapert, test on Nobile
   - train on Haworth + Nobile, test on Malapert
6. Write `reports/sites/report.md` with per-site metrics side by side, and say where performance drops (terrain stratum, range, height).

## Gates

1. Both sites passed registration.
2. Labels were built for both sites.
3. The cross-site metrics table and both rotations are reported.

Status file: `reports/pipeline/step_14.json`.
