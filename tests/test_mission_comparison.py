from unittest.mock import patch

from fastapi.testclient import TestClient

from moonmesh.server import app


def test_alternatives_share_the_same_mission_and_report_tradeoffs():
    def relay(relay_id, online):
        return dict(id=relay_id, lat=-86.8, lon=337.2, distance=1, temporal_coverage=online)

    snapshot = dict(site={"name": "Test"}, config={"minutes": 60}, times=["t0", "t1", "t2", "t3"],
                    samples={"direct": [True, False, False, False]}, provenance={},
                    recommended=["R01", "R02"],
                    candidates=[relay("R01", [False, True, False, False]),
                                relay("R02", [False, False, True, True])])
    request = dict(mission={"minutes": 60, "budget_usd": 150, "relay_cost_usd": 100},
                   alternatives=[{"name": "One tower", "relay_ids": ["R02"]}])
    client = TestClient(app)
    with patch("moonmesh.server.compute", return_value=snapshot) as compute:
        response = client.post("/api/mission/compare-configurations", json=request)
        assert compute.call_args.kwargs == {"include_map": False}
        assert response.status_code == 200, response.text
        result = response.json()
        base, recommended, alternative = result["configurations"]
        assert result["times"] == snapshot["times"]
        assert base["network"]["availability"] == 25
        assert recommended["network"]["availability"] == 100
        assert recommended["cost"]["within_budget"] is False
        assert alternative["network"]["availability"] == 75
        assert alternative["network"]["longest"] == 1
        assert alternative["recovered_hours"] == 2
        assert alternative["worst_relay_failure"]["availability"] == 25
        assert alternative["cost"]["total_usd"] == 100
        request["alternatives"][0]["relay_ids"] = ["R99"]
        assert client.post("/api/mission/compare-configurations", json=request).status_code == 422
        request["alternatives"][0]["relay_ids"] = ["R01", "R01"]
        assert client.post("/api/mission/compare-configurations", json=request).status_code == 422
