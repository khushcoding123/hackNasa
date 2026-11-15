"""Thermal hazard dataset and model: parsing, spatial splits, leakage guards, and that the test blocks are never used."""
import sys
from pathlib import Path

import joblib
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "training" / "src")]

from moonmesh_train import hazard_dataset as hd  # noqa: E402
from moonmesh_train import hazard_model as hm  # noqa: E402
from ora import hazard  # noqa: E402

CONFIG = hd.load_config()
MANIFEST = '{"rows": {}, "registration_check": {}, "sources": {}}'


def prp_file(path, rows):
    """A PRP-shaped table: a 210-byte header record then fixed-width comma separated rows."""
    header = ("      tri1_x,       tri1_y,       tri1_z,       tri2_x,       tri2_y,       tri2_z,       tri3_x,       tri3_y,       tri3_z,"
              "        tri_clon,        tri_clat,        tri_calt, temp_avg, temp_max,   ice_depth")
    lines = [header.ljust(208) + "\r\n"]
    for lon, lat, alt, avg, tmax, ice in rows:
        nine = ",".join(f"{v:12.5f}" for v in (1, 2, 3, 4, 5, 6, 7, 8, 9)) + ","
        lines.append(f"{nine}{lon:16.9f},{lat:16.9f},{alt:16.9f},{avg:8.3f},{tmax:8.3f},{ice:12.5f}".ljust(208) + "\r\n")
    path.write_bytes("".join(lines).encode("ascii"))


def test_read_prp_parses_fixed_width_rows(tmp_path):
    path = tmp_path / "prp.tab"
    prp_file(path, [(-45.0, -85.5, 0.5, 162.9, 272.1, -999.0), (10.25, -88.0, -1.25, 50.5, 90.0, 0.0)])
    table = hd.read_prp(path, expected_bytes=path.stat().st_size)
    np.testing.assert_allclose(table["tri_clat"], [-85.5, -88.0])
    np.testing.assert_allclose(table["tri_clon"], [-45.0, 10.25])
    np.testing.assert_allclose(table["temp_max"], [272.1, 90.0])
    np.testing.assert_allclose(table["ice_depth"], [-999.0, 0.0])


def test_read_prp_rejects_truncated_download(tmp_path):
    path = tmp_path / "prp.tab"
    prp_file(path, [(0, -85, 0, 100, 200, 0)])
    with pytest.raises(ValueError, match="truncated"):
        hd.read_prp(path, expected_bytes=path.stat().st_size + 1)


def test_features_contain_no_label_or_simulation_outputs():
    forbidden = ("temp", "ice", "tri_", "cold_trap", "best_prx", "best_id", "best_los", "count", "min_clearance", "obstruction")
    assert not [name for name in hazard.FEATURES if any(word in name for word in forbidden)]
    assert "lon" not in " ".join(hazard.FEATURES)          # a place must not be memorizable from coordinates


def test_blocks_are_never_split_and_training_is_buffered():
    rng = np.random.default_rng(0)
    x, y = rng.uniform(-250e3, 250e3, 60_000), rng.uniform(-250e3, 250e3, 60_000)
    split, block = hd.assign_blocks(x, y, CONFIG, seed=7)
    for b in np.unique(block):
        kinds = set(split[block == b]) - {hd.SPLITS["buffer"]}
        assert len(kinds) <= 1                               # a block is wholly train, val or test (buffer aside)
    held = np.isin(split, (hd.SPLITS["val"], hd.SPLITS["test"]))
    train = split == hd.SPLITS["train"]
    nearest = np.array([np.min(np.hypot(x[held] - px, y[held] - py)) for px, py in zip(x[train][:300], y[train][:300])])
    assert nearest.min() >= CONFIG["split"]["buffer_km"] * 1000 - 1500    # 1 km raster resolution
    again, _ = hd.assign_blocks(x, y, CONFIG, seed=7)
    assert (split == again).all()                            # deterministic for a seed
    other, _ = hd.assign_blocks(x, y, CONFIG, seed=8)
    assert (split != other).any()


