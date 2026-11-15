import json
import unittest
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from moonmesh.models import Mission, Radio, Satellite

ROOT = Path(__file__).resolve().parents[1] / ".moonmesh-data"
HAVE_DATA = (ROOT / "terrain" / "haworth_near20km.npz").is_file() and (ROOT / "kernels" / "de440s.bsp").is_file()


class ModelTests(unittest.TestCase):
    def test_custom_orbit_needs_all_elements(self):
        with self.assertRaises(ValueError):
            Satellite(preset="custom", semi_major_axis_km=3000)

    def test_invalid_orbit_is_rejected_by_api(self):
        from moonmesh.server import app
        client = TestClient(app)
        for config in ({"semi_major_axis_km":1800,"eccentricity":0.5}, {"epoch":"2026-11-01T00:00:00"}):
            response = client.post("/api/mission", json={"satellite":config})
            self.assertEqual(response.status_code, 422)

    def test_shared_satellite_cannot_satisfy_resilience(self):
        from moonmesh.environment import CASES, optimize_environment
        from moonmesh.network import evaluate, optimize
        mission = Mission(resilient=True, availability=100, blackout_hours=0)
        on, off = np.ones(4, dtype=bool), np.zeros(4, dtype=bool)
        fallback = (off, [off, off])
        _, nominal, worst, redundant = evaluate(on, [on, on], (0, 1), mission, fallback)
        self.assertEqual(nominal["availability"], 100)
        self.assertEqual(worst["availability"], 0)
        self.assertEqual(redundant, 0)
        _, feasible = optimize_environment({c:on for c in CASES}, {c:[on,on] for c in CASES}, [0,1], mission,
            ({c:off for c in CASES}, {c:[off,off] for c in CASES}))
        self.assertFalse(feasible)
        self.assertFalse(optimize(on, [on,on], [0,1], mission, without_satellite=fallback)[1])

    def test_default_mission_has_no_satellite(self):
        self.assertIsNone(Mission().satellite)

    def test_presets_endpoint(self):
        from moonmesh.server import app
        body = TestClient(app).get("/api/satellite/presets").json()
        self.assertEqual(body["status"], "simulated")
        self.assertIn("polar_500km", body["presets"])


@unittest.skipUnless(HAVE_DATA, "Run python -m moonmesh.data (terrain or kernels missing)")
class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from moonmesh.science import scenario
        cls.plain = scenario(Mission(end="2026-11-04"), include_map=False)
        cls.sat = scenario(Mission(end="2026-11-04", satellite=Satellite()), include_map=False)

    def test_no_satellite_leaves_no_trace(self):
        self.assertIsNone(self.plain["satellite"])
        self.assertTrue(all("satellite" not in c for c in self.plain["candidates"]))

    def test_losing_the_satellite_restores_the_baseline(self):
        lost = self.sat["satellite"]["network_without_satellite"]
        self.assertEqual(lost, self.plain["network"])

    def test_satellite_never_reduces_availability(self):
        self.assertGreaterEqual(self.sat["network"]["availability"], self.plain["network"]["availability"])
        self.assertTrue(np.all(np.array(self.sat["samples"]["direct"]) >= np.array(self.plain["samples"]["direct"])))

    def test_satellite_block_is_labelled_and_flags_missing_rf(self):
        block = self.sat["satellite"]
        self.assertEqual(block["status"], "simulated")
        self.assertEqual(block["rf_evaluated"], dict(surface_to_sat=False, sat_to_earth=False))
        gaps = {g["name"]: g for g in self.sat["assessment"]["evidence_gaps"]}
        self.assertTrue(gaps["Surface-to-satellite RF not evaluated"]["blocking"])
        self.assertNotEqual(self.sat["assessment"]["status"], "requirements_met")
        self.assertNotIn("Lunar orbiting relay satellites", self.sat["assessment"]["not_evaluated"])
        self.assertIn("Lunar orbiting relay satellites", self.plain["assessment"]["not_evaluated"])

    def test_radio_margin_can_only_remove_access(self):
        from moonmesh.science import scenario
        weak = Radio(ghz=2.2, watts=0.001, tx_gain=0, rx_gain=0, sensitivity=-100, loss=3, margin=3)
        strong = scenario(Mission(end="2026-11-04", satellite=Satellite(surface_to_sat_radio=weak)), include_map=False)
        self.assertLessEqual(strong["satellite"]["lander_access"]["availability"],
                             self.sat["satellite"]["lander_access"]["availability"])
        self.assertTrue(strong["satellite"]["rf_evaluated"]["surface_to_sat"])

    def test_excel_has_satellite_sheet(self):
        from io import BytesIO
        from zipfile import ZipFile
        from moonmesh.excel import ExportRequest, export_workbook
        data = export_workbook(ExportRequest(result=json.loads(json.dumps(self.sat))))
        sheets = lambda blob: ZipFile(BytesIO(blob)).read("xl/workbook.xml").decode()
        self.assertIn("Satellite (simulated)", sheets(data))
        plain = export_workbook(ExportRequest(result=json.loads(json.dumps(self.plain))))
        self.assertNotIn("Satellite (simulated)", sheets(plain))

    def test_satellite_none_reproduces_samples_and_selection(self):
        from moonmesh.science import scenario
        explicit = scenario(Mission(end="2026-11-04", satellite=None), include_map=False)
        self.assertEqual(explicit, self.plain)

    def test_satellite_failure_state_and_all_combinations(self):
        result = self.sat
        self.assertEqual(result["failure_states"]["S1"]["metrics"], result["satellite"]["network_without_satellite"])
        self.assertIn("S1", [f["id"] for f in result["failures"]])
        for key, state in result["failure_states"].items():
            if "S1" in key:
                self.assertTrue(np.all(np.asarray(state["combined"]) <= np.asarray(result["samples"]["network"])))

    def test_api_returns_simulated_track_and_export(self):
        from unittest.mock import patch
        from moonmesh.server import app
        client = TestClient(app)
        with patch("moonmesh.server.scenario", return_value=self.sat) as compute:
            response = client.post("/api/mission", json={"end":"2026-11-04", "satellite":{"preset":"polar_500km"}})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(compute.call_args.args[0].satellite, Satellite)
        block = response.json()["satellite"]
        self.assertEqual(len(block["lander"]["reason"]), len(self.sat["times"]))
        exported = client.post("/api/export/xlsx", json={"result":response.json()})
        self.assertEqual(exported.status_code, 200)
        from io import BytesIO
        from zipfile import ZipFile
        archive = ZipFile(BytesIO(exported.content))
        strings = "".join(archive.read(name).decode() for name in archive.namelist() if name.startswith("xl/worksheets/") and name.endswith(".xml"))
        for label in ("Sat-Earth margin (dB)", "Satellite cost USD", "Surface-sat state", "simulated"):
            self.assertIn(label, strings)

    def test_track_and_period(self):
        track = self.sat["satellite"]["track"]
        self.assertEqual(len(track["lat"]), len(self.sat["times"]))
        self.assertAlmostEqual(self.sat["satellite"]["period_min"], 158.28, delta=0.05)
        self.assertAlmostEqual(np.mean(track["altitude_km"]), 500, delta=1)


if __name__ == "__main__":
    unittest.main()
