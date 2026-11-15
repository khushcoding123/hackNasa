from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

import numpy as np
from pydantic import ValidationError

from lunar_core.geometry import Body, SurfacePoint, horizontal_arrays
from lunar_core.horizon import horizon_mask
from lunar_core.kernels import load_kernels
from lunar_core.terrain import MOON_RADIUS_M, TerrainGrid, polar_stereographic_south, polar_stereographic_south_inverse
from moonmesh.data import DATA
from moonmesh.models import Mission, Radio
from moonmesh.network import battery, evaluate, link_margin, metrics, optimize
from moonmesh.science import scenario


class TerrainTests(unittest.TestCase):
    def test_projection_round_trip(self):
        lat, lon = polar_stereographic_south_inverse(*polar_stereographic_south(-87.2, 337))
        self.assertAlmostEqual(lat, -87.2)
        self.assertAlmostEqual(lon, 337)

    def test_curvature_and_missing_bins(self):
        pixel = 200
        grid = TerrainGrid(np.zeros((1600, 1600)), -160000, 160000, pixel)
        mask = horizon_mask(-90, 0, grid, grid, observer_height_m=10)
        expected = -np.degrees(np.arccos(MOON_RADIUS_M/(MOON_RADIUS_M+10)))
        np.testing.assert_allclose(mask.elevation_deg, expected, atol=0.001)
        grid.heights[650:675, 790:810] = np.nan
        partial = horizon_mask(-90, 0, grid, grid, observer_height_m=10)
        self.assertTrue(np.isnan(partial.at(0)))
        self.assertTrue(np.isfinite(partial.at(180)))

    def test_ridge_changes_north_horizon(self):
        grid = TerrainGrid(np.zeros((1600, 1600)), -160000, 160000, 200)
        grid.heights[775:780, 795:805] = 900
        mask = horizon_mask(-90, 0, grid, grid)
        self.assertGreater(mask.at(0), 10)
        self.assertLess(mask.at(180), 0)


class NetworkTests(unittest.TestCase):
    def test_fspl_and_rate(self):
        radio = Radio(ghz=1, watts=1, tx_gain=0, rx_gain=0, sensitivity=-100, loss=0, margin=3)
        self.assertAlmostEqual(link_margin(radio, 1), 37.55)
        self.assertAlmostEqual(link_margin(radio.model_copy(update={"rate_kbps": 1280}), 1), 27.55)

    def test_outage_durations(self):
        self.assertEqual(metrics([False, False, True, False], .5),
                         dict(availability=25, longest=1, total=1.5, intervals=2, average=.75))

    def test_battery_initial_state_and_recharge(self):
        powered, reserve = battery([False, True, False, False, False], 3, 2, 1)
        self.assertEqual(powered.tolist(), [False, True, True, True, False])
        self.assertEqual(reserve, [0, 2, 1, 0, 0])
        self.assertEqual(battery([False, False], 2, 1, 1, 50)[0].tolist(), [True, False])

    def test_minimum_configuration_and_failure(self):
        direct = np.array([True, False, False, True])
        relay = np.array([False, True, True, False])
        relays = [relay, relay]
        mission = Mission(availability=100, blackout_hours=0)
        self.assertEqual(optimize(direct, relays, [0, 1], mission), ((0,), True))
        self.assertEqual(evaluate(direct, relays, (0,), mission)[2]["availability"], 50)
        self.assertEqual(optimize(direct, relays, [0, 1], mission.model_copy(update={"resilient": True})), ((0, 1), True))
        self.assertEqual(optimize(direct, relays, [], mission), ((), False))

    def test_time_sampling_convergence(self):
        def coverage(step):
            t = np.arange(0, 10, step)
            return metrics(t >= 2.25, step)["availability"]
        self.assertLess(abs(coverage(.25)-77.5), abs(coverage(1)-77.5))


class DataTests(unittest.TestCase):
    def test_validation(self):
        for kwargs in (dict(start="2026-12-02"), dict(latitude=-65, longitude=20), dict(latitude=-87),
                       dict(earth_radio={"ghz": 0}), dict(minutes=17)):
            with self.assertRaises(ValidationError):
                Mission(**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FileNotFoundError, "Missing SPICE kernel"):
                load_kernels(Path(directory))

    @unittest.skipUnless((DATA/"terrain/haworth_near20km.npz").exists(), "NASA datasets are not prepared")
    def test_real_scenario(self):
        mission = Mission(avoid_hazard_routes=False)   # the default also excludes steep traverses; see test_diagnostics
        result = scenario(mission)
        self.assertEqual(len(result["times"]), 1440)
        self.assertEqual(len(result["candidates"]), 15)
        self.assertGreater(result["network"]["availability"], result["direct"]["availability"])
        self.assertLessEqual(result["worst"]["availability"], result["network"]["availability"])
        self.assertEqual(result["failure_states"]["nominal"]["metrics"], result["network"])
        with self.assertRaisesRegex(ValueError, "outside"):
            scenario(Mission(latitude=-85.1, longitude=179))

    @unittest.skipUnless((DATA/"kernels/de440s.bsp").exists(), "NAIF kernels are not prepared")
    def test_time_dependent_topocentric_geometry(self):
        load_kernels(DATA/"kernels")
        start = datetime(2026, 11, 1, tzinfo=UTC)
        times = (start, start+timedelta(days=7))
        point = SurfacePoint(-86.8, 337.2, 2)
        # JPL Horizons observer ephemeris at 2026-11-01 00:00 UTC.
        # Horizons reports apparent DE441 angles. Allow for geometric DE440.
        references = [(Body.EARTH, 26.874023, 5.796654, .00247194522967),
                      (Body.SUN, 307.823111, 3.421569, .99316254636077)]
        for body, reference_az, reference_el, reference_au in references:
            azimuth, elevation, distance = horizontal_arrays(body, point, times)
            self.assertLess(abs(azimuth[0] - reference_az), .02)
            self.assertLess(abs(elevation[0] - reference_el), .02)
            self.assertLess(abs(distance[0] / 149597870.7 / reference_au - 1), .0001)
            self.assertNotAlmostEqual(elevation[0], elevation[1])
