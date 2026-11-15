"""Read-only terrain sampling for the browser's Cesium Moon viewer.

The browser requests a rectangle of the Moon in degrees and receives a regular grid of elevation samples
(metres above the 1,737,400 m lunar reference sphere). It builds the Cesium terrain mesh and normals itself.

Source priority is decided per sample, never per tile, so neighbouring tiles agree exactly on shared edges:
  1. LOLA 20 m near-field DEM around Haworth, Nobile Rim 1 and Malapert Massif
  2. LOLA 240 m far-field DEM around the same regions
  3. the global 5760 x 2880 LOLA elevation grid (16 px/deg) shipped in web/assets
Region edges are feathered so that a source change does not create a visible step.
"""
from dataclasses import dataclass
from functools import lru_cache
from math import floor, log2
from pathlib import Path

import numpy as np

from lunar_core.terrain import MOON_RADIUS_M, TerrainGrid, polar_stereographic_south

from .data import DATA, LOCAL, SITES

ROOT = Path(__file__).resolve().parents[1]
GLOBAL_META = ROOT / "web" / "assets" / "moon-elevation.json"
GLOBAL_GRID = ROOT / "web" / "assets" / "moon-elevation.i16"
PADDING = 1
MIN_SIZE, MAX_SIZE, DEFAULT_SIZE = 5, 129, 65   # 65 samples per tile: about 4x fewer requests than 33 for the same detail
# Region edges blend into the next source over this many source pixels.
FEATHER_PIXELS = {"near20km": 40, "far": 60}
SOURCE_LABELS = {
    "near20km": "LOLA LDEM_85S_20M · 20 m/pixel",
    "far": "LOLA LDEM_75S_240M · 240 m/pixel",
    "global": "LOLA global 5760 × 2880 grid · about 1.9 km/pixel at the equator",
}


@dataclass(frozen=True)
class GlobalGrid:
    values: np.ndarray
    resolution_m: float
    north_pole: float
    south_pole: float

    @property
    def height(self):
        return self.values.shape[0]

    @property
    def width(self):
        return self.values.shape[1]


@lru_cache(maxsize=1)
def global_grid() -> GlobalGrid:
    import json

    if not GLOBAL_META.is_file() or not GLOBAL_GRID.is_file():
        raise FileNotFoundError("Global elevation grid is missing from web/assets.")
    meta = json.loads(GLOBAL_META.read_text())
    if meta["encoding"] != "int16-le" or meta["unit"] != "m" or meta["referenceRadiusM"] != MOON_RADIUS_M:
        raise ValueError("Unsupported global elevation encoding.")
    values = np.fromfile(GLOBAL_GRID, dtype="<i2")
    if values.size != meta["width"] * meta["height"]:
        raise ValueError("Global elevation grid has an unexpected size.")
    values = values.reshape(meta["height"], meta["width"]).astype(np.float32)
    resolution = 2 * np.pi * MOON_RADIUS_M / meta["width"]
    return GlobalGrid(values, float(resolution), float(values[0].mean()), float(values[-1].mean()))


def sample_global(grid: GlobalGrid, lat, lon):
    """Bilinear, pixel-centred sampling with longitude wrap. Poles resolve to the mean of the polar row,
    so every longitude agrees there."""
    lat, lon = np.asarray(lat, dtype=np.float64), np.asarray(lon, dtype=np.float64)
    h, w = grid.height, grid.width
    y = (90 - lat) / 180 * h - 0.5
    x = (((lon + 180) % 360) / 360) * w - 0.5
    x0 = np.floor(x).astype(int)
    fx = x - x0
    y0 = np.clip(np.floor(y).astype(int), 0, h - 2)
    fy = np.clip(y - y0, 0, 1)
    v = grid.values
    top = v[y0, x0 % w] * (1 - fx) + v[y0, (x0 + 1) % w] * fx
    bottom = v[y0 + 1, x0 % w] * (1 - fx) + v[y0 + 1, (x0 + 1) % w] * fx
    out = top * (1 - fy) + bottom * fy
    # Half a pixel between the first/last pixel centre and the pole: interpolate to the polar mean.
    north = y < 0
    if np.any(north):
        out = np.where(north, grid.north_pole + (v[0, x0 % w] * (1 - fx) + v[0, (x0 + 1) % w] * fx - grid.north_pole) * (y + 0.5) / 0.5, out)
    south = y > h - 1
    if np.any(south):
        out = np.where(south, grid.south_pole + (v[h - 1, x0 % w] * (1 - fx) + v[h - 1, (x0 + 1) % w] * fx - grid.south_pole) * (h - 0.5 - y) / 0.5, out)
    return out


@lru_cache(maxsize=32)
def region_grid(site: str, suffix: str) -> TerrainGrid | None:
    path = DATA / "terrain" / f"{site}_{suffix}.npz"
    return TerrainGrid.load(path) if path.is_file() else None


def sample_region(grid: TerrainGrid, suffix: str, x, y):
    """Return (heights, weight). Weight is 1 inside the DEM and falls to 0 at its edge or at no-data."""
    heights = grid.sample(x, y)
    rows, cols = grid.heights.shape
    px = grid.pixel_size_m
    feather = FEATHER_PIXELS[suffix] * px
    inside = np.minimum.reduce([
        x - grid.x_left, grid.x_left + cols * px - x, grid.y_top - y, y - (grid.y_top - rows * px),
    ])
    weight = np.clip(inside / feather, 0, 1)
    weight = np.where(np.isfinite(heights), weight, 0.0)
    return np.nan_to_num(heights, nan=0.0), weight


