"""Train the ORA placement models. Run ``python -m ora.train`` after ``python -m ora.data --earth``
and ``python -m ora.lunar_windows``.

Three models, all evaluated with spatial cross-validation (whole regions held out, because neighbouring
cells are near-duplicates and random splits leak):

1. Siting prior: a classifier trained on where registered antenna towers stand in mountainous Earth
   terrain, from terrain-only features. It learns siting habits (ridges, prominence, wide views) and is
   applied to the Moon as a ranking prior. It proposes; the line-of-sight simulator decides.
2. Coverage surrogate: a regressor trained on many lunar windows that predicts a cell's exact viewshed
   share from terrain, skyline and multi-range view features, so the optimizer can screen candidates fast.
3. Marginal-gain model: predicts how much extra coverage a candidate adds given the relays already chosen.

The sealed lunar test windows (``ora.lunar_windows.split``) and the Earth tiles in ``ora.evaluate`` are
never read here. Models go under ``.moonmesh-data/models``; the model card is ``ora/model_card.json``.
"""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, r2_score, roc_auc_score
from sklearn.model_selection import GroupKFold

from moonmesh.data import DATA

from .data import EARTH_TILES, earth_tile, fcc_towers
from .features import FEATURES, PRIOR_FEATURES, SURROGATE_INPUTS, VIEW_RADIUS_M, interior_cells, prior_features, rasters
from .los import EARTH_R
from .lunar_windows import VARIANTS, build, matrix, split, variant
from .selector import fit_gain_model

MODELS = DATA / "models"
CARD = Path(__file__).with_name("model_card.json")
NEGATIVES_PER_TOWER = 8
MIN_TOWER_M = 10.0
MIN_TOWERS_TILE = 15  # a tile with fewer usable towers is too noisy to train on or to score


def earth_dataset(tiles=EARTH_TILES, seed=0):
    """Features, labels and tile index for tower cells and random background cells."""
    rng = np.random.default_rng(seed)
    towers = fcc_towers(tiles)
    X, y, group, counts = [], [], [], {}
    for index, name in enumerate(tiles):
        dem, lat_north, lon_west, row_deg, col_deg = earth_tile(name)
        pad = int(VIEW_RADIUS_M / 30.0)
        cells = set()
        for lat, lon, height in towers[name]:
            if height == height and height < MIN_TOWER_M:
                continue
            r, c = int((lat_north - lat) / row_deg), int((lon - lon_west) / col_deg)
            if pad <= r < dem.shape[0] - pad and pad <= c < dem.shape[1] - pad and np.isfinite(dem[r, c]):
                cells.add((c, r))
        positives = np.array(sorted(cells), dtype=float).reshape(-1, 2)
        negatives = interior_cells(dem, 30.0, max(len(positives), 20) * NEGATIVES_PER_TOWER, rng)
        layers = rasters(dem, 30.0)
        for cells_xy, label in ((positives, 1), (negatives, 0)):
            if len(cells_xy):
                X.append(prior_features(dem, 30.0, cells_xy, layers, R=EARTH_R))
                y.append(np.full(len(cells_xy), label))
                group.append(np.full(len(cells_xy), index))
        counts[name] = dict(towers=int(len(positives)), background=int(len(negatives)))
        print(f"{name}: {len(positives)} tower cells, {len(negatives)} background cells", flush=True)
    return np.vstack(X), np.concatenate(y), np.concatenate(group), counts


