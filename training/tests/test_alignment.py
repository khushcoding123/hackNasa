"""Physical averaging, metre support, exact lattices and registration gates."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from scipy.ndimage import fourier_shift, gaussian_filter

from moonmesh_train.alignment import (dbm_to_mw, mw_to_dbm, mean_power_dbm, disk_kernel,
    neighborhood_moments, slope_curvature, make_grid, place_lunar, registration,
    height_rule, hillshade, warp_dem, derive_features)
from rasterio.enums import Resampling

MOON = "+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +R=1737400 +units=m"


def test_power_average_is_linear_and_masks_missing():
    assert mean_power_dbm([-100, -80]) == pytest.approx(-82.967086, abs=1e-6)
    assert mean_power_dbm([-100, np.nan]) == pytest.approx(-100)
    assert np.isnan(mean_power_dbm([np.nan, np.nan]))
    np.testing.assert_allclose(mw_to_dbm(dbm_to_mw([-177, -94.4, 30])), [-177, -94.4, 30])
    np.testing.assert_allclose(mean_power_dbm([[-100, -80], [-90, -90]], axis=1), [-82.967086, -90], atol=1e-6)


def test_metres_disk_support_is_resolution_independent():
    k20 = disk_kernel(100, (20, 20))
    k5 = disk_kernel(100, (5, 5))
    assert k20.shape == (11, 11) and k5.shape == (41, 41)
    np.testing.assert_array_equal(k5[::4, ::4], k20)
    assert k20[0, 5] == 1 and k20[0, 0] == 0
    anisotropic = disk_kernel(100, (20, 50))
    assert anisotropic.shape == (5, 11)
    with pytest.raises(ValueError):
        disk_kernel(100, (0, 20))


def test_neighborhood_masks_and_physical_radius():
    z = np.full((31, 31), 1737400., dtype=float)
    mean = neighborhood_moments(z, 100, (20, 20))
    std = neighborhood_moments(z, 100, (20, 20), std=True)
    assert mean[15, 15] == 1737400
    assert std[15, 15] == 0
    assert np.isnan(mean[:5]).all()
    z[15, 15] = np.nan
    mean = neighborhood_moments(z, 100, (20, 20))
    assert np.isnan(mean[15, 20])  # invalid value exactly 100 m away
    assert np.isfinite(mean[15, 21])  # 120 m away is outside disk


def test_slope_and_curvature_on_analytic_surfaces():
    y, x = np.mgrid[-5:6, -5:6]*20.
    slope, curve = slope_curvature(.1*x+.2*y, (20, 20))
    assert slope[5, 5] == pytest.approx(np.degrees(np.arctan(np.sqrt(.05))), abs=1e-6)
    assert curve[5, 5] == pytest.approx(0)
    _, curve = slope_curvature(.001*(x*x+y*y), (20, 20))
    assert curve[5, 5] == pytest.approx(.004)
    assert np.isnan(slope[0]).all()


def create_raster(path, data, transform, nodata=np.nan):
    with rasterio.open(path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1,
                       dtype="float32", crs=MOON, transform=transform, nodata=nodata) as dst:
        dst.write(data.astype(np.float32), 1)
    return path


def test_master_lattice_expands_outward_and_lunar_copy_is_exact(tmp_path):
    l = create_raster(tmp_path/"Haworth__best_prx_1p800GHz.tif", np.array([[-100, -80], [-999, -70]]), from_origin(0, 40, 20, 20), -999)
    p = create_raster(tmp_path/"pgda.tif", np.ones((4, 4)), from_origin(-21, 61, 20, 20))
    with rasterio.open(l) as lunar, rasterio.open(p) as pgda:
        grid = make_grid(lunar, pgda)
    assert grid["transform"] == [20, 0, -40, 0, -20, 80]
    assert grid["shape"] == [5, 5]
    result, method = place_lunar(l, grid)
    assert result[2, 2] == -100 and result[2, 3] == -80
    assert np.isnan(result[3, 2]) and np.isnan(result[0, 0])
    assert "no resampling" in method


def test_power_downsample_uses_mw(tmp_path):
    p = create_raster(tmp_path/"best_prx.tif", np.array([[-100, -80], [-100, -80]]), from_origin(0, 20, 10, 10))
    grid = {"shape": [1, 1], "height": 1, "width": 1, "transform": [20, 0, 0, 0, -20, 20], "crs_wkt": MOON}
    result, method = place_lunar(p, grid)
    assert result[0, 0] == pytest.approx(-82.967086, abs=1e-5)
    assert "linear mW" in method


def test_registration_detects_half_pixel_failure():
    rng = np.random.default_rng(42)
    a = gaussian_filter(rng.normal(size=(128, 128)), 2).astype(np.float32)
    b = np.fft.ifftn(fourier_shift(np.fft.fftn(a), [.6, -.8])).real.astype(np.float32)
    result = registration(a, b, (20, 20))
    np.testing.assert_allclose(result["shift_to_apply_row_col_px"], [-.6, .8], atol=.1)
    assert result["status"] == "FAIL"
    assert registration(a, a, (20, 20))["status"] == "PASS"
    with pytest.raises(ValueError, match="featureless"):
        registration(np.ones((32, 32)), np.ones((32, 32)), (20, 20))


def test_pds_radius_offset_is_not_added_to_height(tmp_path):
    p = tmp_path/"dem.LBL"
    p.write_text('SCALING_FACTOR = 0.5\nOFFSET = 1737400.\n/* HEIGHT = (DN * SCALING_FACTOR). */')
    from types import SimpleNamespace
    source = SimpleNamespace(scales=(.5,), offsets=(1737400.,), name=str(p))
    rule = height_rule(source, "pds20", 1737400)
    assert rule["output_scale"] == .5 and rule["output_offset"] == 0
    assert -2000*rule["output_scale"]+rule["output_offset"] == -1000


def test_hillshade_flat_surface():
    shade = hillshade(np.zeros((20, 20)), (20, 20), altitude=45)
    np.testing.assert_allclose(shade, np.sin(np.pi/4), atol=1e-6)


def test_average_preserves_footprint_and_native_block_values(tmp_path):
    native = np.arange(64, dtype=np.float32).reshape(8, 8)
    p = create_raster(tmp_path/"surface.tif", native, from_origin(20, 60, 5, 5))
    grid = {"shape": [4, 4], "height": 4, "width": 4, "transform": [20, 0, 0, 0, -20, 80], "crs_wkt": MOON}
    aligned, rule = warp_dem(p, grid, Resampling.average, "pgda78", 1737400)
    assert np.isfinite(aligned).sum() == 4
    np.testing.assert_array_equal(aligned[1:3, 1:3], native.reshape(2, 4, 2, 4).mean(axis=(1, 3)))
    assert "exact 4x4" in rule["implementation"]


def test_feature_tiles_have_no_seams(tmp_path):
    y, x = np.mgrid[:96, :96]*20.
    z = (100+np.sin(x/100)*2+np.cos(y/200)*3).astype(np.float32)
    p = create_raster(tmp_path/"dem.tif", z, from_origin(0, 1920, 20, 20))
    grid = {"shape": [96, 96], "height": 96, "width": 96, "pixel_size_m": [20, 20],
            "transform": [20, 0, 0, 0, -20, 1920], "crs_wkt": MOON}
    outputs = derive_features(p, tmp_path/"features", grid,
                              {"relative_elevation_radii_m": [200, 400], "roughness_radius_m": 100,
                               "processing_tile_pixels": 32}, "test")
    expected = z-neighborhood_moments(z, 200, (20, 20))
    with rasterio.open(outputs["relative_elevation_200m"]) as result:
        np.testing.assert_allclose(result.read(1), expected, atol=1e-5, equal_nan=True)
