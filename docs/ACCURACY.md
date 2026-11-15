# Accuracy against independent reference data

Run `python -m ora accuracy` to reproduce. It writes `docs/figures/ora_accuracy.json` with the date, commit, diff hash and the SHA-256 of every model. Numbers below are from 14 November 2026 (base commit `18ca6c7`, branch `repair/phase0-baseline`).

## Protocol

Tuning against a test set makes any accuracy figure meaningless, so the lunar windows were split before any of this work:

| Split | Windows | Use |
| --- | ---: | --- |
| DEV | 57 | every method choice and all model training |
| HOLD | 8 | two new 2 × 2 blocks carved out of the old training windows (seed 2027); their 24 neighbouring windows were dropped as a buffer, so no development terrain touches them; scored once |
| LEGACY | 16 | the original sealed test windows; scored once (reported separately because they were inspected before this work) |

**Truth for coverage** is dense line of sight. For 150 sites per window, it draws 6,000 random ground points within 8 km of a 10 m mast and checks each one with a 2 m receiver, sampling terrain every 20 m pixel. That is 12,150 sites and 73 million line-of-sight checks. It is independent of the radial sweeps the app uses.

## Results

All "now" figures are on data never used to train or tune them (HOLD · LEGACY).

| Component | Reference | Before | Now |
| --- | --- | ---: | ---: |
| Sun and Earth position | JPL Horizons, 1,452 samples | 100% within tolerance | 100% within tolerance (unchanged) |
| How much ground a relay sees, as the app computes it | dense line of sight | 53.9% · 59.8% R² (old surrogate) | **98.8% · 98.0% R²**, rank 99.5% · 99.6%, mean error 0.9 · 1.1 points |
| Fast learned view estimator (24-ray sweep + correction model) | dense line of sight | — | **98.9% · 98.9% R²**, 100% · 99.8% of sites within 5 points |
| Ranking of relay candidates in the app | dense line of sight (rank correlation) | 74.0% · 82.1% (surrogate 80% + Earth prior 20%) | **99.5% · 99.6%** |
| Coverage-first relay selection in the app | exact greedy | 35.2% vs 63.4% coverage on Shackleton (about 55%) | **exact greedy over every candidate** (same answer as the reference; 1 s instead of 7 s) |
| Learned selector that needs no line-of-sight matrix | exact greedy, 4 / 8 / 12 relays | 79.6–90.3% · 74.5–87.1% (old gain model) | **94.3–95.7% · 94.7–95.3%** |
| Earth tower siting model | FCC registry, 8 sealed tiles | 88.2% AUC | 88.2% AUC (no feature set improved it; no longer used to rank lunar relays) |

### What changed

1. **The label was wrong by a few percent.** The "exact" viewshed used to train and test the old models samples terrain every 40 m along each ray. It steps over narrow ridges and reads 1.8–2.4 points high, which gives R² 95.9% · 92.1% against dense truth. Sampling every 20 m fixes it (`view_rings(..., step_px=1)`).
2. **The app now computes coverage instead of predicting it.** The old surrogate's input features already included a line-of-sight sweep that cost as much as the exact answer. The app now ranks candidates by the computed 8 km viewshed share (`ora/scenario.py`, `SITING_METHOD`).
3. **Coverage-first selection is exact.** The surrogate shortlist kept only 40 candidates and lost almost half the reachable coverage. Computing every candidate's line-of-sight row turned out cheaper than computing the surrogate's features (`ora/selector.py`, `select_sites`).
4. **Two new models** (`ora_view_estimator`, `ora_gain_selector`) are trained on DEV dense truth and recorded in the model card with file hashes. They are research artifacts; the app does not depend on them.

The Shackleton placement is unchanged by the new ranking: 7 relays (4 chain, 3 spare), 2 of 3 mines linked after the worst failure, 47% ground covered. So are both twin results.

## Below 95%, and why

- **Learned selector: about 95%.** It ranges from 94.3% to 95.7% depending on the number of relays, against 95.2–95.8% in development cross-validation. Raising it further would mean tuning on the holdout, so it is reported as measured. The app does not use it; it selects relays exactly.
- **Earth tower siting model: 88.2% AUC.** Registered towers follow roads, power and towns as well as terrain. Leave-one-tile-out cross-validation on the training tiles peaked at 0.86 AUC over 12 model settings with the current features. Two settings with 13 extra multi-scale terrain features (wider relief, valley depth, flat-ground share, roughness, view rings) reached 0.85, no better, and the search was stopped there. Reaching 95% would need non-terrain data such as roads and population. It ranks lunar visibility at only 23–33% rank correlation, which is why it no longer influences lunar relay ranking.

## What this does not cover

- No measured lunar truth exists for communication outages, terrain obstruction (LOLA is both input and reference), temperature, batteries or dust. Dense line of sight checks the *calculation* on LOLA terrain, not the terrain itself.
- The selection reference is exact *greedy* on 500 sampled targets per window, not the true optimum.
