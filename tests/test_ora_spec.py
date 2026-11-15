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


def test_horizon_features_shape_and_enclosure():
    from ora.features import HORIZON, horizon_features
    dem = np.zeros((300, 300), np.float32)
    dem[:, 150:] = 200.0  # a wall to the east of cell (100, 150)
    h = horizon_features(dem, 20.0, [(100, 150)], radius_m=3000)
    assert h.shape == (1, len(HORIZON))
    assert h[0, HORIZON.index("horizon_max")] > 0 and h[0, HORIZON.index("horizon_open")] < 1


def test_spaced_top_respects_spacing():
    from ora.benchmark import SPACING_M, spaced_top
    cells = np.array([[0, 0], [1, 0], [200, 0], [400, 0]], float)
    chosen = spaced_top(np.array([4, 3, 2, 1.0]), cells, 10.0, 3)
    assert chosen[0] == 0 and 1 not in chosen
    assert all(np.hypot(*(cells[a] - cells[b])) * 10.0 >= SPACING_M for a in chosen for b in chosen if a != b)


def test_greedy_union_never_worse_than_first_pick():
    from ora.benchmark import greedy_union, share
    rng = np.random.default_rng(1)
    m = rng.random((30, 200)) < 0.15
    assert share(m, greedy_union(m, 5)) >= share(m, [int(m.sum(axis=1).argmax())])


def test_parts_flag_placeholder_values():
    from ora.configure import catalog
    assert all(entry.get("assumed") for entry in catalog().values())


def test_tall_towers_taller_sees_more():
    from ora.tall_towers import reproduce
    r = reproduce()["ours"]
    assert r["best_50m"] >= r["best_10m"] and r["window_centre_50m"] >= r["window_centre_10m"]


def test_model_card_has_improved_surrogate_and_importance():
    import json
    from pathlib import Path
    card = json.loads((Path(__file__).parents[1] / "ora" / "model_card.json").read_text())
    assert card["coverage_surrogate"]["mean_spearman"] > 0.7
    assert "viewshed" in card["siting_prior"]["auc_lost_when_feature_shuffled"]


def test_fit_uwb_recovers_known_waveguide_loss():
    from ora.fit_uwb import fit_alpha, fspl_db
    d = np.arange(3, 41, dtype=float)
    loss = 10.0 + fspl_db(d, 4.0) + 9.0 * d / 100
    alpha, offset = fit_alpha(d, loss, 4.0, +1)
    assert abs(alpha - 9.0) < 1e-6 and abs(offset - 10.0) < 1e-6
    rss = -10.0 - fspl_db(d, 4.0) - 9.0 * d / 100
    assert abs(fit_alpha(d, rss, 4.0, -1)[0] - 9.0) < 1e-6


def test_lunar_split_is_sealed_and_buffered():
    from ora.lunar_windows import SPACING_M, split
    train, test = split()
    assert len(train) >= 60 and len(test) >= 12
    assert not set(train) & set(test)
    for tx, ty in test:  # no training window touches a test window, diagonals included
        assert all(max(abs(x - tx), abs(y - ty)) > SPACING_M * 1.01 for x, y in train)


def test_ring_features_end_at_the_exact_viewshed():
    from ora.data import terrain
    from ora.features import interior_cells, view_rings
    from ora.los import viewshed_fraction
    g = terrain("haworth")
    cells = interior_cells(g.heights, g.pixel_size_m, 3, np.random.default_rng(0))
    assert np.allclose(view_rings(g.heights, g.pixel_size_m, cells)[:, -1], viewshed_fraction(g.heights, g.pixel_size_m, cells, 10.0, 2.0, 8000)[0], atol=1e-5)


def test_gain_features_shrink_near_chosen_sites():
    from ora.selector import gain_features
    cells = np.array([[0, 0], [1, 0], [400, 400]], float)
    base = np.zeros((3, 4), np.float32)
    f = gain_features(base, cells, [0], 1)
    assert f[1, -4] < f[2, -4]  # nearest-chosen distance column: neighbour closer than the far cell


def test_stress_verdict_logic():
    from ora.stress import CRITERIA, verdict
    assert verdict("A_r2_pct", 70)["passed"] and not verdict("A_r2_pct", 50)["passed"]
    assert verdict("B_calibration_slope", 1.0)["passed"] and not verdict("B_calibration_slope", 1.4)["passed"]
    assert all(op in (">=", "<=", "between") for op, _ in CRITERIA.values())


def test_app_scorer_matches_prior_width():
    import joblib
    from ora.train import MODELS
    from ora.features import PRIOR_FEATURES
    assert joblib.load(MODELS / "ora_siting_prior.joblib").n_features_in_ == len(PRIOR_FEATURES)



def square(half=3000.0, lat=-89.67, lon=129.78):
    from lunar_core.terrain import polar_stereographic_south as forward
    from lunar_core.terrain import polar_stereographic_south_inverse as inverse
    x, y = forward(lat, lon)
    return [tuple(float(v) for v in inverse(x + dx, y + dy)) for dx, dy in ((-half, -half), (half, -half), (half, half), (-half, half))]


