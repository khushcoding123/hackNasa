"""Earth visibility: "Earth is not high in the Moon's sky".

Two levels. A smooth-sphere formula with a simple libration model, for intuition. And a
direct-to-Earth (DTE) availability map from SPICE Earth directions against the terrain horizon,
for the whole cap south of 85 S on the 240 m LOLA product.
"""
from datetime import UTC, date, datetime, time, timedelta

import numpy as np

from lunar_core.geometry import Body, SurfacePoint, horizontal_arrays
from lunar_core.horizon import horizon_mask
from lunar_core.kernels import load_kernels
from lunar_core.terrain import MOON_RADIUS_M, TerrainGrid, polar_stereographic_south_inverse
from moonmesh.data import DATA, PRODUCTS

CACHE = DATA / "ora_cache"


def earth_elevation_deg(lat, lon, sub_lat, sub_lon):
    """Elevation of Earth above a site's local horizon (degrees), on a smooth sphere.

    All inputs in degrees (selenographic). sub_lat/sub_lon is the sub-Earth point (libration).
    Distant-body approximation: ignores topocentric parallax (under about 0.3 degrees).
    """
    la, lo, sa, so = map(np.radians, (lat, lon, sub_lat, sub_lon))
    s = np.sin(la) * np.sin(sa) + np.cos(la) * np.cos(sa) * np.cos(lo - so)
    return np.degrees(np.arcsin(np.clip(s, -1, 1)))


def libration(t_days):
    """Simple libration model for exploration only. The map below uses SPICE instead."""
    return 6.7 * np.sin(2 * np.pi * t_days / 27.212), 7.9 * np.sin(2 * np.pi * t_days / 27.555)


def simple_visibility(lat, lon=0.0, days=27.3, samples=2000):
    """Smooth-sphere Earth elevation range and share of time above the horizon, from the simple model."""
    t = np.linspace(0, days, samples)
    elevation = earth_elevation_deg(lat, lon, *libration(t))
    return dict(min_deg=float(elevation.min()), max_deg=float(elevation.max()), visible_fraction=float((elevation > 0).mean()))


def polar_grid():
    """The whole 240 m south polar product as one TerrainGrid (about 915 km across)."""
    dimension, pixel = PRODUCTS["LDEM_75S_240M"]
    path = DATA / "terrain" / "LDEM_75S_240M.IMG"
    if not path.is_file():
        raise FileNotFoundError("LOLA 240 m terrain is missing. Run python -m moonmesh.data.")
    raw = np.memmap(path, dtype="<i2", mode="r", shape=(dimension, dimension))
    heights = np.where((raw == -32768) | (raw == -32767), np.nan, raw * 0.5).astype(np.float32)
    edge = dimension * pixel / 2
    return TerrainGrid(heights, -edge, edge, pixel)


def dte_map(lat_limit=-85.0, cell_km=5.0, start=date(2026, 1, 1), days=365, step_h=6.0, mast_m=10.0, clearance_deg=0.1,
            refresh=False):
    """Share of time Earth is visible from each cell south of ``lat_limit``.

    Returns a dict with two (N, N) grids on a south polar stereographic lattice: ``terrain``
    (Earth centre above the 240 m terrain horizon plus clearance) and ``flat`` (above a smooth
    sphere's horizon), NaN outside the latitude limit. Cached on disk.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    name = f"dte_{abs(lat_limit):g}S_{cell_km:g}km_{start.isoformat()}_{days}d_{step_h:g}h_{mast_m:g}m.npz"
    path = CACHE / name
    if path.is_file() and not refresh:
        with np.load(path) as data:
            return {key: data[key] for key in data.files}
    load_kernels(DATA / "kernels")
    grid = polar_grid()
    extent = 2 * MOON_RADIUS_M * np.tan(np.radians(90 + lat_limit) / 2)
    axis = np.arange(-extent + cell_km * 500, extent, cell_km * 1000)
    begin = datetime.combine(start, time(), UTC)
    times = tuple(begin + timedelta(hours=step_h * i) for i in range(int(days * 24 / step_h)))
    terrain = np.full((len(axis), len(axis)), np.nan, np.float32)
    flat, elevation_max = terrain.copy(), terrain.copy()
    for row, y in enumerate(axis[::-1]):
        for col, x in enumerate(axis):
            lat, lon = polar_stereographic_south_inverse(x, y)
            if lat > lat_limit:
                continue
            ground = float(grid.sample(x, y)[0])
            if not np.isfinite(ground):
                continue
            azimuth, elevation, _ = horizontal_arrays(Body.EARTH, SurfacePoint(float(lat), float(lon), ground + mast_m), times)
            horizon = horizon_mask(float(lat), float(lon), grid, grid, mast_m)
            limit = horizon.at(azimuth)
            dip = -np.degrees(np.arccos((MOON_RADIUS_M + ground) / (MOON_RADIUS_M + ground + mast_m)))
            terrain[row, col] = np.mean(np.where(np.isfinite(limit), elevation - limit - clearance_deg > 0, False))
            flat[row, col] = np.mean(elevation > dip)
            elevation_max[row, col] = elevation.max()
    out = dict(terrain=terrain, flat=flat, elevation_max=elevation_max, extent_m=np.float64(extent), cell_km=np.float64(cell_km),
               lat_limit=np.float64(lat_limit), samples=np.int64(len(times)), days=np.int64(days))
    with path.with_suffix(".partial").open("wb") as file:
        np.savez_compressed(file, **out)
    path.with_suffix(".partial").replace(path)
    return out
