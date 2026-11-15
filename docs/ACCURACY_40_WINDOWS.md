# Accuracy on 40 held-out lunar terrain windows

Each "dataset" is one real 20 km LOLA terrain window near the lunar south pole. 40 windows: all 8 HOLD, all 16 LEGACY, and 16 buffer windows (fixed seed 40) that no model was trained or tuned on.

**Truth** is dense line of sight: for each of 150 sites per window, the share of 6,000 random ground points within 8 km that a 2 m receiver sees from a 10 m mast. **Accuracy %** is the share of a window's sites where the estimate is within 5 percentage points of that truth. R² and mean error (points of coverage) are also shown.

Two estimators are scored: the **fast view estimator** (`ora_view_estimator`, a 24-ray sweep plus learned correction) and the **app's computed viewshed** (120-ray sweep, 20 m steps, what the app ranks sites by). The estimator file's SHA-256 matches `ora/model_card.json`.

## Summary (mean of per-window values)

| Group | Windows | Fast estimator accuracy | Fast R² | Fast mean error | App viewshed accuracy | App R² | App mean error |
|---|---:|---:|---:|---:|---:|---:|---:|
| All | 40 | 99.8% | 98.9% | 0.90 pts | 98.7% | 98.6% | 0.92 pts |
| HOLD | 8 | 100.0% | 98.8% | 0.93 pts | 99.8% | 98.6% | 0.90 pts |
| LEGACY | 16 | 99.8% | 98.7% | 0.90 pts | 97.3% | 98.1% | 1.10 pts |
| Buffer | 16 | 99.9% | 99.1% | 0.87 pts | 99.4% | 99.2% | 0.74 pts |

## Every window

