"""Terrain-tile endpoint: units, tile boundaries, source limits and missing-data fallback."""
import unittest
from unittest.mock import patch

import numpy as np

from moonmesh import tiles


class TileTests(unittest.TestCase):
    def test_shape_padding_and_units(self):
        data = tiles.tile(-180, -90, 0, 90, 33)
        self.assertEqual(data["heights"].shape, (35, 35))
        self.assertEqual(data["heights"].dtype, np.dtype("<f4"))
        # Metres relative to the 1,737.4 km sphere: the Moon spans roughly -9 km to +11 km.
        self.assertGreater(data["minimum"], -9500)
        self.assertLess(data["maximum"], 11000)
        self.assertTrue(np.isfinite(data["heights"]).all())

    def test_adjacent_tiles_share_edges_and_padding(self):
        west = tiles.tile(-10, -60, 0, -50, 17)["heights"]
        east = tiles.tile(0, -60, 10, -50, 17)["heights"]
        np.testing.assert_array_equal(west[:, 17], east[:, 1])      # shared edge column
        np.testing.assert_array_equal(west[:, 18], east[:, 2])      # padding equals neighbour interior

    def test_longitude_seam_is_continuous(self):
        west = tiles.tile(-180, -10, -90, 10, 17)["heights"]
        east = tiles.tile(90, -10, 180, 10, 17)["heights"]
        np.testing.assert_allclose(east[1:-1, 17], west[1:-1, 1], atol=1e-3)   # +180 and -180 are one meridian

    def test_poles_have_one_height_for_every_longitude(self):
        south = tiles.tile(-180, -90, -90, -80, 9)["heights"]
        self.assertEqual(float(np.ptp(south[-2, 1:-1])), 0.0)

    def test_regional_dem_is_used_only_where_it_exists(self):
        regional = tiles.tile(-23.0, -86.85, -22.6, -86.75, 33)
        self.assertEqual(regional["resolution_m"], 20.0)
        self.assertEqual(regional["sources"], ["near20km"])
        equator = tiles.tile(0, 0, 1, 1, 17)
        self.assertEqual(equator["sources"], ["global"])
        self.assertGreater(equator["resolution_m"], 1500)

    def test_missing_regional_data_falls_back_to_global(self):
        with patch.object(tiles, "region_grid", return_value=None):
            data = tiles.tile(-23.0, -86.85, -22.6, -86.75, 17)
        self.assertEqual(data["sources"], ["global"])
        self.assertTrue(np.isfinite(data["heights"]).all())

    def test_nodata_inside_a_region_is_not_returned(self):
        grid = tiles.region_grid("haworth", "near20km")
        broken = type(grid)(np.full_like(grid.heights, np.nan), grid.x_left, grid.y_top, grid.pixel_size_m)
        with patch.object(tiles, "region_grid", side_effect=lambda site, suffix: broken if suffix == "near20km" else None):
            data = tiles.tile(-23.0, -86.85, -22.6, -86.75, 9)
        self.assertTrue(np.isfinite(data["heights"]).all())
        self.assertNotIn("near20km", data["sources"])

    def test_levels_stop_at_source_resolution(self):
        sources = tiles.info()["sources"]
        self.assertEqual(sources["global"]["max_level"], 7)
        self.assertEqual(sources["far"]["max_level"], 10)
        self.assertEqual(sources["near"]["max_level"], 13)
        # At the deepest level the sample spacing is not much finer than the source.
        for key in sources.values():
            spacing = np.pi * tiles.MOON_RADIUS_M / 32 / 2 ** key["max_level"]
            self.assertGreaterEqual(spacing, key["resolution_m"] * 0.6)

    def test_invalid_requests_are_rejected(self):
        for rect in [(0, 0, 0, 10), (10, 0, 0, 10), (0, -91, 10, 0), (-181, 0, 0, 10), (0, 0, float("nan"), 10)]:
            with self.assertRaises(tiles.TileError):
                tiles.tile(*rect)
        with self.assertRaises(tiles.TileError):
            tiles.tile(0, 0, 1, 1, size=2)

    def test_site_patch_supports_any_location_and_seams(self):
        east = tiles.site_patch(12, 180, 175_000, 129)
        west = tiles.site_patch(12, -180, 175_000, 129)
        np.testing.assert_allclose(east["heights"], west["heights"], atol=1e-3)
        self.assertEqual(east["source"], "global")
        self.assertGreater(east["resolution_m"], 1000)
        pole = tiles.site_patch(-90, 0, 175_000, 129)
        self.assertTrue(np.isfinite(pole["heights"]).all())
        self.assertEqual(pole["heights"].shape, (129, 129))

    def test_site_patch_rejects_invalid_centres(self):
        for lat, lon in [(91, 0), (float("nan"), 0), (0, float("inf"))]:
            with self.assertRaises(tiles.TileError):
                tiles.site_patch(lat, lon, 7000, 129)

    def test_endpoint_headers(self):
        try:
            from fastapi.testclient import TestClient
            from moonmesh.server import app
        except (ImportError, RuntimeError):
            self.skipTest("Starlette's optional test client dependency is not installed")
        client = TestClient(app)
        response = client.get("/api/terrain/tile", params=dict(west=-23, south=-86.85, east=-22.6, north=-86.75, size=33))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.content), 35 * 35 * 4)
        self.assertEqual(response.headers["X-Terrain-Unit"], "m")
        self.assertEqual(response.headers["X-Terrain-Resolution-M"], "20.0")
        self.assertEqual(client.get("/api/terrain/tile", params=dict(west=1, south=0, east=0, north=1)).status_code, 422)
        self.assertEqual(client.get("/api/terrain/info").json()["unit"], "m")


if __name__ == "__main__":
    unittest.main()
