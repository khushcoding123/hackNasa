"""Preflight, honest model status and the three-way result status (phase 0 of the reliability plan)."""
import os
import time

import joblib
import pytest

import moonmesh.data
from moonmesh import evidence
from moonmesh.science import bounded
from ora import status
from ora.scenario import SITING_METHOD


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(moonmesh.data, "DATA", tmp_path)
    return tmp_path / "models"


def test_missing_models_are_reported_with_their_fallback(model_dir):
    rows = {m["name"]: m for m in status.model_status()}
    assert set(rows) == set(status.MODELS)
    for row in rows.values():
        assert not row["present"] and not row["loaded"]
        assert "python -m ora" in row["error"] and row["fallback"]


def test_corrupt_model_is_a_visible_error_not_a_silent_skip(model_dir):
    (model_dir / "ora_siting_prior.joblib").write_bytes(b"not a pickle")
    model, row = status.models()["ora_siting_prior"]
    assert model is None and row["present"] and not row["loaded"]
    assert row["error"] and row["sha256"]


def test_replaced_artifact_is_reloaded(model_dir):
    from sklearn.linear_model import LinearRegression
    path = model_dir / "ora_gain_model.joblib"
    path.write_bytes(b"broken")
    assert status.loaded("ora_gain_model") is None
    joblib.dump(LinearRegression().fit([[0.0], [1.0]], [0.0, 1.0]), path)
    later = time.time() + 5
    os.utime(path, (later, later))  # make sure the mtime moves even on coarse file systems
    model, row = status.models()["ora_gain_model"]
    assert model is not None and row["loaded"] and row["estimator"] == "LinearRegression"


def test_card_hash_mismatch_is_flagged(model_dir, tmp_path, monkeypatch):
    from sklearn.linear_model import LinearRegression
    joblib.dump(LinearRegression().fit([[0.0], [1.0]], [0.0, 1.0]), model_dir / "ora_siting_prior.joblib")
    card = tmp_path / "card.json"
    card.write_text('{"trained": "x", "siting_prior": {}, "artifacts": {"ora_siting_prior": {"sha256": "0000"}}}')
    monkeypatch.setattr(status, "CARD", card)
    row = status.models()["ora_siting_prior"][1]
    assert row["model_card"]["section_present"] and row["model_card"]["matches_file"] is False


def test_siting_evidence_names_the_fallback(monkeypatch):
    from ora.scenario import scoring_evidence
    monkeypatch.setattr(status, "model_status", lambda: [dict(name=n, loaded=False, error="missing") for n in status.MODELS])
    out = scoring_evidence("terrain heuristic (untrained)")
    assert out["fallback"] and not any(m["used"] for m in out["models"])


def test_status_is_three_way():
    ok = evidence.check("a", "x", 1, True)
    assert evidence.assess([ok])["status"] == evidence.MET
    assert evidence.assess([ok, evidence.check("b", "x", 0, False)])["status"] == evidence.FAILED
    assert evidence.assess([ok, evidence.check("b", "x", None, None)])["status"] == evidence.INSUFFICIENT
    assert evidence.assess([ok], [evidence.gap("g", "coarse terrain")])["status"] == evidence.INSUFFICIENT
    assert evidence.assess([ok], [evidence.gap("g", "caveat", blocking=False)])["status"] == evidence.MET
    assert evidence.assess([])["status"] == evidence.INSUFFICIENT  # nothing checked is never "met"
    report = evidence.assess([ok])
    assert report["not_evaluated"] == [] and "not a forecast" in report["scope"]


def test_bounded_check_needs_the_band_to_agree():
    assert bounded("availability", 95, 97, 96, 99, True, "%", "")["met"] is True
    assert bounded("availability", 95, 96, 90, 97, True, "%", "")["met"] is None  # holds nominally, fails if marginal samples are lost
    assert bounded("availability", 95, 80, 79, 90, True, "%", "")["met"] is False
    assert bounded("blackout", 24, 20, 30, 10, False, "h", "")["met"] is None


def test_inventory_lists_required_inputs():
    report = status.inventory(hash_large=False)
    assert report["status"] in report["meaning"]
    assert {"spice_kernels", "lola_products", "site_terrain", "earth_training"} <= set(report["data"])
    assert report["code"].get("commit") or report["code"].get("error")
    missing_required = [e for g in report["data"].values() for e in g if e["required"] and e.get("problem")]
    if missing_required:
        assert report["status"] == "not_ready"


def test_api_reports_loaded_models_and_assessment():
    from fastapi.testclient import TestClient
    from moonmesh.server import app
    if not (moonmesh.data.DATA / "terrain" / "shackleton_near20km.npz").is_file():
        raise FileNotFoundError("Run python -m moonmesh.data, then python -m ora.data.")
    client = TestClient(app)
    report = client.get("/api/ora/status").json()
    assert report["status"] in ("ready", "degraded", "not_ready")
    config = client.get("/api/ora/config").json()
    assert {m["name"] for m in config["models"]} == set(status.MODELS)
    net = client.post("/api/ora/network", json={"candidates": 60, "extra_relays": 0}).json()
    assert net["assessment"]["status"] in (evidence.MET, evidence.FAILED, evidence.INSUFFICIENT)
    assert net["assessment"]["not_evaluated"] and net["assessment"]["evaluated_hazards"]
    loaded = {m["name"]: m for m in config["models"] if m["loaded"]}
    for row in net["score_evidence"]["models"]:
        if row["used"]:
            assert row["name"] in loaded and row["sha256"] == loaded[row["name"]]["sha256"]
    assert net["score_method"] == SITING_METHOD and not net["score_evidence"]["fallback"]
    assert not any(r["used"] for r in net["score_evidence"]["models"])  # relays are ranked by computed viewshed, not a model
