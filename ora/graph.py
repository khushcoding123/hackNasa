"""Terrain to graph: candidate sites and a line-of-sight graph with an RF margin gate."""
import networkx as nx
import numpy as np
from scipy.ndimage import maximum_filter
from scipy.spatial import cKDTree

from lunar_core.terrain import polar_stereographic_south_inverse
from moonmesh.network import link_margin

from .los import MOON_R, los_clear_many


def slope_deg(dem, px_m):
    dy, dx = np.gradient(dem, px_m, px_m)
    return np.degrees(np.arctan(np.hypot(dx, dy)))


def candidate_sites(dem, px_m, count, center=None, radius_m=None, slope_limit=20.0, peak_window_m=400,
                    min_spacing_m=300, seed=0):
    """Candidate relay sites as (N, 2) pixel coordinates (col, row).

    Half are the highest local terrain maxima (ridges and rims, where towers see farthest).
    The rest are a seeded random sample of gentle-slope cells, so chains can step down crater
    walls and across floors where there are no peaks.
    """
    rng = np.random.default_rng(seed)
    rows, cols = dem.shape
    slope = slope_deg(dem, px_m)
    ok = np.isfinite(dem) & (slope <= slope_limit)
    if center is not None and radius_m is not None:
        yy, xx = np.ogrid[:rows, :cols]
        ok &= np.hypot(xx - center[0], yy - center[1]) * px_m <= radius_m
    window = max(3, int(round(peak_window_m / px_m)) | 1)
    peaks = ok & (dem == maximum_filter(np.nan_to_num(dem, nan=-np.inf), size=window))
    py, px = np.nonzero(peaks)
    ranked = np.argsort(dem[py, px])[::-1]
    pool = [np.column_stack([px[ranked], py[ranked]])[: count * 4]]
    fy, fx = np.nonzero(ok)
    pick = rng.choice(len(fy), size=min(len(fy), count * 20), replace=False)
    pool.append(np.column_stack([fx[pick], fy[pick]]))
    spacing = max(min_spacing_m / px_m, 1.0)
    chosen, buckets = [], {}
    for group, quota in zip(pool, (count // 2, count)):
        for x, y in group:
            if len(chosen) >= quota:
                break
            cell = (int(x // spacing), int(y // spacing))
            near = (p for dx in (-1, 0, 1) for dy in (-1, 0, 1) for p in buckets.get((cell[0] + dx, cell[1] + dy), ()))
            if all(np.hypot(x - p[0], y - p[1]) >= spacing for p in near):
                chosen.append((x, y))
                buckets.setdefault(cell, []).append((x, y))
    return np.array(chosen, dtype=float)


def build_los_graph(dem, px_m, sites_xy, h_ant=3.0, max_range_m=15_000, R=MOON_R, radio=None, heights=None):
    """Graph whose edges exist only with true line of sight and, if ``radio`` is given, a viable link.

    sites_xy: (N, 2) pixel coordinates (col, row). heights: optional per-site antenna heights.
    Edges carry dist (m), clearance (m), kind="rf" and, with a radio, margin (dB).
    """
    sites_xy = np.asarray(sites_xy, float)
    heights = np.full(len(sites_xy), h_ant, float) if heights is None else np.asarray(heights, float)
    G = nx.Graph()
    for i, (x, y) in enumerate(sites_xy):
        G.add_node(i, x=float(x), y=float(y), z=float(dem[int(round(y)), int(round(x))]), h=float(heights[i]))
    pairs = np.array(sorted(cKDTree(sites_xy * px_m).query_pairs(max_range_m)), dtype=int).reshape(-1, 2)
    if not len(pairs):
        return G
    clear, clearance = los_clear_many(dem, px_m, sites_xy[pairs[:, 0]], sites_xy[pairs[:, 1]],
                                      heights[pairs[:, 0]], heights[pairs[:, 1]], R)
    dist = np.hypot(*(sites_xy[pairs[:, 0]] - sites_xy[pairs[:, 1]]).T) * px_m
    margin = link_margin(radio, np.maximum(dist, 1.0) / 1000) if radio is not None else None
    for k, (i, j) in enumerate(pairs):
        if not clear[k] or (margin is not None and margin[k] < radio.margin):
            continue
        G.add_edge(int(i), int(j), dist=float(dist[k]), clearance=float(clearance[k]), kind="rf",
                   **({"margin": float(margin[k])} if margin is not None else {}))
    return G


def unit_disk_graph(sites_xy, px_m, max_range_m):
    """Null model: the same nodes joined whenever they are within range, ignoring terrain."""
    sites_xy = np.asarray(sites_xy, float)
    G = nx.Graph()
    G.add_nodes_from(range(len(sites_xy)))
    for i, j in cKDTree(sites_xy * px_m).query_pairs(max_range_m):
        G.add_edge(int(i), int(j), dist=float(np.hypot(*(sites_xy[i] - sites_xy[j])) * px_m), kind="rf")
    return G


def degree_matched_disk(sites_xy, px_m, edges):
    """Unit-disk graph whose range is shrunk until it has about ``edges`` edges (same mean degree)."""
    sites_xy = np.asarray(sites_xy, float)
    tree = cKDTree(sites_xy * px_m)
    low, high = 0.0, float(np.hypot(*(sites_xy.max(axis=0) - sites_xy.min(axis=0))) * px_m) + 1
    for _ in range(40):
        middle = (low + high) / 2
        if len(tree.query_pairs(middle)) < edges:
            low = middle
        else:
            high = middle
    return unit_disk_graph(sites_xy, px_m, high), high


def to_latlon(grid, sites_xy):
    """Latitude and east longitude of pixel coordinates in a south polar stereographic TerrainGrid."""
    x = grid.x_left + (np.asarray(sites_xy)[:, 0] + 0.5) * grid.pixel_size_m
    y = grid.y_top - (np.asarray(sites_xy)[:, 1] + 0.5) * grid.pixel_size_m
    return polar_stereographic_south_inverse(x, y)
