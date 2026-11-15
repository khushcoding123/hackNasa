# Step 07: Experiment A, the LunarRF comparison at Haworth (1.8 GHz)

Goal: check how closely our deterministic chain (step 06) reproduces LunarRF's published Haworth layers on the same terrain and radio settings. This tests our label generator; there is no machine learning here. Expect about 2 hours of tracing. Run it in resumable chunks.

1. Fill lunarrf_1p8 in `training/configs/radio_profiles.yaml` from `reports/lunarrf_findings.md`, with a comment naming the source of each value:
   - frequency_ghz 1.8; tx_power_dbm 30.0; tx_gain_dbi 0.0; rx_gain_dbi 0.0; receiver_sensitivity_dbm -94.40475498807768; max_range_m 22040.
   - fixed_loss_db 0 and required_margin_db 0, each commented as a documented assumption: the published budget lists no fixed or system loss, snr_required_db is 0, and the coverage threshold equals the sensitivity.
   - In `training/configs/exp_a_lunarrf_haworth.yaml`, set the mast height to 4.0 m and the receiver height to 1.5 m (from radio_params.json), replacing tx_heights_m and rx_height_m.
2. Masts: from `interim/lunarrf/meta/data/atlas_base/candidates.csv`, select the masts whose 22,040 m range reaches the LunarRF Haworth window (expected 267). Mast ground height is our DEM at the mast (bilinear). Record its difference from the catalog's z_m.
3. Write `training/scripts/05_exp_a_lunarrf.py`. For each mast, trace with los.py `mode="fast"`: 10 m spacing, 1.8 GHz wavelength, h_tx 4, h_rx 1.5, max 22,040 m, over the Haworth window pixels only. Compute received power with the step 06 model. Update running aggregates over the Haworth window:
   - best_prx: max received power
   - best_id: argmax cand_id; on ties, the lower cand_id
   - best_los: LOS of the best server
   - los_count: masts with LOS
   - count: masts with prx >= threshold

   Checkpoint the aggregates and the list of finished masts after every mast, so the run can resume. Do not store per-mast rasters.
4. Compare with the published layers on valid pixels. Compare mainly against the no-reflection layers (Haworth__best_norefl, best_id_norefl, best_los_norefl, count_norefl), because our model has no ground reflection. Compare against the reflection layers second. Treat a LunarRF count of 0 as zero masts.
   - Any-LOS agreement: (our los_count > 0) vs (LunarRF los_count > 0). Confusion matrix and IoU.
   - best_los agreement, and the fraction of pixels with the same best_id.
   - dB difference (ours - LunarRF best_norefl): histogram, median, MAD, p5, p95 and a map.
   - Covered/uncovered at -94.405 dBm: confusion matrix, IoU, false-covered km² and missed-covered km².
   - A disagreement map over the hillshade.
5. Write `reports/exp_a/report.md` with a diagnosis section:
   - a constant dB offset suggests power or gain bookkeeping
   - disagreement clustered on crater walls or ridges suggests terrain resolution or the diffraction model (LunarRF uses delta-Bullington; we use a single knife-edge)
   - a shifted band suggests registration

   Do not tune parameters to force agreement. Mark both LunarRF's layers and ours as simulated.

## Gates

1. lunarrf_1p8 loads with the config loader (no null fields).
2. Every selected mast was processed. Report the count (expected 267).
3. All the comparison tables and maps above were written.

Add these as warnings, not failures: any-LOS IoU below 0.8, and an absolute median dB difference above 6 dB.

Status file: `reports/pipeline/step_07.json`.