def test_drawn_area_info_reports_size_and_centre():
    from ora.area import info
    meta = info(square())
    assert meta["area_km2"] == pytest.approx(36.0, abs=0.1)
    assert meta["lat"] == pytest.approx(-89.67, abs=0.01)


def test_drawn_area_rejects_only_impossible_shapes():
    from ora.area import AreaError, info
    for bad in ([(-89.0, 0.0), (-89.1, 10.0)],                       # too few corners
                square(half=20.0)):                                  # a few metres across
        with pytest.raises(AreaError):
            info(bad)


def test_local_projection_round_trips_and_is_centred():
    from lunar_core.terrain import local_frame, set_frame
    from lunar_core.terrain import polar_stereographic_south as forward
    from lunar_core.terrain import polar_stereographic_south_inverse as inverse
    try:
        for lat, lon in ((0.7, 23.5), (-43.0, 349.0), (60.0, 100.0)):
            set_frame(local_frame(lat, lon))
            assert np.hypot(*forward(lat, lon)) < 0.5   # metres
            x, y = forward(lat + 0.4, lon + 0.5)
            back = inverse(x, y)
            assert float(back[0]) == pytest.approx(lat + 0.4, abs=1e-9) and float(back[1]) == pytest.approx((lon + 0.5) % 360, abs=1e-9)
    finally:
        set_frame(None)


def test_drawn_area_outside_the_polar_cap_uses_global_terrain():
    from lunar_core.terrain import set_frame
    from moonmesh import globaldem
    from ora.area import info
    if not globaldem.available():
        pytest.skip("ldem_64.img not installed")
    try:
        meta = info([(0.5, 23.0), (0.5, 24.0), (-0.5, 24.0), (-0.5, 23.0)])
    finally:
        set_frame(None)
    assert meta["projection"] == "local" and meta["native_m"] == pytest.approx(473.8, abs=0.5)
    assert 2 * meta["extent_m"] / meta["resolution_m"] <= 2101


def test_drawn_area_accepts_any_size_and_thins_terrain():
    from ora.area import MAX_GRID_PX, info
    small, big = info(square(half=250.0)), info(square(half=3000.0))
    large, huge = info(square(half=50_000.0, lat=-88.5, lon=100.0)), info(square(half=100_000.0, lat=-83.0, lon=200.0))
    assert small["resolution_m"] == big["resolution_m"] == 20.0
    assert large["resolution_m"] > 20.0 and huge["product"] == "LDEM_75S_240M"
    for meta in (small, big, large, huge):
        assert 2 * meta["extent_m"] / meta["resolution_m"] <= MAX_GRID_PX + 1


def test_polygon_mask_covers_the_shape():
    from lunar_core.terrain import TerrainGrid
    from lunar_core.terrain import polar_stereographic_south as forward
    from ora.area import polygon_mask
    x, y = forward(-89.67, 129.78)
    grid = TerrainGrid(np.zeros((1000, 1000), np.float32), x - 10_000, y + 10_000, 20.0)
    mask = polygon_mask(grid, square(half=3000.0))
    assert mask.sum() * 20.0 ** 2 / 1e6 == pytest.approx(36.0, abs=1.0)
    assert mask[500, 500] and not mask[0, 0]


def test_loop_layout_removes_single_path_links():
    import networkx as nx
    from ora.area import loop_layout
    H = nx.cycle_graph(6)
    for a, b in H.edges:
        H.edges[a, b]["dist"] = 1.0
    tree = nx.minimum_spanning_tree(H, weight="dist")
    assert len(list(nx.bridges(tree))) == 5
    assert len(list(nx.bridges(loop_layout(H, tree)))) == 0


def test_analyse_compares_three_layouts():
    from ora.api import NetworkBody, analyse_area
    out = analyse_area(NetworkBody(polygon=square()))
    assert set(out["layouts"]) == {"tree", "loop", "mesh"}
    assert out["recommended"] in out["layouts"]
    assert out["layouts"]["tree"]["links"] <= out["layouts"]["mesh"]["links"]
    assert {"gateway", "mine"} <= {n["role"] for n in out["nodes"]}


def test_mission_and_site_key_requests_work_for_a_non_polar_area():
    from lunar_core.terrain import set_frame
    from moonmesh import data as moon_data
    from moonmesh import globaldem
    from moonmesh.models import Mission
    from moonmesh.science import scenario
    from ora.api import NetworkBody, network_for
    from ora.area import register
    if not globaldem.available():
        pytest.skip("ldem_64.img not installed")
    try:
        key, _, _ = register([(0.4, 23.0), (0.4, 23.5), (-0.1, 23.5), (-0.1, 23.0)])
        moon_data.SITES.pop(key)
        moon_data.LOCAL.discard(key)
        set_frame(None)
        assert scenario(Mission(site=key, start="2026-03-01", end="2026-03-02", minutes=60), include_map=False)["site"]["name"]
        set_frame(None)
        assert network_for(NetworkBody(site=key, radius_km=11.0))["n_relays"] > 0
    finally:
        set_frame(None)
