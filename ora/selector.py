"""Choosing a handful of relay sites for coverage: compare strategies, including a learned marginal-gain model.

All strategies pick ``budget`` sites from a window's candidate cells; coverage is the exact line-of-sight
share of the window's target cells (from the cached matrix) seen by at least one chosen site.

- random: uniformly random candidates.
- surrogate_spaced: highest predicted view share, skipping candidates closer than SPACING_M to one chosen.
- shortlist_greedy: surrogate shortlist of ``SHORTLIST`` candidates, then exact greedy coverage inside it.
  Needs the line-of-sight matrix only for the shortlist rows.
- learned_gain: pick one site at a time by a model that predicts the coverage a candidate would ADD given the
  sites already chosen (features: the candidate's terrain and view features, how many sites are already
  near it, distance to the nearest). It never sees the line-of-sight matrix.
- exact_greedy: the reference; it needs the full matrix.
"""
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from .lunar_windows import matrix
from .features import RINGS_M

SPACING_M = 1_000
SHORTLIST = 40
PX = 20.0
EXTENDED = False
ROLLOUTS = 3


def share(los, chosen):
    return float(los[chosen].any(axis=0).mean()) if len(chosen) else 0.0


def exact_greedy(los, budget, pool=None):
    pool = list(range(len(los))) if pool is None else list(pool)
    chosen, covered = [], np.zeros(los.shape[1], bool)
    for _ in range(budget):
        gains = [(los[c] & ~covered).sum() if c not in chosen else -1 for c in pool]
        best = pool[int(np.argmax(gains))]
        chosen.append(best)
        covered |= los[best]
    return chosen


def spaced_top(score, cells, budget):
    chosen = []
    for i in np.argsort(-score):
        if all(np.hypot(*(cells[i] - cells[j])) * PX >= SPACING_M for j in chosen):
            chosen.append(int(i))
        if len(chosen) == budget:
            break
    return chosen


def gain_features(base, cells, chosen, step, extended=EXTENDED):
    """Per-candidate features for the marginal-gain model, given the sites chosen so far.

    Always: step, distance to the nearest chosen site, chosen sites within 2, 4 and 8 km. Extended adds
    chosen sites within 1 and 12 km, the mean distance to all chosen sites, and the candidate's view
    share relative to the best candidate not yet near a chosen site.
    """
    radii = (2_000, 4_000, 8_000) + ((1_000, 12_000) if extended else ())
    if chosen:
        d = np.hypot(cells[:, None, 0] - cells[chosen][None, :, 0], cells[:, None, 1] - cells[chosen][None, :, 1]) * PX
        nearest = d.min(axis=1)
        counts = [(d < r).sum(axis=1) for r in radii]
        extra = [d.mean(axis=1)] if extended else []
    else:
        nearest = np.full(len(cells), 20_000.0)
        counts = [np.zeros(len(cells))] * len(radii)
        extra = [np.full(len(cells), 20_000.0)] if extended else []
    if extended:
        view = base[:, 15]  # view_4km: the widest ring is the best single-number proxy for new area
        free = nearest > 4_000
        extra.append(view / max(float(view[free].max()) if free.any() else 1.0, 1e-6))
    return np.column_stack([base, np.full(len(cells), step), nearest, *counts, *extra]).astype(np.float32)


def training_rows(window, budget=8, rollouts=None, seed=0, extended=None):
    """Marginal-gain examples from mixed random and greedy rollouts on one window's exact matrix."""
    rollouts = ROLLOUTS if rollouts is None else rollouts
    extended = EXTENDED if extended is None else extended
    rng = np.random.default_rng(seed)
    los = window["los"]
    base = matrix(window)[window["cand_idx"]]
    cells = window["cells"][window["cand_idx"]]
    X, y = [], []
    for _ in range(rollouts):
        chosen, covered = [], np.zeros(los.shape[1], bool)
        for step in range(budget):
            gain = (los & ~covered).sum(axis=1) / los.shape[1]
            free = np.array([c not in chosen for c in range(len(los))])
            X.append(gain_features(base, cells, chosen, step, extended)[free])
            y.append(gain[free])
            pick = int(rng.choice(np.flatnonzero(free))) if rng.random() < 0.3 else int(np.argmax(np.where(free, gain, -1)))
            chosen.append(pick)
            covered |= los[pick]
    return np.vstack(X), np.concatenate(y)


def fit_gain_model(windows, budget=8, rollouts=None, extended=None):
    parts = [training_rows(w, budget, rollouts=rollouts, seed=i, extended=extended) for i, w in enumerate(windows)]
    X, y = np.vstack([p[0] for p in parts]), np.concatenate([p[1] for p in parts])
    return HistGradientBoostingRegressor(max_depth=6, learning_rate=0.06, max_iter=300, random_state=0).fit(X, y), len(y)


