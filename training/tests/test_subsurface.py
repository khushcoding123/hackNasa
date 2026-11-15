import numpy as np
import pytest

from moonmesh_train import subsurface as ss
from moonmesh_train.subsurface_model import check_features, label, load_config, sample_scenarios


def test_lossless_dielectric_has_no_absorption_and_attenuation_grows_with_frequency():
    assert ss.attenuation_db_per_m(2.4, 4.0, 0.0) == 0
    assert ss.attenuation_db_per_m(2.4, 4.0, .01) > ss.attenuation_db_per_m(0.4, 4.0, .01)


def test_low_loss_approximation_matches_exact():
    f, eps, t = 1.0, 3.0, 0.01
    approx = ss.NP_TO_DB * np.pi * f * 1e9 * np.sqrt(eps) * t / ss.C
    assert ss.attenuation_db_per_m(f, eps, t) == pytest.approx(approx, rel=1e-3)


def test_interface_loss_zero_for_matched_and_positive_otherwise():
    assert ss.interface_loss_db(4, 4) == pytest.approx(0)
    assert ss.interface_loss_db(1, 8) > 0


def test_regolith_fits_values():
    eps, t = ss.regolith_properties(1.5, 10)
    assert eps == pytest.approx(1.919 ** 1.5)
    assert t == pytest.approx(10 ** (0.38 + 0.468 - 3.26))


def test_margin_falls_with_depth_and_offset():
    cfg = load_config(); r = cfg["radio"]
    base = dict(frequency_ghz=0.9, regolith_thickness_m=2., basalt_thickness_m=5., horizontal_offset_m=0.,
                regolith_density_g_cc=1.6, feo_tio2_wt_pct=10., basalt_eps_real=7., basalt_tan_delta=.01)
    m0 = ss.link_budget(**base, radio=r)["link_margin_db"]
    assert ss.link_budget(**{**base, "basalt_thickness_m": 20.}, radio=r)["link_margin_db"] < m0
    assert ss.link_budget(**{**base, "horizontal_offset_m": 50.}, radio=r)["link_margin_db"] < m0


def test_max_depth_shrinks_with_loss_and_is_zero_when_unreachable():
    r = load_config()["radio"]
    assert ss.max_depth_m(0.9, r, 7., .005) > ss.max_depth_m(0.9, r, 7., .03) > 0
    assert ss.max_depth_m(0.9, {**r, "tx_power_dbm": -300.}, 7., .01) == 0


def test_features_reject_label_terms_and_labels_marked_simulated():
    with pytest.raises(ValueError):
        check_features(ss.FEATURES + ["link_margin_db"])
    cfg = load_config()
    df = label(sample_scenarios(cfg, 50, 1), cfg["radio"])
    assert (df.label_source == "simulated").all() and list(df[ss.FEATURES].columns) == ss.FEATURES


def test_sampling_is_seeded():
    cfg = load_config()
    assert sample_scenarios(cfg, 20, 7).equals(sample_scenarios(cfg, 20, 7))


def test_small_training_run(tmp_path, monkeypatch):
    monkeypatch.setenv("MOONMESH_DATA_ROOT", str(tmp_path))
    from moonmesh_train.subsurface_model import run
    rep = run(load_config(), 1500)
    assert rep["labels"] == "simulated" and rep["in_distribution_test"]["mae_db"] < 15
