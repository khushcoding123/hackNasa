"""Pressure tests for the ORA models. ``python -m ora stress`` (about 10 minutes).

Each eval probes a different weakness and carries a pass criterion that was written down BEFORE the
numbers were seen (see ``CRITERIA``). Everything runs on sealed data: the 16 held-out lunar windows from
``ora.lunar_windows.split`` and the Earth tiles in ``ora.evaluate``, none of which the training code reads.
Models are loaded as saved and never refitted here.

Evals
  A  accuracy of the coverage surrogate on sealed lunar windows (pooled, per window, by view level)
  B  calibration: do predicted shares mean what they say?
  C  physical sanity: bounded, deterministic, more visible rings never lower the prediction
  D  terrain noise: add random height error to the DEM, recompute features, compare with clean truth
  E  resolution shift: coarser DEM (60 m) than the 20 m used in training
  F  mast-height shift: truth for 3 m and 20 m masts, model trained at 10 m
  G  relay selection: budgets 4, 8 and 12 against random and exact greedy, win rate and worst window
  H  siting prior on sealed Earth tiles, with a shuffled-label control
  I  transfer: does the Earth-trained prior rank lunar cells by real visibility?
"""
import json

import joblib
import numpy as np
from scipy.stats import spearmanr
from sklearn.metrics import r2_score, roc_auc_score

from .features import CHEAP, RINGS_M, horizon_features, rasters, view_rings
from .lunar_windows import RANGE_M, build, crop_xy, matrix, split
from .selector import compare
from .train import MODELS, top_recall

CRITERIA = {
    "A_r2_pct": (">=", 60.0), "A_spearman_pct": (">=", 75.0), "A_mae_points": ("<=", 8.0), "A_worst_window_spearman_pct": (">=", 60.0), "A_worst_window_mae_points": ("<=", 10.0),
    "B_calibration_slope": ("between", (0.85, 1.15)), "B_worst_decile_gap_points": ("<=", 6.0),
    "C_bounded_pct": (">=", 99.0), "C_monotone_in_rings_pct": (">=", 90.0),
    "D_noise_0.5m_r2_drop_points": ("<=", 5.0), "D_noise_2m_r2_drop_points": ("<=", 15.0),
    "D_correlated_2m_r2_drop_points": ("<=", 5.0), "D_correlated_5m_r2_drop_points": ("<=", 15.0),
    "E_res_60m_r2_pct": (">=", 40.0),
    "F_mast_3m_r2_pct": (">=", 40.0), "F_mast_20m_r2_pct": (">=", 40.0),
    "G_learned_efficiency_pct_b8": (">=", 80.0), "G_learned_win_rate_pct_b8": (">=", 90.0), "G_learned_worst_efficiency_pct_b8": (">=", 60.0),
    "G_shortlist_efficiency_pct_b8": (">=", 85.0),
    "H_auc_pct": (">=", 80.0), "H_top10_pct": (">=", 35.0), "H_shuffled_auc_pct": ("between", (40.0, 60.0)),
    "I_spearman_pct": (">=", 40.0),
}


def verdict(name, value):
    op, bound = CRITERIA[name]
    ok = value >= bound if op == ">=" else value <= bound if op == "<=" else bound[0] <= value <= bound[1]
    return dict(value=round(float(value), 1), criterion=f"{op} {bound}", passed=bool(ok))


def features_for(dem, px, cells, h_tx=10.0):
    layers = rasters(dem, px)
    c, r = np.rint(cells[:, 0]).astype(int), np.rint(cells[:, 1]).astype(int)
    cheap = np.column_stack([layers[name][r, c] for name in CHEAP]).astype(np.float32)
    rings = view_rings(dem, px, cells, RINGS_M + (RANGE_M,), h_tx=h_tx)
    return np.hstack([cheap, horizon_features(dem, px, cells, h_tx=h_tx), rings[:, :-1], np.full((len(cells), 1), h_tx, np.float32)]), rings[:, -1]


def pct(v):
    return 100.0 * float(v)


