import math

import pytest
import yaml

from moonmesh.models import Mission
from moonmesh_train.config import ConfigError, RADIO_FIELDS, load_experiment, load_radio_profile


def test_reference_matches_app_defaults():
    mission = Mission()
    radio = mission.surface_radio
    assert load_radio_profile("moonmesh_2p4_ref") == pytest.approx({
        "frequency_ghz": radio.ghz,
        "tx_power_dbm": 30 + 10 * math.log10(radio.watts),
        "tx_gain_dbi": radio.tx_gain,
        "rx_gain_dbi": radio.rx_gain,
        "fixed_loss_db": radio.loss,
        "receiver_sensitivity_dbm": radio.sensitivity,
        "required_margin_db": radio.margin,
        "max_range_m": mission.deployment_km * 1000,
    })


def test_pilot_loads_despite_unused_incomplete_profile():
    run = load_experiment("exp_b_haworth_pilot.yaml")
    assert run["radio"] == load_radio_profile("moonmesh_2p4_ref")
    assert run["seed"] == 42
    assert run["label_origin"] == "simulated"


def test_atlas_run_loads_published_settings():
    run = load_experiment("exp_a_lunarrf_haworth.yaml")
    assert run["radio"] == pytest.approx(dict(frequency_ghz=1.8, tx_power_dbm=30,
        tx_gain_dbi=0, rx_gain_dbi=0, fixed_loss_db=0,
        receiver_sensitivity_dbm=-94.40475498807768, required_margin_db=0, max_range_m=22040))
    assert (run["h_tx_m"], run["h_rx_m"]) == (4, 1.5)


@pytest.mark.parametrize("field", RADIO_FIELDS)
@pytest.mark.parametrize("mode", ["null", "missing"])
def test_every_required_field_is_checked(tmp_path, field, mode):
    profile = load_radio_profile("moonmesh_2p4_ref")
    if mode == "null":
        profile[field] = None
    else:
        del profile[field]
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump({"test": profile}), encoding="utf-8")
    with pytest.raises(ConfigError, match=field):
        load_radio_profile("test", path)


@pytest.mark.parametrize("value", [True, "2.4", float("nan"), float("inf"), 0, -1])
def test_invalid_frequency_rejected(tmp_path, value):
    profile = load_radio_profile("moonmesh_2p4_ref")
    profile["frequency_ghz"] = value
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump({"test": profile}), encoding="utf-8")
    with pytest.raises(ConfigError, match="frequency_ghz"):
        load_radio_profile("test", path)


def test_unknown_profile_rejected():
    with pytest.raises(ConfigError, match="Unknown radio profile"):
        load_radio_profile("typo")


@pytest.mark.parametrize("text", ["[invalid", "- list", "null", "test: null"])
def test_invalid_profile_document(tmp_path, text):
    path = tmp_path / "profiles.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_radio_profile("test", path)


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="Cannot read configuration"):
        load_radio_profile("test", tmp_path / "missing.yaml")


def test_run_requires_profile_name(tmp_path):
    path = tmp_path / "run.yaml"
    path.write_text("site: haworth\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="requires a radio_profile"):
        load_experiment(path)
