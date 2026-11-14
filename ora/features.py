"""Terrain features for siting, defined in metres so they transfer between Earth and Moon grids.

Absolute elevation is left out on purpose: it means different things on the two bodies. Every
feature describes how a cell sits relative to its surroundings.
"""
import numpy as np
from scipy.ndimage import distance_transform_edt, maximum_filter, minimum_filter, uniform_filter

from .los import MOON_R, viewshed_fraction

CHEAP = ("slope", "tpi_300", "tpi_1000", "tpi_3000", "relief_500", "rel_elev_3000", "ridge_dist")
FEATURES = CHEAP + ("viewshed",)
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