def learned_gain(window, model, budget=8, extended=None):
    extended = EXTENDED if extended is None else extended
    base = matrix(window)[window["cand_idx"]]
    cells = window["cells"][window["cand_idx"]]
    chosen = []
    for step in range(budget):
        predicted = model.predict(gain_features(base, cells, chosen, step, extended))
        predicted[chosen] = -np.inf
        chosen.append(int(np.argmax(predicted)))
    return chosen


def compare(window, surrogate, gain_model, budget=8, seeds=20, seed=0):
    los = window["los"]
    base = matrix(window)[window["cand_idx"]]
    cells = window["cells"][window["cand_idx"]]
    score = surrogate.predict(base)
    rng = np.random.default_rng(seed)
    shortlist = list(np.argsort(-score)[:SHORTLIST])
    return dict(
        random=float(np.mean([share(los, list(rng.choice(len(los), budget, replace=False))) for _ in range(seeds)])),
        surrogate_spaced=share(los, spaced_top(score, cells, budget)),
        shortlist_greedy=share(los, exact_greedy(los, budget, shortlist)),
        learned_gain=share(los, learned_gain(window, gain_model, budget)),
        exact_greedy=share(los, exact_greedy(los, budget)))


def select_sites(dem, px, cells, budget, surrogate=None, seed=0, targets=500, range_m=8_000, h_tx=10.0, h_rx=2.0, random_draws=5,
                 shortlist=None):
    """Coverage-first site choice for a real scenario: exact greedy coverage over the candidates.

    ``cells`` are candidate pixel coordinates. Coverage is the share of sampled target cells seen by at least
    one chosen site, by exact line of sight. Every candidate's line-of-sight row is computed: on a 220-site
    scenario that is no slower than the old surrogate shortlist (the surrogate's features cost as much as the
    rows) and it recovered only about 55% of the exact coverage (docs/ACCURACY.md). ``shortlist`` (a number
    of candidates ranked by their own view share) remains for very large candidate sets; ``surrogate`` is
    accepted for compatibility and ignored. Returns indices into ``cells``.
    """
    from .features import interior_cells, view_rings
    from .los import los_clear_many
    rng = np.random.default_rng(seed)
    cells = np.asarray(cells, float)
    budget = min(budget, len(cells))
    pool_ids = list(range(len(cells)))
    if shortlist and shortlist < len(cells):
        view = view_rings(dem, px, cells, (range_m,), h_tx=h_tx, h_rx=h_rx, step_px=1)[:, 0]
        pool_ids = [int(i) for i in np.argsort(-view)[:max(shortlist, 3 * budget)]]
    lo, hi = cells.min(axis=0) - range_m / px, cells.max(axis=0) + range_m / px
    pool = interior_cells(dem, px, targets * 4, rng, margin_m=0)
    pool = pool[(pool >= lo).all(axis=1) & (pool <= hi).all(axis=1)]
    tgt = pool[:targets]
    cache = {}

    def row(i):
        if i not in cache:
            a = np.repeat(cells[i][None, :], len(tgt), axis=0)
            near = np.hypot(*(a - tgt).T) * px <= range_m
            clear = np.zeros(len(tgt), bool)
            if near.any():
                clear[near] = los_clear_many(dem, px, a[near], tgt[near], h_tx, h_rx, step_px=1.0)[0]
            cache[i] = clear
        return cache[i]

    chosen, covered = [], np.zeros(len(tgt), bool)
    for _ in range(budget):
        gains = {i: (row(i) & ~covered).sum() for i in pool_ids if i not in chosen}
        best = max(gains, key=gains.get)
        chosen.append(best)
        covered |= row(best)
    randoms = [float(np.any([row(int(i)) for i in rng.choice(len(cells), budget, replace=False)], axis=0).mean()) for _ in range(random_draws)]
    return dict(chosen=chosen, coverage=float(covered.mean()), random_coverage=float(np.mean(randoms)), targets=int(len(tgt)),
                los_rows=len(cache), candidates=int(len(cells)), shortlist=len(pool_ids),
                method="exact greedy over all candidates" if len(pool_ids) == len(cells) else f"exact greedy over the {len(pool_ids)} widest-view candidates")


# --------------------------------------------------------------------------- map-based learned selector (no LOS matrix)

MAP_AZIMUTHS, MAP_BIN_PX = 120, 10  # 120-ray sweep at 1 px steps, binned into 200 m squares on 20 m terrain


