from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

import numpy as np
import spiceypy as spice

from .kernels import SPICE_LOCK
from .terrain import MOON_RADIUS_M

# The DE440 lunar frame kernel provides its DE440 ME realization. Its alignment
# with older ME realizations is approximate, so use the supported frame and state
# the DEM registration assumption in the scenario provenance.
FRAME = "MOON_ME"


class Body(StrEnum):
    EARTH = "EARTH"
    SUN = "SUN"


@dataclass(frozen=True)
class SurfacePoint:
    latitude_deg: float
    longitude_deg: float
    height_m: float = 0


@dataclass(frozen=True)
class HorizontalPosition:
    azimuth_deg: float
    elevation_deg: float
    distance_km: float


@lru_cache(maxsize=12)
def body_vectors(body, times):
    with SPICE_LOCK:
        epochs = [spice.str2et(stamp.strftime("%Y-%m-%dT%H:%M:%S")) for stamp in times]
        vectors, _ = spice.spkpos(str(body), epochs, FRAME, "NONE", "MOON")
    return vectors


def horizontal_arrays(body, point, times):
    lat, lon = np.radians([point.latitude_deg, point.longitude_deg])
    up = np.array([np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon), np.sin(lat)])
    east = np.array([-np.sin(lon), np.cos(lon), 0])
    north = np.cross(up, east)
    vectors = body_vectors(body, tuple(times)) - up * ((MOON_RADIUS_M + point.height_m) / 1000)
    e, n, u = vectors @ east, vectors @ north, vectors @ up
    return np.degrees(np.arctan2(e, n)) % 360, np.degrees(np.arctan2(u, np.hypot(e, n))), np.linalg.norm(vectors, axis=1)


def horizontal_positions(body, point, times):
    return [HorizontalPosition(*row) for row in zip(*horizontal_arrays(body, point, times), strict=True)]
