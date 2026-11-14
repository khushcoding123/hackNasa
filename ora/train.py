"""Train the ORA placement models. Run ``python -m ora.train`` after ``python -m ora.data --earth``.

Two models, both evaluated with spatial cross-validation (whole regions held out, because
neighbouring cells are near-duplicates and random splits leak):

1. Siting prior: a classifier trained on where registered antenna towers stand in mountainous
   Earth terrain, from terrain-only features. It learns terrain siting habits (ridges,
   prominence, wide views) and is applied to the Moon as a ranking prior. It proposes;
   the line-of-sight simulator decides.
2. Coverage surrogate: a regressor trained on lunar terrain that predicts a cell's exact
   viewshed fraction from cheap features, so the optimizer can screen many candidates fast.

Models are saved under ``.moonmesh-data/models``; the model card with the measured metrics
is written to ``ora/model_card.json``.
"""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import average_precision_score, r2_score, roc_auc_score

from moonmesh.data import DATA

from .data import ALL_SITES, EARTH_TILES, earth_tile, fcc_towers, terrain
from .features import CHEAP, FEATURES, VIEW_RADIUS_M, cell_features, interior_cells, rasters
from .los import EARTH_R

MODELS = DATA / "models"
CARD = Path(__file__).with_name("model_card.json")
NEGATIVES_PER_TOWER = 8
MIN_TOWER_M = 10.0
LUNAR_SAMPLES = 1500


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
                X.append(cell_features(dem, 30.0, cells_xy, layers, R=EARTH_R))
                y.append(np.full(len(cells_xy), label))
                group.append(np.full(len(cells_xy), index))
        counts[name] = dict(towers=int(len(positives)), background=int(len(negatives)))
        print(f"{name}: {len(positives)} tower cells, {len(negatives)} background cells", flush=True)
    return np.vstack(X), np.concatenate(y), np.concatenate(group), counts


def lunar_dataset(sites=None, seed=0):
    """Cheap features and exact viewshed fraction for sampled cells of each prepared lunar site."""
    rng = np.random.default_rng(seed)
    X, y, group, names = [], [], [], []
    for key in sites or ALL_SITES:
        try:
            grid = terrain(key)
        except FileNotFoundError:
            continue
        cells = interior_cells(grid.heights, grid.pixel_size_m, LUNAR_SAMPLES, rng)
        full = cell_features(grid.heights, grid.pixel_size_m, cells)
        X.append(full[:, :len(CHEAP)])
        y.append(full[:, -1])
        group.append(np.full(len(cells), len(names)))
        names.append(key)
        print(f"{key}: {len(cells)} cells, mean viewshed {full[:, -1].mean():.3f}", flush=True)
    return np.vstack(X), np.concatenate(y), np.concatenate(group), names


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
    card = dict(trained=datetime.now(UTC).isoformat(timespec="seconds"), features=list(FEATURES), seed=seed)

    X, y, group, counts = earth_dataset(seed=seed)
    folds = []
    for held in range(len(EARTH_TILES)):
        test = group == held
        if y[test].sum() < 3 or y[~test].sum() < 3:
            continue
        score = classifier().fit(X[~test], y[~test]).predict_proba(X[test])[:, 1]
        baseline = X[test][:, FEATURES.index("viewshed")]
        folds.append(dict(held_out=EARTH_TILES[held], towers=int(y[test].sum()),
                          auc=float(roc_auc_score(y[test], score)),
                          average_precision=float(average_precision_score(y[test], score)),
                          recall_top10pct=top_recall(y[test], score),
                          viewshed_only_auc=float(roc_auc_score(y[test], baseline))))
        print(folds[-1], flush=True)
    prior = classifier().fit(X, y)
    joblib.dump(prior, MODELS / "ora_siting_prior.joblib")
    card["siting_prior"] = dict(
        task="tower cell vs background cell from terrain features, Earth",
        labels="FCC Antenna Structure Registration (public US government record)",
        terrain="SRTM-derived 1 arc-second tiles (AWS open terrain tiles), resampled to 30 m",
        tiles=counts, cross_validation="leave one 1-degree tile out", folds=folds,
        mean_auc=float(np.mean([f["auc"] for f in folds])),
        mean_recall_top10pct=float(np.mean([f["recall_top10pct"] for f in folds])),
        chance_recall_top10pct=0.1,
        limits=["Earth towers follow roads, power and population as well as terrain.",
                "Earth to Moon is a domain shift; the score is a ranking prior, never a decision.",
                "Registry positions are owner-reported and can be tens of metres off."])

    Xl, yl, gl, names = lunar_dataset(seed=seed)
    folds = []
    for held, name in enumerate(names):
        test = gl == held
        predicted = regressor().fit(Xl[~test], yl[~test]).predict(Xl[test])
        folds.append(dict(held_out=name, r2=float(r2_score(yl[test], predicted)),
                          spearman=float(spearmanr(yl[test], predicted).statistic),
                          mean_abs_error=float(np.mean(np.abs(yl[test] - predicted)))))
        print(folds[-1], flush=True)
    joblib.dump(regressor().fit(Xl, yl), MODELS / "ora_coverage_surrogate.joblib")
    card["coverage_surrogate"] = dict(
        task="exact viewshed fraction within 8 km (10 m mast, 2 m receiver) from cheap terrain features, Moon",
        labels="computed by ora.los.viewshed_fraction on LOLA 20 m terrain", sites=names,
        samples_per_site=LUNAR_SAMPLES, cross_validation="leave one site out", folds=folds,
        mean_r2=float(np.mean([f["r2"] for f in folds])), mean_spearman=float(np.mean([f["spearman"] for f in folds])),
        limits=["Screens candidates only. The final set is always re-checked with exact line of sight."])
    CARD.write_text(json.dumps(card, indent=1) + "\n")
    return card


if __name__ == "__main__":
    summary = train(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(json.dumps({k: v for k, v in summary["siting_prior"].items() if k.startswith("mean")}, indent=1))
    print(json.dumps({k: v for k, v in summary["coverage_surrogate"].items() if k.startswith("mean")}, indent=1))
