"""Accuracy against independent references, with a protocol that keeps the final numbers honest.

    python -m ora accuracy            prepare truth (cached), train the fast view estimator on DEV, score HOLD and LEGACY once

References:
- Dense line-of-sight truth for coverage: for a cell, the share of 6,000 uniformly random ground points within 8 km
  that a 2 m receiver can see from a 10 m mast, each by direct line of sight sampled every pixel (20 m). This is
  independent of the radial sweeps the app uses, which sample along rays.
- Exact greedy selection on each window's cached line-of-sight matrix, for relay selection.
- The FCC tower registry on sealed Earth tiles, for the siting prior (ora.evaluate).
- JPL Horizons ephemerides, for Sun and Earth geometry (moonmesh.validation).

Splits (lunar windows from ora.lunar_windows):
- LEGACY: the original sealed test windows (inspected before this work, so reported as a legacy benchmark).
- HOLD: eight new windows (two 2 x 2 blocks) carved out of the training windows with seed 2027, plus their ring of
  neighbours dropped as a buffer, before any of this tuning. Never used to train or tune anything here.
- DEV: the remaining training windows. All method choices and the fast estimator's training use DEV only.
"""
import json
import time

import numpy as np

from moonmesh.data import DATA

from .features import CHEAP, RX_HEIGHT_M, TX_HEIGHT_M, VIEW_RADIUS_M, view_rings
from .los import MOON_R, los_clear_many
from .lunar_windows import SPACING_M, build, crop_xy, key, matrix, split

CACHE = DATA / "ora_cache" / "truth"
TRUTH_CELLS, TRUTH_RAYS = 150, 6000
HOLDOUT_SEED = 2027
FAST_RINGS_M = (1_000, 2_000, 4_000, VIEW_RADIUS_M)
FAST_AZIMUTHS = 24
FAST_INPUTS = tuple(f"sweep{FAST_AZIMUTHS}_{m // 1000}km" for m in FAST_RINGS_M) + CHEAP
SWEEPS = ((12, 1), (16, 1), (24, 1), (36, 1), (24, 2), (120, 1))  # (azimuths, radial step in pixels)


