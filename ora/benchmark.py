"""Does the siting score earn its place? Compare site-selection strategies on a held-out lunar site.

``python -m ora benchmark [--site haworth] [--budget 8] [--seeds 20]``

For each held-out site the coverage surrogate is retrained without it. Candidate cells and
target cells are sampled from the site's interior. Coverage is the exact line-of-sight share
of target cells (10 m mast, 2 m receiver, within RANGE_M) seen by at least one chosen site.

Strategies: random; surrogate score (spaced); Earth siting-prior score (spaced); exact
single-site viewshed rank (spaced); exact greedy union (needs the full line-of-sight matrix,
the reference the cheap scores are trying to approximate).
"""
import json

import joblib
import numpy as np

from .data import ALL_SITES, terrain
from .features import CHEAP, RX_HEIGHT_M, TX_HEIGHT_M, cell_features, horizon_features, interior_cells, rasters
from .los import los_clear_many
from .train import MODELS, lunar_dataset, regressor

RANGE_M = 8_000
SPACING_M = 1_000


def coverage_matrix(dem, px, candidates, targets):
    """Boolean (candidates x targets): exact line of sight within RANGE_M."""
    n_c, n_t = len(candidates), len(targets)
    a = np.repeat(candidates, n_t, axis=0)
    b = np.tile(targets, (n_c, 1))
    near = np.hypot(*(a - b).T) * px <= RANGE_M
    clear = np.zeros(len(a), bool)
    if near.any():
        clear[near] = los_clear_many(dem, px, a[near], b[near], TX_HEIGHT_M, RX_HEIGHT_M, step_px=2.0)[0]
    return clear.reshape(n_c, n_t)


def spaced_top(score, cells, px, budget):
    """Highest-scoring cells, skipping any closer than SPACING_M to one already chosen."""
    chosen = []
    for i in np.argsort(-score):
        if all(np.hypot(*(cells[i] - cells[j])) * px >= SPACING_M for j in chosen):
            chosen.append(i)
        if len(chosen) == budget:
            break
    return chosen


def greedy_union(matrix, budget):
    chosen, covered = [], np.zeros(matrix.shape[1], bool)
    for _ in range(budget):
        gain = (matrix & ~covered).sum(axis=1)
        gain[chosen] = -1
        best = int(np.argmax(gain))
        chosen.append(best)
        covered |= matrix[best]
    return chosen


def share(matrix, chosen):
    return float(matrix[chosen].any(axis=0).mean()) if len(chosen) else 0.0


def run(site, budget=8, seeds=20, candidates=200, targets=1500, seed=0, held=None, grid=None):
    rng = np.random.default_rng(seed)
    grid = grid or terrain(site)
    dem, px = grid.heights, grid.pixel_size_m
    cand = interior_cells(dem, px, candidates, rng)
    targ = interior_cells(dem, px, targets, rng)
    matrix = coverage_matrix(dem, px, cand, targ)
    layers = rasters(dem, px)
    full = cell_features(dem, px, cand, layers)  # cheap features + exact viewshed fraction
    surrogate = held or regressor()
    prior = joblib.load(MODELS / "ora_siting_prior.joblib")
    results = {
        "random": [share(matrix, list(rng.choice(len(cand), budget, replace=False))) for _ in range(seeds)],
        "surrogate": [share(matrix, spaced_top(surrogate.predict(np.hstack([full[:, :len(CHEAP)], horizon_features(dem, px, cand)])), cand, px, budget))],
        "siting_prior": [share(matrix, spaced_top(prior.predict_proba(full)[:, 1], cand, px, budget))],
        "exact_viewshed_rank": [share(matrix, spaced_top(full[:, -1], cand, px, budget))],
        "exact_greedy": [share(matrix, greedy_union(matrix, budget))],
    }
    out = {}
    for name, values in results.items():
        v = np.asarray(values)
        half = 1.96 * v.std(ddof=1) / np.sqrt(len(v)) if len(v) > 1 else None
        out[name] = dict(coverage=float(v.mean()), ci95=None if half is None else float(half), runs=len(v))
    return dict(site=site, budget=budget, candidates=candidates, targets=targets, range_m=RANGE_M, strategies=out)


def benchmark(sites=None, budget=8, seeds=20):
    """Leave-one-site-out: the surrogate used for each site never saw it."""
    X, y, group, names = lunar_dataset()
    rows = []
    for held, name in enumerate(names):
        if sites and name not in sites:
            continue
        test = group == held
        model = regressor().fit(X[~test], y[~test])
        rows.append(run(name, budget, seeds, held=model))
        print(name, {k: round(v["coverage"], 3) for k, v in rows[-1]["strategies"].items()}, flush=True)
    return rows
