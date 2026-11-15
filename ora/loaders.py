"""Load an elevation model as ``(dem, px_m)``: a 2D array of heights in metres and its pixel size.

Supported without extra packages: MOONMESH terrain windows (.npz), SRTM-style tiles (.hgt or
.hgt.gz, resampled from degrees to square metric pixels) and LOLA polar PDS images (.IMG).
GeoTIFFs (for example NASADEM or the PGDA LOLA products) need ``rasterio``; geographic rasters
are resampled to metric pixels the same way as .hgt tiles.
"""
import gzip
import math
from pathlib import Path

import numpy as np

from lunar_core.terrain import TerrainGrid

EARTH_RADIUS_M = 6_371_000
LOLA_SCALE_M = 0.5  # LOLA GDR .IMG: signed 16-bit, half-metre steps, relative to 1737.4 km


def _metric(raw, lat_centre, degrees_per_row, degrees_per_col, pixel_m, radius_m):
    metres_lat = math.radians(degrees_per_row * raw.shape[0]) * radius_m
    metres_lon = math.radians(degrees_per_col * raw.shape[1]) * radius_m * math.cos(math.radians(lat_centre))
    rows, cols = max(1, int(metres_lat / pixel_m)), max(1, int(metres_lon / pixel_m))
    ri = np.minimum((np.arange(rows) + 0.5) * raw.shape[0] / rows, raw.shape[0] - 1).astype(int)
    ci = np.minimum((np.arange(cols) + 0.5) * raw.shape[1] / cols, raw.shape[1] - 1).astype(int)
    return raw[np.ix_(ri, ci)]


def load_dem(path, pixel_m=None):
    """Return (dem, px_m). ``pixel_m`` sets the output pixel for geographic inputs (default 30 m)."""
    path = Path(path)
    name = path.name.lower()
    if name.endswith(".npz"):
        grid = TerrainGrid.load(path)
        return grid.heights, grid.pixel_size_m
    if name.endswith((".hgt", ".hgt.gz")):
        opener = gzip.open if name.endswith(".gz") else open
        with opener(path, "rb") as file:
            data = np.frombuffer(file.read(), dtype=">i2")
        side = int(round(math.sqrt(data.size)))
        raw = data.reshape(side, side).astype(np.float32)
        raw[raw < -1000] = np.nan
        lat0 = int(path.name[1:3]) * (1 if path.name[0].upper() == "N" else -1)
        pixel_m = pixel_m or 30.0
        return _metric(raw, lat0 + 0.5, 1 / side, 1 / side, pixel_m, EARTH_RADIUS_M), float(pixel_m)
    if name.endswith(".img"):
        label = path.with_suffix(".LBL") if path.with_suffix(".LBL").is_file() else path.with_suffix(".lbl")
        if not label.is_file():
            raise FileNotFoundError(f"Keep the PDS label next to the image: {label.name} is missing.")
        text = label.read_text(errors="replace")
        value = lambda key: float(next(line.split("=")[1].split("<")[0] for line in text.splitlines() if line.strip().startswith(key)))
        lines, samples, scale = int(value("LINES")), int(value("LINE_SAMPLES")), value("MAP_SCALE")
        raw = np.memmap(path, dtype="<i2", mode="r", shape=(lines, samples))
        return np.where((raw == -32768) | (raw == -32767), np.nan, raw * LOLA_SCALE_M).astype(np.float32), float(scale)
    if name.endswith((".tif", ".tiff")):
        try:
            import rasterio
        except ImportError as error:
            raise ImportError("Reading GeoTIFFs needs rasterio: python -m pip install rasterio") from error
        with rasterio.open(path) as source:
            raw = source.read(1).astype(np.float32)
            if source.nodata is not None:
                raw[raw == source.nodata] = np.nan
            if source.crs is not None and source.crs.is_geographic:
                centre = (source.bounds.top + source.bounds.bottom) / 2
                pixel_m = pixel_m or 30.0
                return _metric(raw, centre, abs(source.res[1]), abs(source.res[0]), pixel_m, EARTH_RADIUS_M), float(pixel_m)
            return raw, float(abs(source.res[0]))
    raise ValueError(f"Unsupported elevation file: {path.name}")


def describe(path):
    dem, px = load_dem(path)
    return dict(file=str(path), shape=list(dem.shape), pixel_m=px, min_m=float(np.nanmin(dem)), max_m=float(np.nanmax(dem)),
                missing_pct=float(np.isnan(dem).mean() * 100))