def test_flat_terrain_has_no_slope_and_a_wall_sets_the_horizon():
    heights = np.zeros((420, 420), dtype=np.float32)
    flat = hazard.feature_maps(heights)
    centre = (210, 210)
    assert flat["slope_deg"][centre] == pytest.approx(0, abs=1e-6)
    assert flat["horizon_max_deg"][centre] < 0.05            # only the curvature drop, no terrain above the horizontal
    heights[:, 230:] = 600.0                                 # a 600 m wall 20 cells (4.8 km) east of the centre
    walled = hazard.feature_maps(heights)
    expected = np.degrees(np.arctan((600 - (4800 ** 2) / (2 * hazard.MOON_RADIUS_M)) / 4800))
    assert walled["horizon_max_deg"][centre] == pytest.approx(expected, abs=1.0)
    assert walled["open_below_5deg"][centre] < 1.0 and walled["open_below_1deg"][centre] > 0.5


def tiny_dataset(path, rows=4000, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(rows, len(hazard.FEATURES))).astype(np.float32)
    X[:, 0] = rng.uniform(0, 300, rows)
    avg = 60 + 0.3 * X[:, 0] + 8 * X[:, 8] + rng.normal(0, 2, rows)
    tmax = 90 + 0.5 * X[:, 0] - 15 * X[:, 8] + rng.normal(0, 3, rows)
    ice = np.where(tmax < 75, 0.0, -999.0)
    split = rng.choice([0, 1, 2], size=rows, p=[0.6, 0.2, 0.2]).astype(np.int8)
    block = (split * 100 + rng.integers(0, 12, rows)).astype(np.int32)
    np.savez_compressed(path, X=X, temp_avg_k=avg.astype(np.float32), temp_max_k=tmax.astype(np.float32), ice_depth_m=ice.astype(np.float32),
                        cold_trap=(ice == 0).astype(np.int8), split=split, block=block, features=np.array(hazard.FEATURES))
    path.with_suffix(".json").write_text(MANIFEST)


def small_config():
    config = dict(CONFIG)
    config["training"] = dict(config["training"], train_subsample=2000,
                              hist_gradient_boosting=dict(config["training"]["hist_gradient_boosting"], max_iter=20))
    return config


def test_training_never_looks_at_test_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(hm, "GRID", dict(max_leaf_nodes=(15, 31), min_samples_leaf=(50,)))
    first = tmp_path / "a.npz"
    tiny_dataset(first)
    with np.load(first) as data:
        arrays = {k: data[k] for k in data.files}
    shuffled = dict(arrays)
    test = np.flatnonzero(arrays["split"] == 2)
    rng = np.random.default_rng(1)
    for key in ("temp_avg_k", "temp_max_k", "cold_trap"):
        shuffled[key] = arrays[key].copy()
        shuffled[key][test] = rng.permutation(arrays[key][test])          # destroy the test labels only
    second = tmp_path / "b.npz"
    np.savez_compressed(second, **shuffled)
    second.with_suffix(".json").write_text(MANIFEST)
    a = hm.run(small_config(), first, tmp_path / "a.joblib", tmp_path / "ra")
    b = hm.run(small_config(), second, tmp_path / "b.joblib", tmp_path / "rb")
    assert a["model_selection_on_validation_blocks"] == b["model_selection_on_validation_blocks"]
    assert a["regression"]["temp_max_k"]["val"] == b["regression"]["temp_max_k"]["val"]
    assert a["regression"]["temp_max_k"]["test"]["mae_k"] != b["regression"]["temp_max_k"]["test"]["mae_k"]   # test is scored, not used
    probe = arrays["X"][:200]
    np.testing.assert_allclose(joblib.load(tmp_path / "a.joblib").predict(probe), joblib.load(tmp_path / "b.joblib").predict(probe))


def test_report_states_baselines_and_marks_labels_real(tmp_path, monkeypatch):
    monkeypatch.setattr(hm, "GRID", dict(max_leaf_nodes=(15,), min_samples_leaf=(50,)))
    tiny_dataset(tmp_path / "d.npz")
    report = hm.run(small_config(), tmp_path / "d.npz", tmp_path / "m.joblib", tmp_path / "r")
    entry = report["regression"]["temp_max_k"]
    assert set(report["baselines"]["temp_max_k"]) == {"constant_train_mean", "distance_from_pole_only", "linear_all_features"}
    assert isinstance(entry["beats_every_baseline_on_test_mae"], bool)
    assert "real" in report["labels"] and report["status"] == "research / experimental"
    assert (tmp_path / "r" / "report.md").read_text().startswith("# Thermal hazard model")
    assert 0 <= entry["test"]["interval_coverage"] <= 1
