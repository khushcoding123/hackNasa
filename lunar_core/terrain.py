from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.ndimage import map_coordinates

MOON_RADIUS_M = 1_737_400


# A local frame turns the south polar projection into a projection centred on any point of the Moon:
# coordinates are first rotated so the chosen point sits at the pole. Scale error stays small near the
# centre, so terrain grids, line of sight and slopes keep working at any latitude. With no frame set
# (the default) the projection is the ordinary south polar one. The frame is per request context.
_FRAME: ContextVar = ContextVar("lunar_frame", default=None)


def local_frame(lat, lon):
    """Rotation matrix that moves (lat, lon) to the south pole of the projection."""
    la, lo = np.radians(lat), np.radians(lon)
    up = np.array([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])
    east = np.array([-np.sin(lo), np.cos(lo), 0.0])
    north = np.cross(up, east)
    return np.array([east, -north, -up])


def set_frame(rotation):
    _FRAME.set(None if rotation is None else np.asarray(rotation, float))


def current_frame():
    return _FRAME.get()


def _unit(lat, lon):
    lat, lon = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat) + 0 * lon])


def polar_stereographic_south(lat, lon):
    lat, lon = np.broadcast_arrays(np.asarray(lat, float), np.asarray(lon, float))
    rotation = _FRAME.get()
    if rotation is not None:
        v = np.tensordot(rotation, _unit(lat, lon), axes=1)
        lat, lon = np.degrees(np.arcsin(np.clip(v[2], -1, 1))), np.degrees(np.arctan2(v[1], v[0]))
    rho = 2 * MOON_RADIUS_M * np.tan(np.radians(90 + lat) / 2)
    lon = np.radians(lon)
    return rho * np.sin(lon), rho * np.cos(lon)


def polar_stereographic_south_inverse(x, y):
    x, y = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float))
    rho = np.hypot(x, y)
    lat, lon = np.degrees(2 * np.arctan(rho / (2 * MOON_RADIUS_M))) - 90, np.degrees(np.arctan2(x, y))
    rotation = _FRAME.get()
    if rotation is not None:
        v = rotation.T @ _unit(lat, lon).reshape(3, -1)
        lat = np.degrees(np.arcsin(np.clip(v[2], -1, 1))).reshape(x.shape)
        lon = np.degrees(np.arctan2(v[1], v[0])).reshape(x.shape)
    return lat, lon % 360


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
