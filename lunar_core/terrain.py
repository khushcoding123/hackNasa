from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.ndimage import map_coordinates

MOON_RADIUS_M = 1_737_400


def polar_stereographic_south(lat, lon):
    rho = 2 * MOON_RADIUS_M * np.tan(np.radians(90 + np.asarray(lat)) / 2)
    lon = np.radians(lon)
    return rho * np.sin(lon), rho * np.cos(lon)


def polar_stereographic_south_inverse(x, y):
    rho = np.hypot(x, y)
    return np.degrees(2 * np.arctan(rho / (2 * MOON_RADIUS_M))) - 90, np.degrees(np.arctan2(x, y)) % 360


@dataclass(frozen=True)
class TerrainGrid:
    heights: np.ndarray
    x_left: float
    y_top: float
    pixel_size_m: float

    @classmethod
    def load(cls, path: Path):
        with np.load(path) as data:
            return cls(data["heights"], float(data["x_left"]), float(data["y_top"]), float(data["pixel_size_m"]))

    def sample(self, x, y):
        x, y = np.broadcast_arrays(np.atleast_1d(x), np.atleast_1d(y))
        cols = (x - self.x_left) / self.pixel_size_m - 0.5
        rows = (self.y_top - y) / self.pixel_size_m - 0.5
        return map_coordinates(self.heights, [rows, cols], order=1, mode="constant", cval=np.nan)


def radial_points(lat, lon, azimuth, distance_m):
    lat, lon, azimuth = np.radians(lat), np.radians(lon), np.radians(azimuth)
    angle = np.asarray(distance_m) / MOON_RADIUS_M
    out_lat = np.arcsin(np.clip(np.sin(lat)*np.cos(angle) + np.cos(lat)*np.sin(angle)*np.cos(azimuth), -1, 1))
    out_lon = lon + np.arctan2(np.sin(azimuth)*np.sin(angle)*np.cos(lat), np.cos(angle)-np.sin(lat)*np.sin(out_lat))
    return np.degrees(out_lat), np.degrees(out_lon) % 360
