from dataclasses import dataclass

import numpy as np

from .terrain import MOON_RADIUS_M, polar_stereographic_south, radial_points


@dataclass(frozen=True)
class HorizonMask:
    azimuth_deg: np.ndarray
    elevation_deg: np.ndarray
    distance_m: np.ndarray

    def at(self, azimuth):
        azimuth = np.asarray(azimuth) % 360
        left = np.floor(azimuth / (360 / len(self.azimuth_deg))).astype(int)
        right = (left + 1) % len(self.azimuth_deg)
        weight = azimuth / (360 / len(self.azimuth_deg)) - left
        return np.where(weight == 0, self.elevation_deg[left],
                        self.elevation_deg[left] * (1 - weight) + self.elevation_deg[right] * weight)

    def obstruction_distance(self, azimuth):
        nearest = np.rint(np.asarray(azimuth) / (360 / len(self.azimuth_deg))).astype(int)
        return self.distance_m[nearest % len(self.azimuth_deg)]


def horizon_mask(lat, lon, near, far, observer_height_m=2, azimuth_step=2, elevation_bias_m=0):
    if not 1 <= azimuth_step <= 30 or 360 % azimuth_step:
        raise ValueError("Azimuth step must divide 360 and be between 1 and 30 degrees.")
    ground = near.sample(*polar_stereographic_south(lat, lon))[0]
    if not np.isfinite(ground):
        raise ValueError("Observer position lacks local terrain coverage.")
    azimuth = np.arange(0, 360, azimuth_step)
    near_dist = np.arange(near.pixel_size_m, 10_001, near.pixel_size_m)
    far_dist = np.arange(10_000 + far.pixel_size_m, 150_001, far.pixel_size_m)
    distance = np.concatenate([near_dist, far_dist])
    x, y = polar_stereographic_south(*radial_points(lat, lon, azimuth[:, None], distance))
    height = np.concatenate([near.sample(x[:, :len(near_dist)], y[:, :len(near_dist)]),
                             far.sample(x[:, len(near_dist):], y[:, len(near_dist):])], axis=1)
    height += elevation_bias_m * np.minimum(distance / 1000, 1)
    radius = MOON_RADIUS_M + height
    angle = distance / MOON_RADIUS_M
    elevation = np.degrees(np.arctan2(radius * np.cos(angle) - (MOON_RADIUS_M + ground + observer_height_m),
                                    radius * np.sin(angle)))
    indices = np.argmax(np.where(np.isfinite(elevation), elevation, -np.inf), axis=1)
    complete = np.all(np.isfinite(elevation), axis=1)
    return HorizonMask(azimuth, np.where(complete, elevation[np.arange(len(azimuth)), indices], np.nan),
                       np.where(complete, distance[indices], np.nan))