def splits():
    """dict(dev, hold, legacy): lists of window centres. Deterministic; cached so it is computed once."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "splits.json"
    if path.is_file():
        return {k: [tuple(w) for w in v] if isinstance(v, list) else v for k, v in json.loads(path.read_text()).items()}
    train, legacy = split()
    have, s = set(train), SPACING_M
    rng = np.random.default_rng(HOLDOUT_SEED)
    hold, banned = [], set()
    for i in rng.permutation(len(train)):
        x, y = train[i]
        block = [(x, y), (x + s, y), (x, y + s), (x + s, y + s)]
        ring = {(x + dx * s, y + dy * s) for dx in range(-1, 3) for dy in range(-1, 3)}
        if all(b in have for b in block) and not (ring & banned):
            hold += block
            banned |= ring
        if len(hold) >= 8:
            break
    out = dict(dev=[w for w in train if w not in banned], hold=hold, legacy=legacy, buffer_dropped=len(banned) - len(hold))
    path.write_text(json.dumps({k: [list(w) for w in v] if isinstance(v, list) else v for k, v in out.items()}))
    return out


def dense_truth(dem, px, cells, rays=TRUTH_RAYS, seed=0, h_tx=TX_HEIGHT_M, h_rx=RX_HEIGHT_M, radius=VIEW_RADIUS_M):
    """Visible share of the disc, by direct line of sight (1 px steps) to ``rays`` uniformly random ground points."""
    rng = np.random.default_rng(seed)
    out = np.zeros(len(cells))
    rows, cols = dem.shape
    for i, (cx, cy) in enumerate(np.asarray(cells, float)):
        r = radius * np.sqrt(rng.uniform((2 * px / radius) ** 2, 1, rays))
        t = rng.uniform(0, 2 * np.pi, rays)
        tx, ty = np.rint(cx + np.sin(t) * r / px), np.rint(cy - np.cos(t) * r / px)
        ok = (tx >= 0) & (tx < cols) & (ty >= 0) & (ty < rows)
        b = np.column_stack([tx[ok], ty[ok]])
        a = np.repeat([[cx, cy]], len(b), axis=0)
        clear, _ = los_clear_many(dem, px, a, b, h_tx, h_rx, R=MOON_R, step_px=1.0)
        valid = np.isfinite(dem[b[:, 1].astype(int), b[:, 0].astype(int)])
        out[i] = clear[valid].mean()
    return out


def window_truth(w):
    """Cached per window: sweep estimates for every sampled cell, and dense truth for a fixed subset of cells."""
    x, y = w
    path = CACHE / f"{key(x, y)}.npz"
    if path.is_file():
        with np.load(path) as data:
            return {k: data[k] for k in data.files}
    base, grid = build(x, y), crop_xy(x, y)
    dem, px, cells = grid.heights, grid.pixel_size_m, base["cells"]
    out = {f"sweep{az}_s{step}": view_rings(dem, px, cells, FAST_RINGS_M, azimuths=az, step_px=step) for az, step in SWEEPS}
    idx = np.random.default_rng(int(abs(x) + abs(y))).choice(len(cells), TRUTH_CELLS, replace=False)
    out.update(truth_idx=idx, truth=dense_truth(dem, px, cells[idx], seed=int(abs(x) * 7 + abs(y))))
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **out)
    return out


def _job(w):
    window_truth(w)
    return w


def prepare(workers=7):
    import multiprocessing as mp
    sp = splits()
    todo = [w for name in ("hold", "legacy", "dev") for w in sp[name] if not (CACHE / f"{key(*w)}.npz").is_file()]
    if todo:
        print(f"preparing dense truth for {len(todo)} windows", flush=True)
        with mp.Pool(max(1, min(workers, mp.cpu_count() - 1))) as pool:
            for i, _ in enumerate(pool.imap_unordered(_job, todo), 1):
                print(f"{i}/{len(todo)}", flush=True)
    return sp


def window_maps(w):
    """Cached per window: the 200 m view map of every selection candidate (see ora.selector.view_maps)."""
    from .lunar_windows import RANGE_M
    from .selector import view_maps
    path = CACHE / f"{key(*w)}__maps.npz"
    if path.is_file():
        with np.load(path) as data:
            return np.unpackbits(data["maps"], axis=1).astype(bool)[:, :int(data["bins"])]
    base, grid = build(*w), crop_xy(*w)
    maps = view_maps(grid.heights, grid.pixel_size_m, base["cells"][base["cand_idx"]], region_pad_px=int(RANGE_M / grid.pixel_size_m))
    np.savez_compressed(path, maps=np.packbits(maps, axis=1), bins=maps.shape[1])
    return maps


def selection_example(w):
    """(view, cells, maps, los) for one window: the selector's inputs and the exact matrix it is scored against."""
    base, extra = build(*w), window_truth(w)
    c = base["cand_idx"]
    return extra[f"sweep{FAST_AZIMUTHS}_s1"][c], base["cells"][c], window_maps(w), base["los"]


def train_selector(sp=None):
    """Learned selector that needs no line-of-sight matrix, trained on DEV windows only."""
    import joblib
    from .selector import fit_map_gain_model
    sp = sp or splits()
    model, rows = fit_map_gain_model(selection_example(w) for w in sp["dev"])
    path = DATA / "models" / "ora_gain_selector.joblib"
    joblib.dump(model, path)
    return model, dict(training_rows=int(rows), windows=len(sp["dev"]), path=str(path))


def group_of(w):
    return int(w[0] // 60_000) * 100 + int(w[1] // 60_000)


def table(windows):
    """Every truth cell of the given windows: truth, current label, old surrogate inputs, sweeps, fast inputs."""
    parts = dict(truth=[], label=[], X=[], fast=[], group=[])
    sweeps = {}
    for w in windows:
        base, extra = build(*w), window_truth(w)
        idx = extra["truth_idx"]
        X = matrix(base)[idx]
        parts["truth"].append(extra["truth"])
        parts["label"].append(base["label"][idx])
        parts["X"].append(X)
        parts["fast"].append(np.hstack([extra[f"sweep{FAST_AZIMUTHS}_s1"][idx], X[:, :len(CHEAP)]]))
        parts["group"].append(np.full(len(idx), group_of(w)))
        for k, v in extra.items():
            if k.startswith("sweep"):
                sweeps.setdefault(k, []).append(v[idx][:, -1])
    out = {k: (np.vstack(v) if k in ("X", "fast") else np.concatenate(v)) for k, v in parts.items()}
    out.update({k: np.concatenate(v) for k, v in sweeps.items()})
    return out


def metrics(t, p):
    from scipy.stats import spearmanr
    from sklearn.metrics import r2_score
    t, p = np.asarray(t, float), np.asarray(p, float)
    return dict(r2_pct=100 * r2_score(t, p), spearman_pct=100 * spearmanr(t, p).statistic, mae_points=100 * np.mean(np.abs(t - p)),
                bias_points=100 * np.mean(p - t), within_5_points_pct=100 * np.mean(np.abs(t - p) <= 0.05),
                within_10_points_pct=100 * np.mean(np.abs(t - p) <= 0.10), cells=int(len(t)))


def fast_model():
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_depth=5, learning_rate=0.05, max_iter=400, l2_regularization=1.0, random_state=0)


