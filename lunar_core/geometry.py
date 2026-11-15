from datetime import timezone
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


def satellite_vectors(orbit, times):
    """Satellite position (km) relative to the Moon centre in MOON_ME, shape (n, 3)."""
    from .orbit import propagate_inertial
    with SPICE_LOCK:
        epochs = np.array([spice.str2et(stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")) for stamp in times])
        start = spice.str2et(orbit.epoch.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f"))
        inertial, _ = propagate_inertial(orbit, epochs - start)
        rotations = np.array([spice.pxform("J2000", FRAME, et) for et in epochs])
    return np.einsum("nij,nj->ni", rotations, inertial)


def satellite_horizontal_arrays(orbit, point, times):
    """Azimuth (deg), elevation (deg) and range (km) of the satellite from a surface point."""
    lat, lon = np.radians([point.latitude_deg, point.longitude_deg])
    up = np.array([np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon), np.sin(lat)])
    east = np.array([-np.sin(lon), np.cos(lon), 0])
    north = np.cross(up, east)
    vectors = satellite_vectors(orbit, tuple(times)) - up * ((MOON_RADIUS_M + point.height_m) / 1000)
    e, n, u = vectors @ east, vectors @ north, vectors @ up
    return np.degrees(np.arctan2(e, n)) % 360, np.degrees(np.arctan2(u, np.hypot(e, n))), np.linalg.norm(vectors, axis=1)


def satellite_earth_arrays(orbit, times):
    """Satellite-to-Earth range (km) and whether the Moon (a sphere) occults the line of sight."""
    times = tuple(times)
    sat = satellite_vectors(orbit, times)
    to_earth = body_vectors(Body.EARTH, times) - sat
    distance = np.linalg.norm(to_earth, axis=1)
    direction = to_earth / distance[:, None]
    # Closest approach of the segment sat->Earth to the Moon centre.
    along = np.clip(-np.einsum("ij,ij->i", sat, direction), 0, distance)
    closest = np.linalg.norm(sat + direction * along[:, None], axis=1)
    return distance, closest < MOON_RADIUS_M / 1000


def horizontal_positions(body, point, times):
    return [HorizontalPosition(*row) for row in zip(*horizontal_arrays(body, point, times), strict=True)]
