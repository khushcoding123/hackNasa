"""Thermal hazard model: terrain features, the saved model class, and per-point inference.

RESEARCH / EXPERIMENTAL. Everything here is a data-driven estimate of how cold the surface around a relay is,
learned from NASA LRO Diviner's south polar resource product (PRP, LRO-L-DLRE-5-PRP-V2.0), with inputs derived
only from the LOLA 240 m south polar DEM. It does not replace the physics in ``moonmesh.science``.

Why this file exists in the app (and not only under ``training/``): the same feature code must run when the
model is trained and when the app scores a relay, otherwise the model would see different inputs at inference.
It needs only numpy and scipy, which the app already requires.

What the target is (and is not): the PRP is a thermal-model fit to the first year of Diviner polar observations,
reported per ~500 m mesh facet: annual average temperature at 2 cm depth, annual maximum surface temperature
and the depth at which water ice is stable. It is not a raw measurement and not a night-time minimum. The model
therefore answers "how cold is a ~500 m facet of terrain like this, over a year", not "what is the electronics
temperature of this relay tonight".
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import map_coordinates, maximum_filter, minimum_filter, uniform_filter

from lunar_core.terrain import MOON_RADIUS_M, polar_stereographic_south

PIXEL_M = 240.0                 # LDEM_75S_240M pixel size (moonmesh.data.PRODUCTS)
DIMENSION = 3812                # LDEM_75S_240M is 3812 x 3812, int16, 0.5 m per count
NODATA = (-32768, -32767)       # sentinels used by moonmesh.data.crop
# The PRP mesh covers a 600 km square centred on the pole. Training and scoring are restricted to inside the
# 80 degree circle (PRP label: "covers a square region centered on the pole to 80 degrees latitude").
DOMAIN_MAX_LAT = -80.0
DOMAIN_RADIUS_M = 2 * MOON_RADIUS_M * np.tan(np.radians(10) / 2)   # polar stereographic radius of 80 S
HORIZON_RADIUS_CELLS = 125      # 30 km
HORIZON_AZIMUTHS = 16
HORIZON_STEPS = (1, 2, 3, 4, 6, 8, 11, 16, 22, 32, 44, 64, 90, 125)
OPEN_THRESHOLDS_DEG = (1.0, 3.0, 5.0, 7.0)
FEATURES = (
    "r_pole_km", "elevation_m", "slope_deg", "slope_std_deg", "tpi_1km_m", "tpi_3km_m", "tpi_10km_m", "relief_5km_m",
    "horizon_mean_deg", "horizon_max_deg", "horizon_min_deg", "horizon_std_deg",
    "slope_radial_deg", "slope_tangential_deg", "horizon_equatorward_deg", "horizon_poleward_deg",
    *[f"open_below_{int(t)}deg" for t in OPEN_THRESHOLDS_DEG],
)
# Which inputs a model may use. Chosen on validation blocks (never test); ablations guard against a model that only
# memorizes where it is (absolute elevation and distance from the pole together identify a place).
FEATURE_SETS = {
    "all": FEATURES,
    "no_elevation": tuple(f for f in FEATURES if f != "elevation_m"),
    "local_only": tuple(f for f in FEATURES if f not in ("elevation_m", "r_pole_km")),
}
TARGETS = ("temp_avg_k", "temp_max_k")
MARGIN_CELLS = HORIZON_RADIUS_CELLS + 42   # context needed around a point so every feature is exact


def load_heights(path):
    """LOLA LDEM_75S_240M as float32 metres, NaN where there is no data."""
    raw = np.memmap(path, dtype="<i2", mode="r", shape=(DIMENSION, DIMENSION))
    return np.where(np.isin(raw, NODATA), np.nan, raw * 0.5).astype(np.float32)


def grid_axes():
    edge = DIMENSION * PIXEL_M / 2
    return -edge, edge            # x_left, y_top of the grid (cell centres are 0.5 pixel inside)


def xy_to_rc(x, y):
    """Fractional (row, col) of cell centres for polar stereographic metres."""
    x_left, y_top = grid_axes()
    return (y_top - np.asarray(y)) / PIXEL_M - 0.5, (np.asarray(x) - x_left) / PIXEL_M - 0.5


def _padded(heights, pad):
    return np.pad(heights, pad, mode="constant", constant_values=np.nan)


def horizon_stack(heights):
    """Horizon elevation angle (deg) for HORIZON_AZIMUTHS directions, shape (azimuths, rows, cols).

    The angle to terrain at distance d includes the curvature drop d^2 / 2R, which is 260 m at 30 km.
    Directions without data return NaN for that cell and direction.
    """
    pad = HORIZON_RADIUS_CELLS
    padded = _padded(heights, pad)
    rows, cols = heights.shape
    out = np.full((HORIZON_AZIMUTHS, rows, cols), np.nan, dtype=np.float32)
    for index in range(HORIZON_AZIMUTHS):
        azimuth = 2 * np.pi * index / HORIZON_AZIMUTHS
        best = np.full((rows, cols), -np.inf, dtype=np.float32)
        for step in HORIZON_STEPS:
            dr, dc = int(round(-step * np.cos(azimuth))), int(round(step * np.sin(azimuth)))
            distance = PIXEL_M * np.hypot(dr, dc)
            view = padded[pad + dr: pad + dr + rows, pad + dc: pad + dc + cols]
            rise = view - heights - distance ** 2 / (2 * MOON_RADIUS_M)
            best = np.fmax(best, np.degrees(np.arctan2(rise, distance)).astype(np.float32))
        best[~np.isfinite(best)] = np.nan
        out[index] = best
    return out


def _axes(rows, cols, x0, y0):
    """(y, x) polar stereographic metres of every cell centre, as broadcastable-to-grid float32 arrays."""
    xs = (x0 + np.arange(cols) * PIXEL_M).astype(np.float32)
    ys = (y0 - np.arange(rows) * PIXEL_M).astype(np.float32)
    return np.broadcast_to(ys[:, None], (rows, cols)), np.broadcast_to(xs[None, :], (rows, cols))


def _box_mean(values, cells):
    """Mean over a (2*cells+1) square, ignoring NaN, with NaN returned where nothing is valid."""
    size = 2 * cells + 1
    finite = np.isfinite(values)
    total = uniform_filter(np.where(finite, values, 0.0), size=size, mode="constant") * size * size
    count = uniform_filter(finite.astype(np.float32), size=size, mode="constant") * size * size
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0.5, total / np.maximum(count, 1), np.nan)


def feature_maps(heights, x0=None, y0=None):
    """Every model input as a raster aligned with ``heights`` (dict name -> float32 array).

    ``x0``/``y0`` are polar stereographic metres of the array's top-left cell centre. They are used only for the
    distance from the pole, which is the one coordinate-derived input (no longitude, so a place cannot be memorized).
    """
    heights = np.asarray(heights, dtype=np.float32)
    rows, cols = heights.shape
    if x0 is None:
        x0, y0 = grid_axes()[0] + PIXEL_M / 2, grid_axes()[1] - PIXEL_M / 2
    dy, dx = np.gradient(heights, PIXEL_M, PIXEL_M)
    slope = np.degrees(np.arctan(np.hypot(dx, dy))).astype(np.float32)
    mean_slope = _box_mean(slope, 2)
    slope_std = np.sqrt(np.maximum(_box_mean(slope ** 2, 2) - mean_slope ** 2, 0)).astype(np.float32)
    filled = np.where(np.isfinite(heights), heights, np.nanmean(heights))   # only for min/max filters; masked below
    horizon = horizon_stack(heights)
    valid = np.isfinite(horizon).sum(axis=0) >= HORIZON_AZIMUTHS // 2
    with np.errstate(invalid="ignore"):
        hmean, hmax, hmin, hstd = (np.nanmean(horizon, 0), np.nanmax(horizon, 0), np.nanmin(horizon, 0), np.nanstd(horizon, 0))
        openness = {f"open_below_{int(t)}deg": np.nanmean(np.where(np.isfinite(horizon), horizon < t, np.nan), 0)
                    for t in OPEN_THRESHOLDS_DEG}
    # Direction-aware inputs. Over a lunar day the Sun circles the whole horizon at a few degrees of elevation, and at local
    # noon it stands toward the equator, i.e. radially outward from the pole. A face tilted that way is lit far more than a
    # pole-facing one, so slope is split into its radial and tangential parts and the horizon is read along that direction.
    ys_g, xs_g = _axes(rows, cols, x0, y0)
    radius = np.maximum(np.hypot(xs_g, ys_g), 1.0)
    toward_up = -dy                                    # np.gradient's first axis points down the page; +y is up
    radial = (dx * xs_g + toward_up * ys_g) / radius   # terrain rise per metre when moving away from the pole
    tangential = (-dx * ys_g + toward_up * xs_g) / radius
    outward = np.arctan2(xs_g, ys_g) % (2 * np.pi)     # compass azimuth of "away from the pole", same convention as the horizon rays
    position = outward / (2 * np.pi) * HORIZON_AZIMUTHS
    first = np.floor(position).astype(int) % HORIZON_AZIMUTHS
    weight = (position - np.floor(position)).astype(np.float32)
    def along(shift):
        a = np.take_along_axis(horizon, ((first + shift) % HORIZON_AZIMUTHS)[None], axis=0)[0]
        b = np.take_along_axis(horizon, ((first + 1 + shift) % HORIZON_AZIMUTHS)[None], axis=0)[0]
        return (1 - weight) * a + weight * b
    maps = dict(slope_radial_deg=np.degrees(np.arctan(radial)), slope_tangential_deg=np.degrees(np.arctan(tangential)),
                horizon_equatorward_deg=along(0), horizon_poleward_deg=along(HORIZON_AZIMUTHS // 2))
    maps.update(elevation_m=heights, slope_deg=slope, slope_std_deg=slope_std,
                tpi_1km_m=heights - _box_mean(heights, 4), tpi_3km_m=heights - _box_mean(heights, 12),
                tpi_10km_m=heights - _box_mean(heights, 42),
                relief_5km_m=maximum_filter(filled, size=21) - minimum_filter(filled, size=21),
                horizon_mean_deg=hmean, horizon_max_deg=hmax, horizon_min_deg=hmin, horizon_std_deg=hstd, **openness)
    maps["r_pole_km"] = (radius / 1000).astype(np.float32)
    finite = np.isfinite(heights) & valid
    return {name: np.where(finite, np.asarray(maps[name], dtype=np.float32), np.nan).astype(np.float32) for name in FEATURES}


def sample_maps(maps, x, y, x_left=None, y_top=None):
    """Bilinear sample of each feature raster at polar stereographic metres. Returns (n, len(FEATURES))."""
    if x_left is None:
        x_left, y_top = grid_axes()
    cols = (np.asarray(x, float) - x_left) / PIXEL_M - 0.5
    rows = (y_top - np.asarray(y, float)) / PIXEL_M - 0.5
    columns = []
    for name in FEATURES:
        columns.append(map_coordinates(maps[name], [rows, cols], order=1, mode="constant", cval=np.nan))
    return np.stack(columns, axis=1)


def point_features(heights, lat, lon):
    """Features for scattered (lat, lon) points, computed from a window around each point (not the whole cap).

    ``heights`` is the full 240 m grid (``load_heights``). Points are grouped so that nearby relays share one window.
    Returns an array (n, len(FEATURES)) with NaN for points the grid cannot describe.
    """
    lat, lon = np.atleast_1d(np.asarray(lat, float)), np.atleast_1d(np.asarray(lon, float))
    x, y = polar_stereographic_south(lat, lon)
    out = np.full((len(lat), len(FEATURES)), np.nan)
    row, col = xy_to_rc(x, y)
    ok = np.isfinite(row) & (row > 1) & (col > 1) & (row < DIMENSION - 2) & (col < DIMENSION - 2)
    if not ok.any():
        return out
    r0 = max(0, int(np.floor(row[ok].min())) - MARGIN_CELLS)
    r1 = min(DIMENSION, int(np.ceil(row[ok].max())) + MARGIN_CELLS + 1)
    c0 = max(0, int(np.floor(col[ok].min())) - MARGIN_CELLS)
    c1 = min(DIMENSION, int(np.ceil(col[ok].max())) + MARGIN_CELLS + 1)
    x_left, y_top = grid_axes()
    window = heights[r0:r1, c0:c1]
    x0, y0 = x_left + (c0 + 0.5) * PIXEL_M, y_top - (r0 + 0.5) * PIXEL_M
    maps = feature_maps(window, x0, y0)
    out[ok] = sample_maps(maps, x[ok], y[ok], x_left + c0 * PIXEL_M, y_top - r0 * PIXEL_M)
    return out


class ThermalHazardModel:
    """Gradient-boosted regressors and a classifier, saved as one artifact (loaded by ``ora.status``).

    ``predict`` returns the point predictions (n, 2): [annual mean K, annual maximum K]; ``detail`` returns
    everything the dashboard shows. Intervals are quantile models fitted on the training blocks and checked for
    coverage on held-out blocks (see ``ora/model_card.json``); they are not guarantees.
    """

    def __init__(self, features, regressors, intervals, cold_trap, reference, feature_set="all", interval_scale=None):
        self.features_ = tuple(features)             # every column the caller passes (``FEATURES`` order)
        self.feature_set_ = feature_set              # which of them the estimators were fitted on
        self.columns_ = [self.features_.index(f) for f in FEATURE_SETS[feature_set]]
        self.interval_scale_ = interval_scale or {t: 1.0 for t in TARGETS}
        self.regressors_ = regressors        # {"temp_avg_k": model, "temp_max_k": model}
        self.intervals_ = intervals          # {"temp_avg_k": (lo_model, hi_model), ...}
        self.cold_trap_ = cold_trap          # classifier for ice stable at the surface (PRP ice_depth == 0)
        self.reference_ = reference          # percentile tables of the training targets, for context only
        self.n_features_in_ = len(self.features_)

    def _used(self, X):
        return np.asarray(X, float)[:, self.columns_]

    def predict(self, X):
        X = self._used(X)
        return np.stack([self.regressors_[t].predict(X) for t in TARGETS], axis=1)

    def detail(self, X):
        X = self._used(X)
        out = {}
        for target in TARGETS:
            lo, hi = self.intervals_[target]
            point = self.regressors_[target].predict(X)
            scale = self.interval_scale_[target]      # widening fitted on validation blocks so the range is not over-confident
            out[target] = dict(value=point, low=point - scale * np.maximum(point - lo.predict(X), 0),
                               high=point + scale * np.maximum(hi.predict(X) - point, 0))
        out["cold_trap_probability"] = self.cold_trap_.predict_proba(X)[:, 1]
        return out


# ------------------------------------------------------------------------------------------------ scoring

_HEIGHTS = {}


def _terrain():
    """The 240 m south polar DEM from the app cache (loaded once; ~58 MB as float32)."""
    if "grid" not in _HEIGHTS:
        from moonmesh.data import DATA
        path = DATA / "terrain" / "LDEM_75S_240M.IMG"
        if not path.is_file():
            raise FileNotFoundError("LOLA terrain is missing. Run python -m moonmesh.data from the repository.")
        _HEIGHTS["grid"] = load_heights(path)
    return _HEIGHTS["grid"]


def _percentile(reference, value):
    """Share (0-100) of training facets whose target was below ``value``; reference holds the 0, 5, ... 100th percentiles."""
    return float(np.interp(value, reference, np.linspace(0, 100, len(reference))))


def _spearman(a, b):
    from scipy.stats import spearmanr
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 4 or np.ptp(a[ok]) == 0 or np.ptp(b[ok]) == 0:
        return None
    return float(spearmanr(a[ok], b[ok]).statistic)


def score_points(points, model, status=None):
    """Thermal hazard estimate for relay points. ``points``: dicts with id, lat, lon and optional ``sunlight_pct``.

    Points outside the 80-90 S training cap, or whose terrain window is incomplete, are returned with
    ``in_domain: False`` and a reason, never a number.
    """
    lat = np.array([p["lat"] for p in points], float)
    lon = np.array([p["lon"] for p in points], float)
    features = point_features(_terrain(), lat, lon)
    inside = (lat <= DOMAIN_MAX_LAT) & np.isfinite(features).all(axis=1)
    detail = model.detail(features[inside]) if inside.any() else None
    rows, k = [], 0
    for index, point in enumerate(points):
        row = dict(id=point.get("id"), lat=float(lat[index]), lon=float(lon[index]), in_domain=bool(inside[index]))
        if not inside[index]:
            row["reason"] = ("outside the 80-90 S training cap" if lat[index] > DOMAIN_MAX_LAT else "terrain window incomplete at this point")
            rows.append(row)
            continue
        row["inputs"] = {name: float(features[index, i]) for i, name in enumerate(FEATURES)}
        for target in TARGETS:
            d = detail[target]
            row[target] = dict(value=float(d["value"][k]), low=float(d["low"][k]), high=float(d["high"][k]),
                               percentile=_percentile(model.reference_[target], float(d["value"][k])))
        row["cold_trap_probability"] = float(detail["cold_trap_probability"][k])
        rows.append(row)
        k += 1
    scored = [(p.get("sunlight_pct"), r["temp_max_k"]["value"]) for p, r in zip(points, rows) if r["in_domain"] and p.get("sunlight_pct") is not None]
    agreement = _spearman([s for s, _ in scored], [t for _, t in scored]) if scored else None
    return dict(points=rows, agreement_with_physics=dict(
        spearman_sunlight_vs_predicted_max_temperature=agreement, n=len(scored),
        note="Rank correlation across these points between the mission's computed sunlight share (a mission window, at the relay's own position) and the model's "
             "predicted annual maximum temperature (a ~500 m facet, all year). They measure different things at different scales, so this is shown for transparency "
             "and a weak or negative value is not an error by itself; it is not a validation of either result."))