def fast_features(dem, px, cells, layers=None):
    """Inputs of the fast view estimator for arbitrary cells, in FAST_INPUTS order."""
    from .features import rasters
    layers = layers or rasters(dem, px)
    cells = np.asarray(cells, float)
    c, r = np.rint(cells[:, 0]).astype(int), np.rint(cells[:, 1]).astype(int)
    sweep = view_rings(dem, px, cells, FAST_RINGS_M, azimuths=FAST_AZIMUTHS, step_px=1)
    return np.hstack([sweep, np.column_stack([layers[name][r, c] for name in CHEAP])]).astype(np.float32)


def train_fast(sp=None):
    """Fast view estimator: a 24-ray sweep corrected by a gradient-boosted model, trained on DEV dense truth only."""
    import joblib
    from sklearn.model_selection import GroupKFold
    sp = sp or splits()
    t = table(sp["dev"])
    X, y = t["fast"], t["truth"]
    pred = np.zeros_like(y)
    for tr, te in GroupKFold(n_splits=5).split(X, y, t["group"]):
        pred[te] = fast_model().fit(X[tr], y[tr]).predict(X[te])
    model = fast_model().fit(X, y)
    path = DATA / "models" / "ora_view_estimator.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)
    return model, dict(dev_cross_validation=metrics(y, pred), inputs=list(FAST_INPUTS), training_cells=int(len(y)), path=str(path))


def coverage(sp, fast):
    from .status import loaded
    old = loaded("ora_coverage_surrogate")
    out = {}
    for name in ("hold", "legacy"):
        t = table(sp[name])
        rows = {"label used for training until now (120 rays, 40 m steps)": metrics(t["truth"], t["label"]),
                "computed sweep used by the app (120 rays, 20 m steps)": metrics(t["truth"], t["sweep120_s1"]),
                "fast view estimator (24-ray sweep + learned correction)": metrics(t["truth"], fast.predict(t["fast"])),
                "raw 24-ray sweep (no correction)": metrics(t["truth"], t["sweep24_s1"])}
        if old is not None:
            rows["old coverage surrogate" + (" (its training included these windows)" if name == "hold" else "")] = \
                metrics(t["truth"], old.predict(t["X"]))
        out[name] = rows
    return out


def selection(sp, selector=None, budgets=(4, 8, 12)):
    """Relay selection against exact greedy on each window's line-of-sight matrix."""
    from .selector import SHORTLIST, exact_greedy, learned_gain, map_gain_select, share
    from .status import loaded
    old, gain = loaded("ora_coverage_surrogate"), loaded("ora_gain_model")
    out = {}
    for name in ("hold", "legacy"):
        rows = {}
        for budget in budgets:
            got = dict(new_learned_selector=[], old_surrogate_shortlist=[], old_learned_gain=[], view_shortlist_60=[])
            for w in sp[name]:
                b, e = build(*w), window_truth(w)
                los, c = b["los"], b["cand_idx"]
                ref = share(los, exact_greedy(los, budget))
                view = e["sweep120_s1"][c][:, -1]
                got["view_shortlist_60"].append(share(los, exact_greedy(los, budget, list(np.argsort(-view)[:60]))) / ref)
                if selector is not None:
                    got["new_learned_selector"].append(share(los, map_gain_select(*selection_example(w)[:3], selector, budget)) / ref)
                if old is not None:
                    score = old.predict(matrix(b)[c])
                    got["old_surrogate_shortlist"].append(share(los, exact_greedy(los, budget, list(np.argsort(-score)[:SHORTLIST]))) / ref)
                if gain is not None:
                    got["old_learned_gain"].append(share(los, learned_gain(b, gain, budget)) / ref)
            rows[f"budget_{budget}"] = {k: dict(mean_pct_of_exact=100 * float(np.mean(v)), worst_pct_of_exact=100 * float(np.min(v)))
                                        for k, v in got.items() if v}
        out[name] = rows
    return out


