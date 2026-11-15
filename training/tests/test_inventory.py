"""Independent checks of inventory geometry, masks, PDS semantics and archives."""

import io
import json
from pathlib import Path
import tarfile

import numpy as np
import pytest
from pyproj import CRS
import rasterio
from rasterio.transform import from_origin

from moonmesh_train.inventory import (band_stats, clip_rectangle, compare_dem, discover,
                                      grid_overlap, haworth_point, parse_pds, polygon_area,
                                      candidate_inventory, summarize_json)

MOON = "+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +R=1737400 +units=m"


def raster(path, data, transform=None, nodata=-999):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1],
                       count=1, dtype=data.dtype, crs=MOON,
                       transform=transform or from_origin(0, 40, 20, 20), nodata=nodata) as out:
        out.write(data, 1)
    return path


def test_statistics_nodata_and_unmasked_floor(tmp_path):
    data = np.array([[-999, -300, -300], [-100, -50, np.nan]], dtype="float32")
    with rasterio.open(raster(tmp_path/"a.tif", data)) as src:
        stats = band_stats(src, 1)
    assert stats["nodata_count"] == 1
    assert stats["nonfinite_non_nodata_count"] == 1
    assert stats["valid_count"] == 4
    assert stats["min"] == -300
    assert stats["minimum_count"] == 2
    assert stats["mean"] == -187.5
    assert len(stats["histogram"]["counts"]) == 20
    assert sum(stats["histogram"]["counts"]) == 4


def test_explicit_mask_cannot_reintroduce_nodata_into_histogram(tmp_path):
    p = raster(tmp_path/"mask.tif", np.array([[-999, 2], [4, 6]], dtype="float32"))
    with rasterio.open(p, "r+") as out:
        out.write_mask(np.array([[255, 255], [255, 0]], dtype="uint8"))
    with rasterio.open(p) as src:
        result = band_stats(src, 1)
    assert result["nodata_count"] == 1
    assert result["other_masked_count"] == 1
    assert result["valid_count"] == 2
    assert sum(result["histogram"]["counts"]) == 2
    assert result["mean"] == 3


def test_projection_geometry_is_not_datum_equivalence(tmp_path):
    p = raster(tmp_path/"a.tif", np.ones((2, 2), dtype="float32"))
    q = raster(tmp_path/"b.tif", np.ones((2, 2), dtype="float32"))
    with rasterio.open(p, "r+") as changed:
        changed.crs = CRS.from_authority("IAU_2015", "30135")
    with rasterio.open(q, "r+") as changed:
        changed.crs = rasterio.crs.CRS.from_wkt(changed.crs.to_wkt().replace('DATUM["unknown"', 'DATUM["Moon_DE421"').replace('SPHEROID["unknown"', 'SPHEROID["Moon_DE421"'))
    with rasterio.open(p) as a, rasterio.open(q) as b:
        result = grid_overlap(b, a)
    assert result["geometrically_aligned_20m_grid"]
    assert not result["crs_equals_including_datum_names"]
    assert not result["exact_20m_grid"]


@pytest.mark.parametrize("value", [-999., np.nan])
def test_all_nodata(tmp_path, value):
    with rasterio.open(raster(tmp_path/"a.tif", np.full((2, 2), value, dtype="float32"), nodata=value)) as src:
        stats = band_stats(src, 1)
    assert stats["nodata_count"] == 4
    assert stats["mean"] is None
    assert stats["histogram"]["edges"] is None


def test_overlap_denominator_grid_and_moon_coordinates(tmp_path):
    data = np.ones((2, 2), dtype="float32")
    ref = raster(tmp_path/"ref.tif", data)
    big = raster(tmp_path/"big.tif", np.ones((4, 4), dtype="float32"), from_origin(0, 40, 20, 20))
    shifted = raster(tmp_path/"shift.tif", data, from_origin(10, 40, 20, 20))
    with rasterio.open(ref) as r, rasterio.open(big) as b, rasterio.open(shifted) as s:
        assert grid_overlap(b, r)["percent_of_source_footprint"] == pytest.approx(25)
        assert grid_overlap(b, r)["exact_20m_grid"]
        assert not grid_overlap(s, r)["exact_20m_grid"]
        assert grid_overlap(s, r)["fractional_offset_reference_pixels"][0] == .5
        point = haworth_point(r, 337.2, -86.8)
        assert point["x"] == pytest.approx(-37612.727, abs=1)
        assert not point["inside"]
    assert polygon_area(clip_rectangle([(0, 0), (4, 0), (4, 4), (0, 4)], [2, 2, 5, 5])) == 4


def test_pds_label_keeps_radius_offset_distinct():
    parsed = parse_pds('SCALING_FACTOR = 0.5\nOFFSET = 1737400.\n/* HEIGHT = (DN * SCALING_FACTOR). */')
    assert parsed["fields"]["OFFSET"][0] == {"key": "OFFSET", "value": "1737400.", "line": 2}
    assert parsed["fields"]["MISSING_CONSTANT"] is None
    assert parsed["conversion_lines"][0]["line"] == 3


def test_dem_exact_pixels_and_fractional_rejection(tmp_path):
    a = raster(tmp_path/"a.tif", np.array([[1, 2], [3, -999]], dtype="float32"))
    b = raster(tmp_path/"b.tif", np.zeros((4, 4), dtype="float32"), from_origin(-20, 60, 20, 20))
    result = compare_dem(a, b)
    assert result["stats"]["count"] == 3
    assert result["stats"]["mean"] == 2
    c = raster(tmp_path/"c.tif", np.zeros((2, 2), dtype="float32"), from_origin(10, 40, 20, 20))
    assert compare_dem(a, c)["stats"] is None


def test_archive_discovery_without_extraction(tmp_path):
    raw = tmp_path/"raw"
    raw.mkdir()
    p = raw/"test.tar"
    with tarfile.open(p, "w") as archive:
        info = tarfile.TarInfo("bundle/terrain.tif")
        info.size = 3
        archive.addfile(info, io.BytesIO(b"abc"))
    files, sources, archives, _ = discover(tmp_path, tmp_path/"absent.json")
    assert len(files) == len(sources) == len(archives) == 1
    assert sources[0]["open_path"].startswith("/vsitar/")
    assert not (raw/"bundle").exists()


def test_candidate_range_is_distance_to_rectangle_not_center(tmp_path):
    p = raster(tmp_path/"ref.tif", np.ones((2, 2), dtype="float32"))
    name = "interim/lunarrf/meta/data/atlas_base/candidates.csv"
    catalog = tmp_path/name
    catalog.parent.mkdir(parents=True)
    catalog.write_text("cand_id,x_m,y_m\n0,45,45\n1,46,46\n")
    rows = [{"cand_id": "0", "x_m": "45", "y_m": "45"}, {"cand_id": "1", "x_m": "46", "y_m": "46"}]
    docs = {name: rows, "interim/lunarrf/meta/atlas_v2/manifest.json": {"done": {"0": {}, "1": {}}},
            "interim/lunarrf/meta/atlas_v2/bundle/radio_params.json": {"max_radius_m": 8}}
    with rasterio.open(p) as ref:
        result = candidate_inventory(tmp_path, docs, ref)[0]
    assert result["within_range_ids"] == ["0"]
    assert result["matches_manifest_done_ids"]


def test_manifest_summary_is_bounded():
    summary = summarize_json({str(i): {"data": list(range(1000))} for i in range(11827)})
    assert summary["count"] == 11827
    assert len(json.dumps(summary)) < 1000