def view_maps(dem, px, cells, radius_m=8_000.0, h_tx=10.0, h_rx=2.0, region_pad_px=None, azimuths=MAP_AZIMUTHS, bin_px=MAP_BIN_PX):
    """(cells x bins) boolean matrix: the 200 m bins each candidate sees, from one radial sweep per candidate.

    ``region_pad_px`` keeps only bins at least that far from the grid edge (the region the targets are drawn from).
    """
    from .los import MOON_R
    rows, cols = dem.shape
    n_cols = cols // bin_px + 1
    radii = np.arange(px, radius_m + 1e-9, px)
    theta = np.arange(azimuths) * 2 * np.pi / azimuths
    drop = radii ** 2 / (2 * MOON_R)
    out = np.zeros((len(cells), (rows // bin_px + 1) * n_cols), bool)
    for i, (cx, cy) in enumerate(np.asarray(cells, float)):
        x = np.rint(cx + np.sin(theta)[:, None] * radii / px).astype(int)
        y = np.rint(cy - np.cos(theta)[:, None] * radii / px).astype(int)
        inside = (x >= 0) & (x < cols) & (y >= 0) & (y < rows)
        ground = np.where(inside, dem[y.clip(0, rows - 1), x.clip(0, cols - 1)], np.nan)
        z = dem[int(round(cy)), int(round(cx))] + h_tx
        before = np.concatenate([np.full((azimuths, 1), -np.inf),
                                 np.maximum.accumulate(np.nan_to_num((ground - drop - z) / radii, nan=-np.inf), axis=1)[:, :-1]], axis=1)
        seen = np.isfinite(ground) & ((ground + h_rx - drop - z) / radii >= before)
        if region_pad_px:
            p = region_pad_px
            seen &= (x >= p) & (x < cols - p) & (y >= p) & (y < rows - p)
        out[i, (y[seen] // bin_px) * n_cols + x[seen] // bin_px] = True
    return out


def map_gain_features(view, cells, maps, chosen, covered, step):
    """Candidate view (4 rings), map size, bins it would add, that share of its map, step, and spacing to chosen sites."""
    size = maps.sum(axis=1).astype(float)
    new = (maps & ~covered).sum(axis=1).astype(float)
    if chosen:
        d = np.hypot(cells[:, None, 0] - cells[chosen][None, :, 0], cells[:, None, 1] - cells[chosen][None, :, 1]) * PX
        nearest, counts = d.min(axis=1), [(d < r).sum(axis=1) for r in (2_000, 4_000, 8_000)]
    else:
        nearest, counts = np.full(len(cells), 20_000.0), [np.zeros(len(cells))] * 3
    return np.column_stack([view, size, new, new / np.maximum(size, 1), np.full(len(cells), step), nearest, *counts]).astype(np.float32)


def map_gain_rows(view, cells, maps, los, budget=8, rollouts=3, seed=0):
    """Training rows: true marginal coverage gains from the exact matrix, along mixed greedy and random rollouts."""
    rng = np.random.default_rng(seed)
    X, y = [], []
    for _ in range(rollouts):
        chosen, covered_t, covered_m = [], np.zeros(los.shape[1], bool), np.zeros(maps.shape[1], bool)
        for step in range(budget):
            gain = (los & ~covered_t).sum(axis=1) / los.shape[1]
            free = np.array([i not in chosen for i in range(len(los))])
            X.append(map_gain_features(view, cells, maps, chosen, covered_m, step)[free])
            y.append(gain[free])
            pick = int(rng.choice(np.flatnonzero(free))) if rng.random() < 0.3 else int(np.argmax(np.where(free, gain, -1)))
            chosen.append(pick)
            covered_t |= los[pick]
            covered_m |= maps[pick]
    return np.vstack(X), np.concatenate(y)


def fit_map_gain_model(examples):
    """``examples``: iterable of (view, cells, maps, los) per training window."""
    parts = [map_gain_rows(*e, seed=i) for i, e in enumerate(examples)]
    X, y = np.vstack([p[0] for p in parts]), np.concatenate([p[1] for p in parts])
    return HistGradientBoostingRegressor(max_depth=6, learning_rate=0.06, max_iter=300, random_state=0).fit(X, y), len(y)


def map_gain_select(view, cells, maps, model, budget):
    chosen, covered = [], np.zeros(maps.shape[1], bool)
    for step in range(budget):
        predicted = model.predict(map_gain_features(view, cells, maps, chosen, covered, step))
        predicted[chosen] = -np.inf
        best = int(np.argmax(predicted))
        chosen.append(best)
        covered |= maps[best]
    return chosen
