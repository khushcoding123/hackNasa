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
