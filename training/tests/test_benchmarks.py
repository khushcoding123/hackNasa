"""Prompt 12 benchmark helpers: projection, area scale, decimation, the configs table and the LOS code convention."""
import math

import numpy as np
from rasterio.transform import Affine

from lunar_core.terrain import polar_stereographic_south
from moonmesh_train import benchmarks as B


def test_polar_xy_matches_the_app_projection():
    for lat, lon in [(-89.468, 222.6), (-85.964, 357.681), (-86.8, 337.2)]:
        x, y = polar_stereographic_south(lat, lon)
        assert np.allclose(B.polar_xy(lat, lon), (float(x), float(y)), atol=1e-6)


def test_area_scale_is_one_at_the_pole_and_shrinks_outward():
    assert B.area_scale(np.array(0.), np.array(0.)) == 1
    assert 0.99 < B.area_scale(np.array(122_000.), np.array(0.)) < 1


def test_decimate_block_mean_keeps_nodata():
    dem = np.arange(16, dtype=float).reshape(4, 4)
    dem[0, 0] = np.nan
    out, tr = B.decimate(dem, Affine(20, 0, 0, 0, -20, 0), 2)
    assert out.shape == (2, 2) and tr.a == 40 and tr.e == -40
    assert np.isnan(out[0, 0]) and out[1, 1] == np.mean([10, 11, 14, 15])


def test_configs_markdown_lists_every_study_with_pages_and_not_stated():
    config = B.load_config()
    text = B.configs_markdown(config)
    for study in config["studies"].values():
        assert study["title"] in text
    assert "NOT STATED" in text and "| 8 |" in text
    assert {c["study"] for c in config["cases"]} <= set(config["studies"])


def test_visible_code_is_clear_line_of_sight_and_a_wall_blocks():
    # Flat patch 50 km from the pole with a 20 m wall 1 km east of the tower.
    px, n = 20.0, 301
    x0, y0 = -n * px / 2, -50_000 + n * px / 2
    transform = Affine(px, 0, x0, 0, -px, y0)
    dem = np.zeros((n, n))
    x, y = B.centres(dem, transform)
    tx = (0.0, -50_000.0)
    dem[(np.abs(x - 1000) < 30) & (np.abs(y - tx[1]) < 400)] = 20
    v = B.run_viewshed(tx, 10, 2, dem, transform, 2500)
    near_west = (np.abs(x + 500) < 15) & (np.abs(y - tx[1]) < 15)
    behind_wall = (np.abs(x - 1500) < 15) & (np.abs(y - tx[1]) < 15)
    assert (v["los"][near_west] == B.VISIBLE).all()
    assert (v["los"][behind_wall] == 0).all()
    assert math.isclose(float(B.area_scale(np.array(0.), np.array(-50_000.))), 1 / (1 + 50_000**2 / (4 * B.R**2))**2)