def accuracy(model, test_windows):
    built = [build(*w) for w in test_windows]
    X = np.vstack([matrix(w) for w in built])
    y = np.concatenate([w["label"] for w in built])
    p = model.predict(X)
    per_window = []
    for w in built:
        pw = model.predict(matrix(w))
        top = max(1, len(w["label"]) // 4)
        per_window.append(dict(r2=r2_score(w["label"], pw), spearman=spearmanr(w["label"], pw).statistic, mae=np.mean(np.abs(w["label"] - pw)), best_quarter=len(set(np.argsort(-w["label"])[:top]) & set(np.argsort(-pw)[:top])) / top))
    out = dict(cells=int(len(y)), windows=len(built), r2_pct=pct(r2_score(y, p)), spearman_pct=pct(spearmanr(y, p).statistic),
               mae_points=pct(np.mean(np.abs(y - p))), within_10_points_pct=pct(np.mean(np.abs(y - p) <= 0.10)),
               worst_window_r2_pct=pct(min(w["r2"] for w in per_window)), worst_window_spearman_pct=pct(min(w["spearman"] for w in per_window)),
               worst_window_mae_points=pct(max(w["mae"] for w in per_window)), mean_window_r2_pct=pct(np.mean([w["r2"] for w in per_window])),
               best_quarter_found_pct=pct(np.mean([w["best_quarter"] for w in per_window])), chance_best_quarter_pct=25.0)
    edges = [0, 0.1, 0.3, 0.6, 1.01]
    out["mae_points_by_true_view"] = {f"{int(100 * a)}-{int(100 * min(b, 1))}%": round(pct(np.mean(np.abs(y - p)[(y >= a) & (y < b)])), 1)
                                     for a, b in zip(edges[:-1], edges[1:]) if ((y >= a) & (y < b)).any()}
    slope, intercept = np.polyfit(p, y, 1)
    order = np.argsort(p)
    deciles = np.array_split(order, 10)
    gaps = [pct(abs(p[d].mean() - y[d].mean())) for d in deciles]
    out["calibration"] = dict(slope=float(slope), intercept=float(intercept), worst_decile_gap_points=max(gaps),
                              deciles=[dict(predicted_pct=round(pct(p[d].mean()), 1), true_pct=round(pct(y[d].mean()), 1)) for d in deciles])
    return out, built, X, y, p


def sanity(model, X, p):
    bounded = np.mean((p >= -0.05) & (p <= 1.05))
    first, second = model.predict(X[:500]), model.predict(X[:500])
    cols = [13, 14, 15]  # view_1km, view_2km, view_4km
    up = X[:2000].copy()
    up[:, cols] = np.minimum(up[:, cols] + 0.05, 1.0)
    monotone = np.mean(model.predict(up) >= model.predict(X[:2000]) - 1e-6)
    return dict(bounded_pct=pct(bounded), deterministic=bool(np.array_equal(first, second)), monotone_in_rings_pct=pct(monotone))


def shifted(model, test_windows, kind, per_window=150, windows=4, seed=0):
    """Truth from clean 20 m terrain; features from perturbed terrain (noise or coarser pixels) or other mast heights."""
    rng = np.random.default_rng(seed)
    truth, pred = [], []
    for (x, y) in test_windows[:windows]:
        w = build(x, y)
        grid = crop_xy(x, y)
        dem, px = grid.heights, grid.pixel_size_m
        idx = rng.choice(len(w["cells"]), per_window, replace=False)
        cells, label = w["cells"][idx], w["label"][idx]
        if kind[0] == "noise":
            noisy = dem + rng.normal(0, kind[1], dem.shape).astype(np.float32)
            F, _ = features_for(noisy, px, cells)
            truth.append(label)
        elif kind[0] == "correlated":
            from .lunar_windows import correlated_noise
            noisy = dem + correlated_noise(dem.shape, kind[1], 200.0, px, int(rng.integers(1_000_000)))
            F, _ = features_for(noisy, px, cells)
            truth.append(label)
        elif kind[0] == "resolution":
            s = int(kind[1] / px)
            coarse = dem[::s, ::s]
            F, _ = features_for(coarse, px * s, cells / s)
            truth.append(label)
        else:  # mast height: truth and features both use the other mast (mast height is a model input)
            _, truth_h = features_for(dem, px, cells, h_tx=kind[1])
            F, _ = features_for(dem, px, cells, h_tx=kind[1])
            truth.append(truth_h)
        pred.append(model.predict(F))
    t, p = np.concatenate(truth), np.concatenate(pred)
    return dict(cells=int(len(t)), r2_pct=pct(r2_score(t, p)), spearman_pct=pct(spearmanr(t, p).statistic), mean_bias_points=pct(np.mean(p - t)))


def selection(built, surrogate, gain_model, budgets=(4, 8, 12)):
    out = {}
    for budget in budgets:
        rows = [compare(w, surrogate, gain_model, budget=budget) for w in built]
        means = {k: pct(np.mean([r[k] for r in rows])) for k in rows[0]}
        ref = np.array([r["exact_greedy"] for r in rows])
        entry = dict(mean_coverage_pct=means)
        for name in ("surrogate_spaced", "shortlist_greedy", "learned_gain"):
            vals = np.array([r[name] for r in rows])
            rand = np.array([r["random"] for r in rows])
            entry[name] = dict(efficiency_vs_exact_pct=pct(np.mean(vals / ref)), worst_window_efficiency_pct=pct(np.min(vals / ref)),
                               win_rate_vs_random_pct=pct(np.mean(vals > rand)), mean_gain_over_random_points=pct(np.mean(vals - rand)))
        out[f"budget_{budget}"] = entry
    return out


def siting_sealed():
    from .evaluate import siting
    result = siting()
    return result


def shuffled_control(seed=0):
    """Train on tower labels shuffled within each tile and test on the sealed tiles: must score near chance."""
    from .data import EARTH_TILES
    from .evaluate import NEW_EARTH_TILES
    from .train import MIN_TOWERS_TILE, classifier, earth_dataset
    X, y, group, counts = earth_dataset(seed=seed)
    rng = np.random.default_rng(seed)
    keep = np.isin(group, [i for i, n in enumerate(EARTH_TILES) if counts[n]["towers"] >= MIN_TOWERS_TILE])
    shuffled = y.copy()
    shuffled[keep] = rng.permutation(y[keep])
    model = classifier().fit(X[keep], shuffled[keep])
    Xs, ys, _, _ = earth_dataset(NEW_EARTH_TILES, seed=123)
    return pct(roc_auc_score(ys, model.predict_proba(Xs)[:, 1]))


def transfer(prior, built, windows=16):
    """Earth-trained prior scores on lunar cells against their exact viewshed share."""
    truth, score = [], []
    for w in built[:windows]:
        base = np.hstack([w["cheap"], w["label"][:, None], w["horizon"]])  # FEATURES then HORIZON
        truth.append(w["label"])
        score.append(prior.predict_proba(base)[:, 1])
    t, s = np.concatenate(truth), np.concatenate(score)
    top = len(t) // 4
    return dict(spearman_pct=pct(spearmanr(t, s).statistic), best_quarter_found_pct=pct(len(set(np.argsort(-t)[:top]) & set(np.argsort(-s)[:top])) / top))


def run():
    surrogate = joblib.load(MODELS / "ora_coverage_surrogate.joblib")
    prior = joblib.load(MODELS / "ora_siting_prior.joblib")
    gain = joblib.load(MODELS / "ora_gain_model.joblib")
    _, test = split()
    report, checks = {}, {}

    print("A/B accuracy and calibration", flush=True)
    acc, built, X, y, p = accuracy(surrogate, test)
    report["A_accuracy"] = acc
    for name, v in (("A_r2_pct", acc["r2_pct"]), ("A_spearman_pct", acc["spearman_pct"]), ("A_mae_points", acc["mae_points"]),
                    ("A_worst_window_spearman_pct", acc["worst_window_spearman_pct"]), ("A_worst_window_mae_points", acc["worst_window_mae_points"]),
                    ("B_calibration_slope", acc["calibration"]["slope"]), ("B_worst_decile_gap_points", acc["calibration"]["worst_decile_gap_points"])):
        checks[name] = verdict(name, v)

    print("C sanity", flush=True)
    report["C_sanity"] = san = sanity(surrogate, X, p)
    checks["C_bounded_pct"] = verdict("C_bounded_pct", san["bounded_pct"])
    checks["C_monotone_in_rings_pct"] = verdict("C_monotone_in_rings_pct", san["monotone_in_rings_pct"])

    print("D noise", flush=True)
    base = shifted(surrogate, test, ("noise", 0.0))
    report["D_noise"] = {"0m": base}
    for sigma in (0.5, 2.0, 5.0):
        r = shifted(surrogate, test, ("noise", sigma))
        r["r2_drop_points"] = base["r2_pct"] - r["r2_pct"]
        report["D_noise"][f"{sigma}m"] = r
    checks["D_noise_0.5m_r2_drop_points"] = verdict("D_noise_0.5m_r2_drop_points", report["D_noise"]["0.5m"]["r2_drop_points"])
    checks["D_noise_2m_r2_drop_points"] = verdict("D_noise_2m_r2_drop_points", report["D_noise"]["2.0m"]["r2_drop_points"])

    report["D_correlated_noise"] = {"0m": base}
    for sigma in (2.0, 5.0):
        r = shifted(surrogate, test, ("correlated", sigma))
        r["r2_drop_points"] = base["r2_pct"] - r["r2_pct"]
        report["D_correlated_noise"][f"{sigma}m"] = r
    checks["D_correlated_2m_r2_drop_points"] = verdict("D_correlated_2m_r2_drop_points", report["D_correlated_noise"]["2.0m"]["r2_drop_points"])
    checks["D_correlated_5m_r2_drop_points"] = verdict("D_correlated_5m_r2_drop_points", report["D_correlated_noise"]["5.0m"]["r2_drop_points"])

    print("E resolution", flush=True)
    report["E_resolution"] = {f"{m}m": shifted(surrogate, test, ("resolution", m)) for m in (40, 60, 100)}
    checks["E_res_60m_r2_pct"] = verdict("E_res_60m_r2_pct", report["E_resolution"]["60m"]["r2_pct"])

    print("F mast height", flush=True)
    report["F_mast"] = {f"{h}m": shifted(surrogate, test, ("mast", h)) for h in (3.0, 10.0, 20.0)}
    checks["F_mast_3m_r2_pct"] = verdict("F_mast_3m_r2_pct", report["F_mast"]["3.0m"]["r2_pct"])
    checks["F_mast_20m_r2_pct"] = verdict("F_mast_20m_r2_pct", report["F_mast"]["20.0m"]["r2_pct"])

    print("G selection", flush=True)
    report["G_selection"] = sel = selection(built, surrogate, gain)
    b8 = sel["budget_8"]
    checks["G_learned_efficiency_pct_b8"] = verdict("G_learned_efficiency_pct_b8", b8["learned_gain"]["efficiency_vs_exact_pct"])
    checks["G_learned_win_rate_pct_b8"] = verdict("G_learned_win_rate_pct_b8", b8["learned_gain"]["win_rate_vs_random_pct"])
    checks["G_learned_worst_efficiency_pct_b8"] = verdict("G_learned_worst_efficiency_pct_b8", b8["learned_gain"]["worst_window_efficiency_pct"])
    checks["G_shortlist_efficiency_pct_b8"] = verdict("G_shortlist_efficiency_pct_b8", b8["shortlist_greedy"]["efficiency_vs_exact_pct"])

    print("H siting prior on sealed Earth tiles", flush=True)
    earth = siting_sealed()
    report["H_siting_prior"] = earth
    report["H_siting_prior"]["shuffled_label_auc_pct"] = shuffled = shuffled_control()
    checks["H_auc_pct"] = verdict("H_auc_pct", earth["pooled"]["auc_pct"])
    checks["H_top10_pct"] = verdict("H_top10_pct", earth["pooled"]["towers_in_top10_pct"])
    checks["H_shuffled_auc_pct"] = verdict("H_shuffled_auc_pct", shuffled)

    print("I transfer", flush=True)
    report["I_transfer"] = tr = transfer(prior, built)
    checks["I_spearman_pct"] = verdict("I_spearman_pct", tr["spearman_pct"])

    report["checks"] = checks
    report["summary"] = dict(passed=sum(c["passed"] for c in checks.values()), total=len(checks),
                             failed=[k for k, c in checks.items() if not c["passed"]])
    return report
