"""Terrain features for siting, defined in metres so they transfer between Earth and Moon grids.

Absolute elevation is left out on purpose: it means different things on the two bodies. Every
feature describes how a cell sits relative to its surroundings.
"""
import numpy as np
from scipy.ndimage import distance_transform_edt, maximum_filter, minimum_filter, uniform_filter

from .los import MOON_R, viewshed_fraction

CHEAP = ("slope", "tpi_300", "tpi_1000", "tpi_3000", "relief_500", "rel_elev_3000", "ridge_dist")
FEATURES = CHEAP + ("viewshed",)
HORIZON = ("horizon_mean", "horizon_max", "horizon_std", "horizon_open", "sector_min", "sector_max")
RINGS_M = (1_000, 2_000, 4_000)
RING_NAMES = tuple(f"view_{m // 1000}km" for m in RINGS_M)
SURROGATE_INPUTS = CHEAP + HORIZON + RING_NAMES + ("mast_m",)
PRIOR_FEATURES = FEATURES + HORIZON
VIEW_RADIUS_M = 8_000
TX_HEIGHT_M, RX_HEIGHT_M = 10.0, 2.0


def _window(metres, px_m):
    return max(3, int(round(2 * metres / px_m)) | 1)


def rasters(dem, px_m):
    """Cheap per-cell feature rasters keyed by name."""
    filled = np.where(np.isfinite(dem), dem, np.nanmean(dem)).astype(np.float32)
    dy, dx = np.gradient(filled, px_m, px_m)
    out = {"slope": np.degrees(np.arctan(np.hypot(dx, dy)))}
    for metres in (300, 1000, 3000):
        out[f"tpi_{metres}"] = filled - uniform_filter(filled, _window(metres, px_m), mode="nearest")
    out["relief_500"] = (maximum_filter(filled, _window(500, px_m), mode="nearest")
                         - minimum_filter(filled, _window(500, px_m), mode="nearest"))
    step = max(1, int(round(120 / px_m)))  # the 3 km percentile is smooth; compute it on a coarser grid
    coarse = filled[::step, ::step]
    size = _window(3000, px_m * step)
    low, high = minimum_filter(coarse, size, mode="nearest"), maximum_filter(coarse, size, mode="nearest")
    rel = (coarse - low) / np.maximum(high - low, 1.0)
    out["rel_elev_3000"] = np.repeat(np.repeat(rel, step, axis=0), step, axis=1)[:dem.shape[0], :dem.shape[1]]
    ridge = out["tpi_300"] >= np.quantile(out["tpi_300"][::7, ::7], 0.95)
    out["ridge_dist"] = distance_transform_edt(~ridge) * px_m
    return out


def cell_features(dem, px_m, cells, layers=None, R=MOON_R, with_viewshed=True):
    """Feature matrix for (N, 2) pixel cells (col, row), columns in FEATURES (or CHEAP) order."""
    layers = layers or rasters(dem, px_m)
    cells = np.asarray(cells, float)
    c, r = np.rint(cells[:, 0]).astype(int), np.rint(cells[:, 1]).astype(int)
    columns = [layers[name][r, c] for name in CHEAP]
    if with_viewshed:
        columns.append(viewshed_fraction(dem, px_m, cells, TX_HEIGHT_M, RX_HEIGHT_M, VIEW_RADIUS_M, R)[0])
    return np.column_stack(columns).astype(np.float32)


def interior_cells(dem, px_m, count, rng, margin_m=VIEW_RADIUS_M):
    """Seeded sample of valid cells far enough from the edge for a full viewshed disc."""
    pad = int(margin_m / px_m)
    rows, cols = dem.shape
    r = rng.integers(pad, rows - pad, size=count * 2)
    c = rng.integers(pad, cols - pad, size=count * 2)
    keep = np.isfinite(dem[r, c])
    return np.column_stack([c[keep], r[keep]])[:count].astype(float)


