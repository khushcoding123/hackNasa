"""Thermal hazard model in the app: scoring contract, domain limits, the HTTP interface and model registration."""
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sklearn.dummy import DummyClassifier, DummyRegressor

from moonmesh.server import app
from ora import hazard, hazard_api, status


def fake_model():
    X = np.zeros((4, len(hazard.FEATURES)))
    regressors = {"temp_avg_k": DummyRegressor(strategy="constant", constant=100.0).fit(X, [100.0] * 4),
                  "temp_max_k": DummyRegressor(strategy="constant", constant=150.0).fit(X, [150.0] * 4)}
    low = DummyRegressor(strategy="constant", constant=90.0).fit(X, [90.0] * 4)
    high = DummyRegressor(strategy="constant", constant=210.0).fit(X, [210.0] * 4)
    classifier = DummyClassifier(strategy="prior").fit(X, [0, 0, 0, 1])
    reference = {t: list(np.linspace(40, 300, 21)) for t in hazard.TARGETS}
    return hazard.ThermalHazardModel(hazard.FEATURES, regressors, {t: (low, high) for t in hazard.TARGETS}, classifier, reference)


@pytest.fixture
def flat_terrain(monkeypatch):
    monkeypatch.setattr(hazard, "_terrain", lambda: np.zeros((hazard.DIMENSION, hazard.DIMENSION), dtype=np.float32))


def test_model_is_registered_and_marked_research():
    assert "ora_hazard_thermal" in status.MODELS
    assert status.MODELS["ora_hazard_thermal"]["card"] == "hazard_thermal"
    assert hazard_api.LABEL == "research / experimental"


def test_scoring_returns_ordered_intervals_and_refuses_outside_the_training_cap(flat_terrain):
    points = [dict(id="in", lat=-86.8, lon=337.2, sunlight_pct=50.0), dict(id="north", lat=-70.0, lon=10.0), dict(id="edge", lat=-89.9, lon=0.0)]
    result = hazard.score_points(points, fake_model())
    inside, outside = result["points"][0], result["points"][1]
    assert inside["in_domain"] and inside["temp_max_k"]["low"] <= inside["temp_max_k"]["value"] <= inside["temp_max_k"]["high"]
    assert 0 <= inside["temp_avg_k"]["percentile"] <= 100 and 0 <= inside["cold_trap_probability"] <= 1
    assert set(inside["inputs"]) == set(hazard.FEATURES)
    assert outside["in_domain"] is False and "80-90 S" in outside["reason"] and "temp_max_k" not in outside   # no number outside the cap
    assert result["agreement_with_physics"]["n"] == 1
    assert result["agreement_with_physics"]["spearman_sunlight_vs_predicted_max_temperature"] is None      # needs at least four points


def test_api_reports_unavailable_model_without_breaking_the_app(monkeypatch):
    monkeypatch.setattr(hazard_api, "_model", lambda: (None, dict(error="file not found")))
    client = TestClient(app)
    response = client.post("/api/hazard/thermal", json={"points": [{"id": "R01", "lat": -86.8, "lon": 337.2}]})
    assert response.status_code == 503 and "not available" in response.json()["detail"]
    info = client.get("/api/hazard/model").json()
    assert info["label"] == "research / experimental" and info["domain"].startswith("south polar cap")


def test_api_scores_and_labels_the_result(monkeypatch, flat_terrain):
    monkeypatch.setattr(hazard_api, "_model", lambda: (fake_model(), dict(sha256="abc")))
    client = TestClient(app)
    response = client.post("/api/hazard/thermal", json={"points": [{"id": "R01", "lat": -86.8, "lon": 337.2, "sunlight_pct": 30}]})
    body = response.json()
    assert response.status_code == 200 and body["label"] == "research / experimental"
    assert body["points"][0]["temp_max_k"]["value"] == 150.0 and body["model"]["sha256"] == "abc"
    assert client.post("/api/hazard/thermal", json={"points": []}).status_code == 422
    assert client.post("/api/hazard/thermal", json={"points": [{"id": "x", "lat": -200, "lon": 0}]}).status_code == 422


def test_trained_artifact_scores_the_known_sites():
    model, state = status.models()["ora_hazard_thermal"]
    if model is None:
        pytest.skip(f"hazard model not trained: {state.get('error')}")
    from moonmesh.data import SITES
    points = [dict(id=key, lat=lat, lon=lon) for key, (_, lat, lon) in SITES.items()]
    result = hazard.score_points(points, model)
    for row in result["points"]:
        assert row["in_domain"] and 20 < row["temp_max_k"]["value"] < 400 and 0 <= row["cold_trap_probability"] <= 1
    assert state["model_card"]["section_present"]
