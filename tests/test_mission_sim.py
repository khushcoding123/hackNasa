from datetime import UTC, datetime, timedelta

import numpy as np

from moonmesh.data import DATA
from moonmesh.mission_sim import MOON_RADIUS_KM, moon_occults, service_state


def test_service_requires_simultaneous_known_hops_and_node_power():
    start = datetime(2026, 11, 1, tzinfo=UTC)
    step = timedelta(minutes=5)
    times = tuple(start + i*step for i in range(5))
    good = {"status": "available", "reasons": []}
    blocked = {"status": "unavailable", "reasons": ["Terrain obstruction"]}
    unknown = {"status": "unknown", "reasons": ["Unknown relay horizon terrain"]}
    samples = [
        {"hops": {"surface": blocked, "access": good, "backhaul": good}, "node_failures": {}},
        {"hops": {"surface": good, "access": good, "backhaul": blocked}, "node_failures": {}},
        {"hops": {"surface": good, "access": unknown, "backhaul": good}, "node_failures": {}},
        {"hops": {"surface": good, "access": good, "backhaul": good}, "node_failures": {"Relay": "Battery depleted"}},
        {"hops": {"surface": good, "access": good, "backhaul": good}, "node_failures": {}},
    ]
    state = service_state(samples, times, times[-1]+step, 1/12)
    assert state["online"] == [False, False, False, False, True]
    assert state["metrics"]["availability"] == 20
    assert state["outages"][0]["last"] == 4
    assert "Relay: Battery depleted" in state["outages"][0]["causes"]


def test_lunar_occultation_checks_segment_not_infinite_line():
    radius = MOON_RADIUS_KM
    orbiter = np.array([[radius+500, 0, 0]] * 3)
    receivers = np.array([[-400_000, 0, 0], [400_000, 0, 0], [0, 400_000, 0]])
    assert moon_occults(orbiter, receivers).tolist() == [True, False, False]


def test_reference_api_run():
    from fastapi.testclient import TestClient
    from moonmesh.server import app

    response = TestClient(app).get("/api/mission/reference")
    if response.status_code == 503:
        raise FileNotFoundError(f"{DATA}: {response.json()['detail']}")
    assert response.status_code == 200, response.text
    run = response.json()
    nominal = run["states"]["nominal"]
    assert len(run["samples"]) == len(nominal["online"]) == 288
    assert any(nominal["online"]) and not all(nominal["online"])
    assert all(len(baseline["online"]) == 288 for baseline in run["baselines"].values())
    for sample, online in zip(run["samples"], nominal["online"], strict=True):
        assert online == (not sample["node_failures"]
                          and all(hop["status"] == "available" for hop in sample["hops"].values()))
    for node in ("relay", "satellite"):
        failed = run["states"][node]
        assert failed["metrics"]["availability"] == 0
        assert failed["metrics"]["longest"] == 24
        assert f"{node.capitalize()}: disabled" in failed["outages"][0]["causes"]