def ranking(sp):
    """How well each siting score ranks candidates by true coverage (Spearman with dense truth), per split."""
    from scipy.stats import spearmanr
    from .status import loaded
    old, prior = loaded("ora_coverage_surrogate"), loaded("ora_siting_prior")
    rank = lambda v: np.argsort(np.argsort(v)) / max(len(v) - 1, 1)
    out = {}
    for name in ("hold", "legacy"):
        t = table(sp[name])
        rows = {"app now: computed viewshed share": 100 * spearmanr(t["truth"], t["sweep120_s1"]).statistic}
        if old is not None and prior is not None:
            prior_x = np.hstack([t["X"][:, :len(CHEAP)], t["label"][:, None], t["X"][:, len(CHEAP):len(CHEAP) + 6]])
            blend = 0.8 * rank(old.predict(t["X"])) + 0.2 * rank(prior.predict_proba(prior_x)[:, 1])
            rows["app before: surrogate 80% + Earth prior 20%"] = 100 * spearmanr(t["truth"], blend).statistic
            rows["Earth prior alone"] = 100 * spearmanr(t["truth"], prior.predict_proba(prior_x)[:, 1]).statistic
        out[name] = rows
    return out


def run():
    start = time.time()
    sp = prepare()
    fast, fast_info = train_fast(sp)
    selector, selector_info = train_selector(sp)
    report = dict(protocol=dict(dev_windows=len(sp["dev"]), hold_windows=len(sp["hold"]), legacy_windows=len(sp["legacy"]),
                                buffer_windows_dropped=sp["buffer_dropped"], truth_cells_per_window=TRUTH_CELLS, truth_rays=TRUTH_RAYS,
                                note="Methods chosen and models trained on DEV only; HOLD and LEGACY scored once."),
                  fast_view_estimator=fast_info, learned_selector=selector_info, coverage=coverage(sp, fast), ranking=ranking(sp),
                  selection=selection(sp, selector))
    from .evaluate import siting
    report["earth_siting_prior"] = siting()["pooled"]
    record_in_card(fast_info, selector_info, report)
    report["seconds"] = round(time.time() - start)
    return report


def record_in_card(fast_info, selector_info, report):
    """Add the two models trained here to ora/model_card.json, bound to their files by SHA-256."""
    from pathlib import Path
    from .status import CARD, sha256
    card = json.loads(CARD.read_text()) if CARD.is_file() else {}
    hold = report["coverage"]["hold"]["fast view estimator (24-ray sweep + learned correction)"]
    card["view_estimator"] = dict(task="8 km viewshed share from a 24-ray sweep and cheap terrain features, Moon",
                                  labels="dense line of sight to 6,000 random ground points per cell (ora.accuracy)",
                                  inputs=fast_info["inputs"], training_cells=fast_info["training_cells"],
                                  dev_cross_validation=fast_info["dev_cross_validation"], holdout=hold,
                                  limits=["Trained on south-polar LOLA 20 m terrain, 10 m mast, 2 m receiver."])
    card["gain_selector"] = dict(task="coverage a candidate adds given chosen sites, from 200 m view maps; no line-of-sight matrix",
                                 training_rows=selector_info["training_rows"], windows=selector_info["windows"],
                                 holdout=report["selection"]["hold"],
                                 limits=["Window-scale study (120 candidates, 500 targets). The app selects by exact greedy."])
    artifacts = card.setdefault("artifacts", {})
    for name in ("ora_view_estimator", "ora_gain_selector"):
        path = Path(DATA / "models" / f"{name}.joblib")
        artifacts[name] = dict(sha256=sha256(path), bytes=path.stat().st_size)
    CARD.write_text(json.dumps(card, indent=1) + "\n")
