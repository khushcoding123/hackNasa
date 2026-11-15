"""Test the saved ORA models on data that was never used to train or tune them.

``python -m ora evaluate``

- Siting prior: Earth tiles that are not in ``EARTH_TILES``; labels are the FCC registry, with fresh random
  background cells (different seed). Compared with the view-only baseline and with chance.
- Coverage surrogate: lunar south-pole windows that are not in ``ALL_SITES`` (sealed 20 km windows from the
  LOLA 20 m product; see ``ora.lunar_windows``). Labels are the exact viewshed share.

The models are loaded as saved. Nothing is refitted here, so a number cannot be improved by looking at it.
"""
import json
from pathlib import Path

import joblib
import numpy as np
from scipy.stats import spearmanr
from sklearn.metrics import r2_score, roc_auc_score


from .data import EARTH, EARTH_TILES, TILE_URL, download_earth
from .features import FEATURES
from .train import MODELS, earth_dataset, top_recall

NEW_EARTH_TILES = ("N46W121", "N43W110", "N40W106", "N34W117", "N36W118", "N41W112", "N44W115", "N37W112")
def pct(value):
    return None if value is None else round(100 * float(value), 1)


def siting(seed=123):
    assert not set(NEW_EARTH_TILES) & set(EARTH_TILES)
    download_earth(NEW_EARTH_TILES)
    X, y, group, counts = earth_dataset(NEW_EARTH_TILES, seed=seed)
    model = joblib.load(MODELS / "ora_siting_prior.joblib")
    score = model.predict_proba(X)[:, 1]
    view = X[:, FEATURES.index("viewshed")]
    rows = []
    for index, name in enumerate(NEW_EARTH_TILES):
        t = group == index
        if y[t].sum() < 5:
            rows.append(dict(tile=name, towers=int(y[t].sum()), note="too few usable towers to score"))
            continue
        rows.append(dict(tile=name, towers=int(y[t].sum()), auc_pct=pct(roc_auc_score(y[t], score[t])),
                         view_only_auc_pct=pct(roc_auc_score(y[t], view[t])),
                         towers_in_top10_pct=pct(top_recall(y[t], score[t], 0.1)),
                         towers_in_top20_pct=pct(top_recall(y[t], score[t], 0.2))))
    scored = [r for r in rows if "auc_pct" in r]
    pooled = dict(towers=int(y.sum()), background=int((y == 0).sum()), auc_pct=pct(roc_auc_score(y, score)),
                  view_only_auc_pct=pct(roc_auc_score(y, view)), towers_in_top10_pct=pct(top_recall(y, score, 0.1)),
                  towers_in_top20_pct=pct(top_recall(y, score, 0.2)), chance_top10_pct=10.0, chance_top20_pct=20.0,
                  tiles_beating_view_only=f"{sum(r['auc_pct'] > r['view_only_auc_pct'] for r in scored)} of {len(scored)}",
                  mean_tile_auc_pct=round(float(np.mean([r['auc_pct'] for r in scored])), 1))
    return dict(tiles=rows, pooled=pooled)


def surrogate():
    """Coverage surrogate on the sealed lunar test windows (see ora.lunar_windows.split)."""
    import joblib

    from .lunar_windows import split
    from .stress import accuracy
    model = joblib.load(MODELS / "ora_coverage_surrogate.joblib")
    result = accuracy(model, split()[1])[0]
    result.pop("calibration", None)
    return result


def evaluate():
    return dict(siting_prior=siting(), coverage_surrogate=surrogate(),
                note="Models loaded as saved; none of these tiles or sites were used for training or tuning.")