def lunar_dataset():
    """Features, exact viewshed share, spatial-block group and the built windows (training windows only)."""
    train_windows, _ = split()
    built = [build(*w) for w in train_windows]
    X = np.vstack([matrix(w) for w in built])
    y = np.concatenate([w["label"] for w in built])
    group = np.concatenate([np.full(len(w["label"]), int(x // 60_000) * 100 + int(yy // 60_000)) for w, (x, yy) in zip(built, train_windows)])
    return X, y, group, built


def augmented(train_windows, groups_of):
    """Extra training rows: the same windows with correlated height noise (2 m, 5 m) and masts of 3 m and 20 m."""
    X, y, g = [], [], []
    for kind, value in VARIANTS:
        for w, (x, yy) in zip(train_windows, groups_of):
            v = variant(x, yy, kind, value)
            X.append(matrix(v)); y.append(v["label"]); g.append(np.full(len(v["label"]), int(x // 60_000) * 100 + int(yy // 60_000)))
    return np.vstack(X), np.concatenate(y), np.concatenate(g)


def peak_weight(y):
    return 1 + 3 * np.maximum(y - 0.4, 0)


def classifier():
    return HistGradientBoostingClassifier(max_depth=4, learning_rate=0.06, max_iter=200, l2_regularization=1.0,
                                          class_weight="balanced", random_state=0)


def regressor():
    return HistGradientBoostingRegressor(max_depth=6, learning_rate=0.06, max_iter=300, random_state=0)


def top_recall(y, score, fraction=0.1):
    """Share of real towers that fall in the top ``fraction`` of cells by score."""
    cut = np.quantile(score, 1 - fraction)
    return float(((score >= cut) & (y == 1)).sum() / max((y == 1).sum(), 1))


def train(seed=0):
    MODELS.mkdir(parents=True, exist_ok=True)
    card = dict(trained=datetime.now(UTC).isoformat(timespec="seconds"), features=list(PRIOR_FEATURES), seed=seed)

    X, y, group, counts = earth_dataset(seed=seed)
    usable = [i for i, name in enumerate(EARTH_TILES) if counts[name]["towers"] >= MIN_TOWERS_TILE]
    fit_mask = np.isin(group, usable)
    folds, importance = [], []
    for held in usable:
        test = group == held
        train_mask = fit_mask & ~test
        model = classifier().fit(X[train_mask], y[train_mask])
        score = model.predict_proba(X[test])[:, 1]
        importance.append(permutation_importance(model, X[test], y[test], scoring="roc_auc", n_repeats=5, random_state=0).importances_mean)
        baseline = X[test][:, FEATURES.index("viewshed")]
        folds.append(dict(held_out=EARTH_TILES[held], towers=int(y[test].sum()), auc=float(roc_auc_score(y[test], score)),
                          average_precision=float(average_precision_score(y[test], score)),
                          recall_top10pct=top_recall(y[test], score), recall_top20pct=top_recall(y[test], score, 0.2),
                          viewshed_only_auc=float(roc_auc_score(y[test], baseline))))
        print(folds[-1], flush=True)
    joblib.dump(classifier().fit(X[fit_mask], y[fit_mask]), MODELS / "ora_siting_prior.joblib")
    card["siting_prior"] = dict(
        task="tower cell vs background cell from terrain and skyline features, Earth",
        labels="FCC Antenna Structure Registration (public US government record)",
        terrain="SRTM-derived 1 arc-second tiles (AWS open terrain tiles), resampled to 30 m",
        tiles=counts, tiles_used=[EARTH_TILES[i] for i in usable], min_towers_per_tile=MIN_TOWERS_TILE,
        cross_validation="leave one 1-degree tile out", folds=folds,
        mean_auc=float(np.mean([f["auc"] for f in folds])), mean_recall_top10pct=float(np.mean([f["recall_top10pct"] for f in folds])),
        chance_recall_top10pct=0.1,
        auc_lost_when_feature_shuffled={name: float(v) for name, v in zip(PRIOR_FEATURES, np.mean(importance, axis=0))},
        limits=["Earth towers follow roads, power and population as well as terrain; no road or population data is used to correct this.",
                "Earth to Moon is a domain shift; the score is a ranking prior, never a decision.",
                "Registry positions are owner-reported and can be tens of metres off."])

    Xl, yl, gl, windows = lunar_dataset()
    Xa, ya, ga = augmented(split()[0], split()[0])
    folds = []
    for fold, (tr, te) in enumerate(GroupKFold(n_splits=5).split(Xl, yl, gl)):
        keep = np.isin(ga, np.unique(gl[tr]))
        Xt, yt = np.vstack([Xl[tr], Xa[keep]]), np.concatenate([yl[tr], ya[keep]])
        predicted = regressor().fit(Xt, yt, sample_weight=peak_weight(yt)).predict(Xl[te])
        folds.append(dict(fold=fold, cells=int(len(te)), r2=float(r2_score(yl[te], predicted)),
                          spearman=float(spearmanr(yl[te], predicted).statistic), mean_abs_error=float(np.mean(np.abs(yl[te] - predicted)))))
        print(folds[-1], flush=True)
    Xf, yf = np.vstack([Xl, Xa]), np.concatenate([yl, ya])
    joblib.dump(regressor().fit(Xf, yf, sample_weight=peak_weight(yf)), MODELS / "ora_coverage_surrogate.joblib")
    card["coverage_surrogate"] = dict(
        task="exact viewshed share within 8 km (10 m mast, 2 m receiver) from terrain, skyline and multi-range view features, Moon",
        labels="computed by ora.features.view_rings on LOLA 20 m terrain", features=list(SURROGATE_INPUTS),
        windows=len(windows), cells=int(len(yl)), augmentation="correlated height noise 2 m and 5 m, masts 3 m and 20 m, peak-weighted loss (1 + 3 x max(share - 0.4, 0))", cross_validation="5 folds of 60 km spatial blocks, training windows only", folds=folds,
        mean_r2=float(np.mean([f["r2"] for f in folds])), mean_spearman=float(np.mean([f["spearman"] for f in folds])),
        limits=["Screens candidates only. The final set is always re-checked with exact line of sight."])

    gain, rows = fit_gain_model(windows)
    joblib.dump(gain, MODELS / "ora_gain_model.joblib")
    card["marginal_gain"] = dict(task="coverage a candidate adds given the relays already chosen", training_rows=int(rows), windows=len(windows),
                                 rollouts="3 per window, 8 steps, 70% greedy and 30% random picks",
                                 limits=["Needs no line-of-sight matrix at use time; the final set is still re-checked exactly."])
    CARD.write_text(json.dumps(card, indent=1) + "\n")
    return card


if __name__ == "__main__":
    summary = train(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    for name in ("siting_prior", "coverage_surrogate"):
        print(json.dumps({k: v for k, v in summary[name].items() if k.startswith("mean")}, indent=1))