def horizon_features(dem, px_m, cells, radius_m=VIEW_RADIUS_M, azimuths=72, R=MOON_R, h_tx=TX_HEIGHT_M):
    """Skyline summaries per cell, columns in HORIZON order (degrees except ``horizon_open``).

    The terrain elevation angle is taken along ``azimuths`` rays; the mean, maximum and spread of
    that skyline, the share of azimuths with the skyline above the horizontal, and the lowest and
    highest of eight 45-degree sector maxima. Together they say how enclosed a cell is.
    """
    theta = np.arange(azimuths) * 2 * np.pi / azimuths
    radii = np.arange(2 * px_m, radius_m + 1e-9, 2 * px_m)
    rows, cols = dem.shape
    out = []
    for cx, cy in np.asarray(cells, float):
        x = np.rint(cx + np.sin(theta)[:, None] * radii / px_m).astype(int).clip(0, cols - 1)
        y = np.rint(cy - np.cos(theta)[:, None] * radii / px_m).astype(int).clip(0, rows - 1)
        angle = (dem[y, x] - radii ** 2 / (2 * R) - (dem[int(cy), int(cx)] + h_tx)) / radii
        skyline = np.degrees(np.arctan(np.nanmax(angle, axis=1)))
        sectors = skyline.reshape(8, -1).max(axis=1)
        out.append([skyline.mean(), skyline.max(), skyline.std(), np.mean(skyline > 0), sectors.min(), sectors.max()])
    return np.array(out, np.float32).reshape(-1, len(HORIZON))


def view_rings(dem, px_m, cells, radii_m=RINGS_M + (VIEW_RADIUS_M,), h_tx=TX_HEIGHT_M, h_rx=RX_HEIGHT_M, R=MOON_R, azimuths=120,
               step_px=2):
    """Share of the disc visible out to each radius in ``radii_m``; columns follow ``radii_m``.

    The last radius is the same sweep as ``viewshed_fraction`` (the training label); the shorter ones come
    from the same sweep, so they cost nothing extra. ``step_px`` is the radial sample spacing in pixels:
    2 (the training label) steps over ridges narrower than two pixels and reads a few percent high; 1 matches
    dense line of sight closely (see docs/ACCURACY.md).
    """
    step_m = step_px * px_m
    radii = np.arange(step_m, max(radii_m) + 1e-9, step_m)
    theta = np.arange(azimuths) * 2 * np.pi / azimuths
    rows, cols = dem.shape
    weights = radii  # area of a polar cell is proportional to its radius (constant step and azimuth)
    out = np.zeros((len(cells), len(radii_m)), np.float32)
    for i, (cx, cy) in enumerate(np.asarray(cells, float)):
        x = np.rint(cx + np.sin(theta)[:, None] * radii / px_m).astype(int)
        y = np.rint(cy - np.cos(theta)[:, None] * radii / px_m).astype(int)
        inside = (x >= 0) & (x < cols) & (y >= 0) & (y < rows)
        ground = np.where(inside, dem[y.clip(0, rows - 1), x.clip(0, cols - 1)], np.nan)
        z_tx = dem[int(round(cy)), int(round(cx))] + h_tx
        drop = radii ** 2 / (2 * R)
        before = np.concatenate([np.full((azimuths, 1), -np.inf),
                                 np.maximum.accumulate(np.nan_to_num((ground - drop - z_tx) / radii, nan=-np.inf), axis=1)[:, :-1]], axis=1)
        visible = np.isfinite(ground) & ((ground + h_rx - drop - z_tx) / radii >= before)
        per_radius = (visible * weights).sum(axis=0)
        total = np.cumsum(per_radius)
        denominator = np.cumsum(weights) * azimuths
        for j, limit in enumerate(radii_m):
            k = int(np.searchsorted(radii, limit + 1e-9)) - 1
            out[i, j] = total[k] / denominator[k]
    return out


def prior_features(dem, px_m, cells, layers=None, R=MOON_R):
    """Columns in PRIOR_FEATURES order: the terrain and viewshed features plus the skyline summaries."""
    base = cell_features(dem, px_m, cells, layers, R=R)
    return np.hstack([base, horizon_features(dem, px_m, cells, R=R)]).astype(np.float32)


def surrogate_features(dem, px_m, cells, layers=None, h_tx=TX_HEIGHT_M, R=MOON_R):
    """(X, share): the surrogate's input matrix in SURROGATE_INPUTS order, and the exact viewshed share
    at 8 km for a mast of ``h_tx`` metres (the training label when the mast is that height)."""
    layers = layers or rasters(dem, px_m)
    cells = np.asarray(cells, float)
    c, r = np.rint(cells[:, 0]).astype(int), np.rint(cells[:, 1]).astype(int)
    cheap = np.column_stack([layers[name][r, c] for name in CHEAP]).astype(np.float32)
    rings = view_rings(dem, px_m, cells, RINGS_M + (VIEW_RADIUS_M,), h_tx=h_tx, R=R)
    X = np.hstack([cheap, horizon_features(dem, px_m, cells, R=R, h_tx=h_tx), rings[:, :-1], np.full((len(cells), 1), h_tx, np.float32)])
    return X.astype(np.float32), rings[:, -1]
