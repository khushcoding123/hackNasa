import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from lunar_core.orbit import MOON_RADIUS_KM, MU_MOON_KM3_S2, PRESETS, Orbit, propagate_inertial, solve_kepler

EPOCH = datetime(2026, 3, 1, tzinfo=timezone.utc)
KERNELS = Path(__file__).resolve().parents[1] / ".moonmesh-data" / "kernels"


def orbit(name="polar_500km", **changes):
    return Orbit(**{**PRESETS[name], **changes}, epoch=EPOCH)


class KeplerTests(unittest.TestCase):
    def test_kepler_equation_residual(self):
        for ecc in (0.0, 0.3, 0.6, 0.9):
            m = np.linspace(-6, 6, 200)
            e = solve_kepler(m, ecc)
            wrapped = np.mod(m + np.pi, 2 * np.pi) - np.pi
            self.assertLess(np.max(np.abs(e - ecc * np.sin(e) - wrapped)), 1e-10)

    def test_period_and_closure(self):
        o = orbit()
        pos, _ = propagate_inertial(o, [0, o.period_s])
        # Matches the front-end preview's ~158.3 min period for a 500 km orbit.
        self.assertAlmostEqual(o.period_s / 60, 158.28, delta=0.05)
        np.testing.assert_allclose(pos[0], pos[1], atol=1e-6)

    def test_conserved_quantities_elliptical(self):
        o = orbit("frozen_elliptical_south")
        pos, vel = propagate_inertial(o, np.linspace(0, o.period_s, 500))
        energy = 0.5 * (vel**2).sum(1) - MU_MOON_KM3_S2 / np.linalg.norm(pos, axis=1)
        momentum = np.linalg.norm(np.cross(pos, vel), axis=1)
        self.assertLess(np.ptp(energy) / abs(energy[0]), 1e-10)
        self.assertLess(np.ptp(momentum) / momentum[0], 1e-10)
        self.assertAlmostEqual(energy[0], -MU_MOON_KM3_S2 / (2 * o.semi_major_axis_km), places=8)

    def test_radius_bounds(self):
        o = orbit("frozen_elliptical_south")
        pos, _ = propagate_inertial(o, np.linspace(0, o.period_s, 400))
        r = np.linalg.norm(pos, axis=1)
        self.assertAlmostEqual(r.min(), o.semi_major_axis_km * (1 - o.eccentricity), delta=1)
        self.assertAlmostEqual(r.max(), o.semi_major_axis_km * (1 + o.eccentricity), delta=1)

    def test_polar_orbit_passes_over_poles(self):
        o = orbit()
        pos, _ = propagate_inertial(o, np.linspace(0, o.period_s, 721))
        self.assertAlmostEqual(np.abs(pos[:, 2]).max(), o.semi_major_axis_km, delta=1)

    def test_rejects_bad_orbits(self):
        with self.assertRaises(ValueError):
            orbit(eccentricity=1.0)
        with self.assertRaises(ValueError):
            orbit(semi_major_axis_km=MOON_RADIUS_KM)


class SyntheticGeometryTests(unittest.TestCase):
    def test_known_occultation_geometries_without_kernels(self):
        from unittest.mock import patch
        from lunar_core.geometry import satellite_earth_arrays
        positions = np.array([[2237.4,0,0],[-2237.4,0,0],[0,2237.4,0]])
        with patch("lunar_core.geometry.satellite_vectors", return_value=positions), patch(
            "lunar_core.geometry.body_vectors", return_value=np.tile([384400.,0,0],(3,1))):
            distance, occulted = satellite_earth_arrays(orbit(), [EPOCH]*3)
        self.assertEqual(occulted.tolist(), [False,True,False])
        self.assertAlmostEqual(distance[0],384400-2237.4)


@unittest.skipUnless((KERNELS / "de440s.bsp").is_file(), "Run python -m moonmesh.data (SPICE kernels missing)")
class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from lunar_core.kernels import load_kernels
        load_kernels(KERNELS)

    def test_radius_preserved_in_moon_frame(self):
        from lunar_core.geometry import satellite_vectors
        o = orbit()
        times = [EPOCH + timedelta(minutes=10 * i) for i in range(12)]
        r = np.linalg.norm(satellite_vectors(o, times), axis=1)
        np.testing.assert_allclose(r, o.semi_major_axis_km, atol=1e-6)

    def test_overhead_and_far_side(self):
        from lunar_core.geometry import SurfacePoint, satellite_horizontal_arrays, satellite_vectors
        o = orbit()
        times = [EPOCH + timedelta(minutes=2 * i) for i in range(60)]
        sat = satellite_vectors(o, times)
        # A point directly below the satellite sees it near 90 degrees elevation.
        lat = np.degrees(np.arcsin(sat[0, 2] / np.linalg.norm(sat[0])))
        lon = np.degrees(np.arctan2(sat[0, 1], sat[0, 0]))
        _, el, rng = satellite_horizontal_arrays(o, SurfacePoint(lat, lon), times[:1])
        self.assertAlmostEqual(el[0], 90, delta=0.01)
        self.assertAlmostEqual(rng[0], 500, delta=0.5)
        # The antipode never sees it at t0 (below horizon).
        _, el, _ = satellite_horizontal_arrays(o, SurfacePoint(-lat, lon + 180), times[:1])
        self.assertLess(el[0], 0)

    def test_earth_occultation(self):
        from lunar_core.geometry import body_vectors, satellite_earth_arrays, Body
        o = orbit()
        times = [EPOCH + timedelta(minutes=2 * i) for i in range(120)]
        distance, occulted = satellite_earth_arrays(o, times)
        self.assertTrue(occulted.any() and (~occulted).any())
        # Range is about the Earth-Moon distance, within one orbit radius.
        centre = np.linalg.norm(body_vectors(Body.EARTH, tuple(times)), axis=1)
        self.assertLess(np.max(np.abs(distance - centre)), o.semi_major_axis_km + 1)


if __name__ == "__main__":
    unittest.main()
