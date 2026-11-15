"""Two-body (point-mass Moon) orbit propagation.

Elements are selenocentric and inertial (J2000 axes). This is an analytic
Keplerian model: no lunar gravity harmonics, no third-body or SRP forces, so
it is only suitable for short, simulated scenarios. Units: km, seconds, degrees.
"""
from dataclasses import dataclass

import numpy as np

MU_MOON_KM3_S2 = 4902.800118  # DE440 lunar GM
MOON_RADIUS_KM = 1737.4


@dataclass(frozen=True)
class Orbit:
    semi_major_axis_km: float
    eccentricity: float
    inclination_deg: float
    raan_deg: float
    arg_periapsis_deg: float
    mean_anomaly_deg: float  # at epoch
    epoch: "object"  # datetime (UTC) at which mean_anomaly_deg applies

    def __post_init__(self):
        if not np.all(np.isfinite([self.semi_major_axis_km, self.eccentricity, self.inclination_deg, self.raan_deg, self.arg_periapsis_deg, self.mean_anomaly_deg])):
            raise ValueError("orbital elements must be finite")
        if not 0 <= self.eccentricity < 1:
            raise ValueError("eccentricity must be in [0, 1)")
        if self.semi_major_axis_km * (1 - self.eccentricity) <= MOON_RADIUS_KM:
            raise ValueError("periapsis is below the lunar surface")

    @property
    def period_s(self) -> float:
        return 2 * np.pi * np.sqrt(self.semi_major_axis_km**3 / MU_MOON_KM3_S2)


def solve_kepler(mean_anomaly, eccentricity, tolerance=1e-13):
    """Eccentric anomaly E from M = E - e sin E (Newton, vectorized)."""
    m = np.mod(np.asarray(mean_anomaly, dtype=float) + np.pi, 2 * np.pi) - np.pi
    e = np.where(eccentricity < 0.8, m, np.pi * np.sign(m) + 0 * m)
    for _ in range(50):
        step = (e - eccentricity * np.sin(e) - m) / (1 - eccentricity * np.cos(e))
        e = e - step
        if np.max(np.abs(step)) < tolerance:
            break
    return e


def propagate_inertial(orbit: Orbit, seconds_since_epoch):
    """Position (km) and velocity (km/s) in selenocentric J2000 axes, shape (n, 3)."""
    t = np.atleast_1d(np.asarray(seconds_since_epoch, dtype=float))
    a, ecc = orbit.semi_major_axis_km, orbit.eccentricity
    n = np.sqrt(MU_MOON_KM3_S2 / a**3)
    anomaly = solve_kepler(np.radians(orbit.mean_anomaly_deg) + n * t, ecc)
    cos_e, sin_e = np.cos(anomaly), np.sin(anomaly)
    root = np.sqrt(1 - ecc**2)
    # Perifocal frame
    px, py = a * (cos_e - ecc), a * root * sin_e
    rate = n * a / (1 - ecc * cos_e)
    vx, vy = -rate * sin_e, rate * root * cos_e
    i, raan, argp = np.radians([orbit.inclination_deg, orbit.raan_deg, orbit.arg_periapsis_deg])
    cr, sr, ci, si, cw, sw = np.cos(raan), np.sin(raan), np.cos(i), np.sin(i), np.cos(argp), np.sin(argp)
    rot = np.array([
        [cr*cw - sr*sw*ci, -cr*sw - sr*cw*ci, sr*si],
        [sr*cw + cr*sw*ci, -sr*sw + cr*cw*ci, -cr*si],
        [sw*si, cw*si, ci],
    ])
    pos = np.stack([px, py, np.zeros_like(px)], axis=1) @ rot.T
    vel = np.stack([vx, vy, np.zeros_like(vx)], axis=1) @ rot.T
    return pos, vel


PRESETS = {
    # Illustrative geometries, not mission designs. RAAN/argp/anomaly are arbitrary.
    "polar_500km": dict(semi_major_axis_km=MOON_RADIUS_KM + 500, eccentricity=0.0, inclination_deg=90.0,
                        raan_deg=0.0, arg_periapsis_deg=0.0, mean_anomaly_deg=0.0),
    "frozen_elliptical_south": dict(semi_major_axis_km=6142.4, eccentricity=0.6, inclination_deg=57.7,
                                    raan_deg=0.0, arg_periapsis_deg=270.0, mean_anomaly_deg=0.0),
}
