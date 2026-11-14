"""Curvature-corrected line of sight on a gridded elevation model.

Coordinates are pixel (col, row). The body bulges above the straight chord between two
points by d1*d2/(2R), so terrain is raised by that amount before it is compared with the
antenna-to-antenna line. Missing terrain (NaN) along a ray makes the result unknown, which
is reported as not clear with a NaN clearance.
"""
import numpy as np

MOON_R = 1_737_400.0  # m
EARTH_R = 6_371_000.0  # m


def _heights(value, count):
    return np.broadcast_to(np.asarray(value, dtype=float), (count,))


def los_clear_many(dem, px_m, a, b, h_a, h_b, R=MOON_R, step_px=1.0, chunk_samples=3_000_000):
    """Line of sight for many antenna pairs.

    a, b: (M, 2) pixel coordinates (col, row). h_a, h_b: antenna heights above ground in
    metres, scalar or per pair. Returns (clear, clearance_m): the smallest gap between the
    ray and the terrain along each pair, negative where terrain intersects the ray.
    """
    a, b = np.atleast_2d(np.asarray(a, float)), np.atleast_2d(np.asarray(b, float))
    count = len(a)
    h_a, h_b = _heights(h_a, count), _heights(h_b, count)
    rows, cols = dem.shape
    ai, bi = np.rint(a).astype(int), np.rint(b).astype(int)
    za = dem[ai[:, 1].clip(0, rows - 1), ai[:, 0].clip(0, cols - 1)] + h_a
    zb = dem[bi[:, 1].clip(0, rows - 1), bi[:, 0].clip(0, cols - 1)] + h_b
    delta = b - a
    length_px = np.hypot(delta[:, 0], delta[:, 1])
    clearance = np.full(count, np.inf)
    order = np.argsort(length_px)
    start = 0
    while start < count:
        samples = max(3, int(np.ceil(length_px[order[start]] / step_px)) + 1)
        size = max(1, chunk_samples // samples)
        index = order[start:start + size]
        samples = max(3, int(np.ceil(length_px[index[-1]] / step_px)) + 1)
        t = np.linspace(0, 1, samples)[1:-1]
        x = np.rint(a[index, 0, None] + delta[index, 0, None] * t).astype(int)
        y = np.rint(a[index, 1, None] + delta[index, 1, None] * t).astype(int)
        inside = (x >= 0) & (x < cols) & (y >= 0) & (y < rows)
        ground = np.where(inside, dem[y.clip(0, rows - 1), x.clip(0, cols - 1)], np.nan)
        distance = length_px[index, None] * px_m
        bulge = (distance * t) * (distance * (1 - t)) / (2 * R)
        line = za[index, None] + (zb[index] - za[index])[:, None] * t
        gap = line - (ground + bulge)
        clearance[index] = np.where(np.isnan(gap).any(axis=1), np.nan, np.min(gap, axis=1, initial=np.inf, where=~np.isnan(gap)))
        start += size
    clearance = np.where(np.isfinite(za) & np.isfinite(zb), clearance, np.nan)
    return np.nan_to_num(clearance, nan=-1.0) > 0, clearance


def los_clear(dem, px_m, ax, ay, bx, by, h_a, h_b, R=MOON_R):
    """True if antenna A at pixel (ax, ay) sees antenna B at (bx, by)."""
    return bool(los_clear_many(dem, px_m, [(ax, ay)], [(bx, by)], h_a, h_b, R)[0][0])


def viewshed_fraction(dem, px_m, sites, h_tx, h_rx=2.0, radius_m=8_000, R=MOON_R, azimuths=120, step_m=None):
    """Fraction of the disc of ``radius_m`` around each site that a receiver at ``h_rx`` can see.

    Radial sweep: along each azimuth a cell is visible when its elevation angle from the
    transmitter is at least the largest terrain angle before it. Cells with missing terrain
    count as not visible. Returns (fraction, visible_area_km2), each of shape (len(sites),).
    """
    sites = np.atleast_2d(np.asarray(sites, float))
    step_m = step_m or 2 * px_m
    radii = np.arange(step_m, radius_m + 1e-9, step_m)
    theta = np.arange(azimuths) * 2 * np.pi / azimuths
    rows, cols = dem.shape
    weights = radii * step_m * (2 * np.pi / azimuths)  # area of one polar cell
    fraction, area = np.zeros(len(sites)), np.zeros(len(sites))
    for i, (cx, cy) in enumerate(sites):
        x = np.rint(cx + np.sin(theta)[:, None] * radii / px_m).astype(int)
        y = np.rint(cy - np.cos(theta)[:, None] * radii / px_m).astype(int)
        inside = (x >= 0) & (x < cols) & (y >= 0) & (y < rows)
        ground = np.where(inside, dem[y.clip(0, rows - 1), x.clip(0, cols - 1)], np.nan)
        z_tx = dem[int(round(cy)), int(round(cx))] + h_tx
        drop = radii ** 2 / (2 * R)
        terrain_angle = (ground - drop - z_tx) / radii
        target_angle = (ground + h_rx - drop - z_tx) / radii
        blocked_by = np.maximum.accumulate(np.nan_to_num(terrain_angle, nan=-np.inf), axis=1)
        before = np.concatenate([np.full((azimuths, 1), -np.inf), blocked_by[:, :-1]], axis=1)
        visible = np.isfinite(ground) & (target_angle >= before)
        area[i] = float((visible * weights).sum())
        fraction[i] = area[i] / float(weights.sum() * azimuths)
    return fraction, area / 1e6


def visible_cells(dem, px_m, site, cells, h_tx, h_rx=2.0, R=MOON_R):
    """Exact line of sight from one site to an (N, 2) array of receiver cells."""
    site = np.broadcast_to(np.asarray(site, float), (len(cells), 2))
    return los_clear_many(dem, px_m, site, cells, h_tx, h_rx, R, step_px=2.0)[0]


def radio_horizon_m(height_m, R=MOON_R):
    """Distance to the horizon over smooth ground for an antenna ``height_m`` up: sqrt(2*R*h).

    Two masts see each other out to the sum of their horizons, so the Moon's small radius keeps
    links short: 3 m masts reach about 6.5 km, 10 m masts about 11.8 km.
    """
    return float(np.sqrt(2 * R * height_m))