def region_extent(grid: TerrainGrid):
    rows, cols = grid.heights.shape
    px = grid.pixel_size_m
    return dict(x_min=grid.x_left, x_max=grid.x_left + cols * px, y_min=grid.y_top - rows * px, y_max=grid.y_top, pixel_m=px)


def level_for(resolution_m: float, size: int = DEFAULT_SIZE) -> int:
    """Deepest geographic-tiling level whose sample spacing is at least 60 % of the source spacing, so tiles
    never sample a source much more finely than it was measured."""
    spacing_level0 = np.pi * MOON_RADIUS_M / (size - 1)
    return max(0, floor(log2(spacing_level0 / (0.6 * resolution_m))))


@lru_cache(maxsize=1)
def info(size: int = DEFAULT_SIZE):
    grid = global_grid()
    regions = []
    for key, (name, lat, lon) in [i for i in SITES.items() if i[0] not in LOCAL]:
        near, far = region_grid(key, "near20km"), region_grid(key, "far")
        if near is None and far is None:
            continue
        regions.append(dict(
            id=key, name=name, lat=lat, lon=lon,
            near=region_extent(near) if near else None, far=region_extent(far) if far else None,
        ))
    return dict(
        unit="m",
        vertical_reference=f"Height above the {MOON_RADIUS_M:,} m lunar reference sphere. Rendered at 1x scale.",
        reference_radius_m=MOON_RADIUS_M,
        padding=PADDING,
        default_size=DEFAULT_SIZE,
        min_size=MIN_SIZE,
        max_size=MAX_SIZE,
        sources={
            "global": dict(id="global", name=SOURCE_LABELS["global"], resolution_m=grid.resolution_m, max_level=level_for(grid.resolution_m, size)),
            "far": dict(id="far", name=SOURCE_LABELS["far"], resolution_m=240, max_level=level_for(240, size)),
            "near": dict(id="near20km", name=SOURCE_LABELS["near20km"], resolution_m=20, max_level=level_for(20, size)),
        },
        regions=regions,
        projection="South polar stereographic, as in the mission terrain crops",
    )


def sample_points(lat, lon):
    """Elevation (m) and per-source dominance for arbitrary points. Shapes are preserved."""
    lat, lon = np.asarray(lat, dtype=np.float64), np.asarray(lon, dtype=np.float64)
    # A rectangle's padded ring may leave the sphere: fold over the pole and wrap longitude.
    over = np.abs(lat) > 90
    lat = np.where(over, np.sign(lat) * 180 - lat, lat)
    lon = np.where(over, lon + 180, lon)
    lon = (lon + 180) % 360 - 180
    result = sample_global(global_grid(), lat, lon)
    best = np.full(lat.shape, 20_000.0)          # finest source resolution that dominates each sample
    chosen = np.full(lat.shape, "global", dtype=object)
    if np.any(lat < -70):
        x, y = polar_stereographic_south(lat, lon)
        regional = []
        for key in [k for k in SITES if k not in LOCAL]:
            for suffix, resolution in (("far", 240.0), ("near20km", 20.0)):
                grid = region_grid(key, suffix)
                if grid is not None:
                    regional.append((resolution, suffix, grid))
        for resolution, suffix, grid in sorted(regional, key=lambda item: -item[0]):   # coarse to fine
            heights, weight = sample_region(grid, suffix, x, y)
            result = np.where(weight > 0, result * (1 - weight) + heights * weight, result)
            dominant = weight >= 0.5
            better = dominant & (resolution < best)
            best = np.where(better, resolution, best)
            chosen = np.where(better, suffix, chosen)
    return result.astype(np.float32), best, chosen


class TileError(ValueError):
    pass


def tile(west, south, east, north, size=DEFAULT_SIZE):
    """(size + 2*PADDING)^2 elevations covering the rectangle, row 0 = north. Padding extends one sample step
    beyond every side so that the browser can compute identical normals on both sides of a tile edge."""
    if not all(np.isfinite([west, south, east, north])):
        raise TileError("Rectangle bounds must be finite.")
    if not (MIN_SIZE <= size <= MAX_SIZE):
        raise TileError(f"size must be between {MIN_SIZE} and {MAX_SIZE}.")
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise TileError("Rectangle must satisfy -90 <= south < north <= 90 and -180 <= west < east <= 180.")
    step_lon, step_lat = (east - west) / (size - 1), (north - south) / (size - 1)
    n = size + 2 * PADDING
    lons = west + (np.arange(n) - PADDING) * step_lon
    lats = north - (np.arange(n) - PADDING) * step_lat
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    heights, resolution, chosen = sample_points(lat_grid, lon_grid)
    inner = slice(PADDING, n - PADDING)
    inner_heights, inner_chosen = heights[inner, inner], chosen[inner, inner]
    used = {key: int(np.sum(inner_chosen == key)) for key in ("near20km", "far", "global")}
    finest = min(resolution[inner, inner].min(), global_grid().resolution_m)
    return dict(
        size=size,
        heights=np.ascontiguousarray(heights, dtype="<f4"),
        minimum=float(inner_heights.min()),
        maximum=float(inner_heights.max()),
        resolution_m=float(finest),
        sources=[key for key, count in used.items() if count],
        samples=used,
    )