| # | Window (x_y km) | Split | Mean true coverage | Fast accuracy | Fast R² | Fast error | App accuracy | App R² | App error |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | -030_-090 | hold | 18.5% | 100.0% | 99.3% | 0.68 | 100.0% | 99.1% | 0.82 |
| 2 | -010_-090 | hold | 24.8% | 100.0% | 98.9% | 0.91 | 100.0% | 99.3% | 0.65 |
| 3 | -030_-070 | hold | 25.9% | 100.0% | 99.0% | 0.88 | 100.0% | 99.0% | 0.89 |
| 4 | -010_-070 | hold | 26.8% | 100.0% | 98.6% | 1.14 | 100.0% | 98.7% | 1.06 |
| 5 | -110_-050 | hold | 18.2% | 100.0% | 98.3% | 0.99 | 99.3% | 95.5% | 1.75 |
| 6 | -090_-050 | hold | 26.1% | 100.0% | 98.8% | 0.99 | 100.0% | 99.4% | 0.68 |
| 7 | -110_-030 | hold | 27.7% | 100.0% | 98.2% | 1.14 | 98.7% | 98.6% | 0.86 |
| 8 | -090_-030 | hold | 24.1% | 100.0% | 99.0% | 0.74 | 100.0% | 99.5% | 0.52 |
| 9 | +010_+090 | legacy | 23.8% | 99.3% | 98.6% | 1.14 | 94.7% | 96.3% | 1.87 |
| 10 | +030_+090 | legacy | 22.6% | 100.0% | 99.2% | 0.84 | 100.0% | 99.6% | 0.60 |
| 11 | +010_+110 | legacy | 15.4% | 100.0% | 98.1% | 0.91 | 100.0% | 95.8% | 1.34 |
| 12 | +030_+110 | legacy | 18.6% | 100.0% | 99.2% | 0.73 | 100.0% | 99.1% | 0.81 |
| 13 | +010_-010 | legacy | 37.2% | 96.7% | 97.6% | 1.56 | 68.0% | 91.5% | 3.81 |
| 14 | +030_-010 | legacy | 19.7% | 100.0% | 99.1% | 0.70 | 100.0% | 99.1% | 0.71 |
| 15 | +010_+010 | legacy | 28.1% | 100.0% | 99.1% | 1.00 | 98.7% | 98.0% | 1.47 |
| 16 | +030_+010 | legacy | 24.3% | 100.0% | 99.2% | 0.89 | 96.7% | 98.9% | 0.82 |
| 17 | -090_+070 | legacy | 22.7% | 100.0% | 98.4% | 0.89 | 100.0% | 99.1% | 0.66 |
| 18 | -070_+070 | legacy | 22.0% | 100.0% | 99.2% | 0.84 | 100.0% | 98.6% | 1.18 |
| 19 | -090_+090 | legacy | 19.1% | 100.0% | 98.7% | 0.82 | 100.0% | 99.5% | 0.51 |
| 20 | -070_+090 | legacy | 25.8% | 100.0% | 99.1% | 0.96 | 99.3% | 98.6% | 1.10 |
| 21 | +050_-110 | legacy | 19.5% | 100.0% | 98.9% | 0.72 | 100.0% | 99.5% | 0.54 |
| 22 | +070_-110 | legacy | 24.3% | 100.0% | 98.7% | 0.97 | 100.0% | 98.8% | 0.93 |
| 23 | +050_-090 | legacy | 18.4% | 100.0% | 98.5% | 0.75 | 100.0% | 99.0% | 0.65 |
| 24 | +070_-090 | legacy | 14.7% | 100.0% | 98.2% | 0.76 | 100.0% | 98.9% | 0.62 |
| 25 | -130_-010 | buffer | 22.4% | 100.0% | 99.0% | 0.81 | 100.0% | 99.7% | 0.47 |
| 26 | -110_-070 | buffer | 19.6% | 99.3% | 98.2% | 0.99 | 100.0% | 97.4% | 1.21 |
| 27 | -090_-070 | buffer | 27.9% | 100.0% | 99.2% | 0.96 | 100.0% | 99.6% | 0.67 |
| 28 | -090_-010 | buffer | 23.1% | 100.0% | 99.2% | 0.83 | 100.0% | 99.2% | 0.85 |
| 29 | -070_-070 | buffer | 22.9% | 100.0% | 99.3% | 0.78 | 100.0% | 99.7% | 0.52 |
| 30 | -070_-050 | buffer | 20.1% | 100.0% | 98.9% | 0.87 | 100.0% | 99.6% | 0.51 |
| 31 | -070_-030 | buffer | 26.7% | 100.0% | 98.9% | 0.92 | 95.3% | 97.5% | 1.00 |
| 32 | -050_-110 | buffer | 23.9% | 100.0% | 99.5% | 0.78 | 100.0% | 99.6% | 0.68 |
| 33 | -050_-090 | buffer | 17.0% | 100.0% | 99.5% | 0.71 | 100.0% | 99.6% | 0.66 |
| 34 | -050_-070 | buffer | 25.4% | 100.0% | 99.4% | 0.82 | 100.0% | 99.7% | 0.54 |
| 35 | -050_-050 | buffer | 25.0% | 100.0% | 99.4% | 0.76 | 100.0% | 99.4% | 0.70 |
| 36 | -030_-110 | buffer | 29.1% | 100.0% | 99.0% | 1.01 | 100.0% | 99.5% | 0.70 |
| 37 | -030_-050 | buffer | 35.1% | 98.7% | 99.0% | 1.16 | 95.3% | 97.7% | 1.67 |
| 38 | -010_-110 | buffer | 19.7% | 100.0% | 99.1% | 0.84 | 100.0% | 99.7% | 0.46 |
| 39 | +010_-110 | buffer | 16.1% | 100.0% | 98.3% | 0.86 | 100.0% | 99.5% | 0.47 |
| 40 | +010_-070 | buffer | 20.2% | 100.0% | 99.3% | 0.77 | 100.0% | 99.3% | 0.81 |

## Limits

- Truth is a simulation on LOLA terrain, not measured lunar radio data. It checks the calculation, not the terrain or real links.
- 150 sites per window: one window's accuracy moves by roughly 0.7 points per site. The lowest result is window 13 (+010_-010) for the app viewshed, 68.0% within 5 points (R² 91.5%), where mean true coverage is the highest of the legacy set (37%). The fast estimator stays at 96.7% there.
- Buffer windows neighbour HOLD/LEGACY windows, so their terrain partly overlaps; they are not geographically independent of those sets.
- This scores coverage estimation only. It is not the Earth-tower model, the thermal model, or Khush's Haworth LightGBM (its data, `shared-data/`, is packed but not unpacked on this machine).
- Run on commit `e263e4ad` with uncommitted changes; reproduce with `python scripts/accuracy_40_windows.py` (about 259 s). Raw numbers: `docs/ACCURACY_40_WINDOWS.json`.
