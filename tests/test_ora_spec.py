"""Tests for the spec additions: topologies, loaders, export, DTE map, twin extras."""
import gzip
import json

import numpy as np
import pytest

from ora import loaders
from ora.export import line_chart_svg, to_geojson
from ora.topologies import table


def test_topologies_ring_has_no_single_point_of_failure():
    rows = {r["topology"]: r for r in table()}
    assert rows["chain"]["articulation_points"] > 0
    assert rows["ring"]["articulation_points"] == 0
    assert rows["chain+skip"]["reliability"]["0.1"] > rows["chain"]["reliability"]["0.1"]


def test_hgt_loader_resamples_to_metric_pixels(tmp_path):
    raw = (np.arange(121, dtype=">i2").reshape(11, 11) * 10).astype(">i2")
    path = tmp_path / "N47W122.hgt.gz"
    with gzip.open(path, "wb") as f:
        f.write(raw.tobytes())
    dem, px = loaders.load_dem(path, pixel_m=1000.0)
    assert px == 1000.0 and dem.ndim == 2 and np.nanmax(dem) <= 1200
    assert loaders.describe(path)["pixel_m"] == 30.0


def test_loader_rejects_unknown_format(tmp_path):
    with pytest.raises(ValueError):
        loaders.load_dem(tmp_path / "x.xyz")


def test_img_needs_label(tmp_path):
    (tmp_path / "a.IMG").write_bytes(b"\0" * 8)
    with pytest.raises(FileNotFoundError):
        loaders.load_dem(tmp_path / "a.IMG")


def test_line_chart_svg_is_wellformed():
    svg = line_chart_svg([dict(label="a", color="#000", x=[0, 1], y=[1, 0], band=[0.1, 0.1])], "t", "x", "y")
    assert svg.startswith("<svg") and svg.endswith("</svg>") and "polygon" in svg


def test_geojson_roundtrip_and_roles():
    from ora.scenario import build
    data = to_geojson(build("shackleton_rim_to_floor"))
    json.dumps(data)
    roles = {f["properties"].get("role") for f in data["features"] if f["geometry"]["type"] == "Point"}
    assert {"relay", "gateway", "mine"} <= roles
    assert "crs_note" in data


def test_twin_blast_and_dust_are_reported():
    from ora import twin
    from ora.scenario import build
    n = build("shackleton_rim_to_floor")
    r = twin.run(n, blast=(n["demand"][0], 100.0, 300.0, 6000.0), dust=dict(rate_per_day=0.02, base_hazard_per_h=2e-4, seed=1))
    assert "blast" in r and "dust_failures" in r and "module_swaps" in r


def test_dte_map_terrain_never_exceeds_flat_by_much():
    from ora.earth_vis import dte_map
    d = dte_map()
    inside = np.isfinite(d["terrain"])
    assert d["terrain"][inside].mean() < d["flat"][inside].mean()
