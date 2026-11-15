"""Explainable limits, deployment screening, power-loss what-ifs and the setup check, without downloading NASA data."""
import zipfile
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from unittest import TestCase

import numpy as np

from lunar_core.terrain import TerrainGrid
from moonmesh import setup
from moonmesh.excel import ExportRequest, export_workbook
from moonmesh.diagnostics import deployment_report, explain_candidate, power_loss_simulation
from moonmesh.models import Mission
from moonmesh.science import scenario
from ora import status

N = 100


def sample(rf=30.0, reason="Available"):
    return dict(rf_margin=[rf] * N, reason=[reason] * N)


def state(reason="Available"):
    return dict(reason=[reason] * N)


class ExplainTests(TestCase):
    def explain(self, los=True, surface_margin_ok=True, earth=None, relay=None, lander=None, sunlight=None):
        return explain_candidate(los, los is True and surface_margin_ok, surface_margin_ok, earth or sample(), relay or state(),
                                 np.ones(N, bool) if sunlight is None else sunlight, np.ones(N, bool) if lander is None else lander, 3.0)

    def test_nothing_limiting(self):
        info = self.explain()
        self.assertIsNone(info["primary"])
        self.assertEqual(info["available_pct"], 100)
        self.assertIn("Nothing limits", info["summary"])

    def test_blocked_surface_link_is_terrain_for_the_whole_mission(self):
        info = self.explain(los=False, surface_margin_ok=False)
        self.assertEqual(info["primary"], "terrain")
        self.assertEqual(info["blocked_pct"]["terrain"], 100)
        self.assertIn("whole mission", info["summary"])

    def test_surface_rf_shortfall_is_rf_not_terrain(self):
        info = self.explain(los=True, surface_margin_ok=False)
        self.assertEqual(info["primary"], "rf")
        self.assertEqual(info["blocked_pct"]["terrain"], 0)

    def test_frozen_relay_is_temperature(self):
        reason = ["Available"] * 40 + ["Frozen"] * 60
        info = self.explain(relay=dict(reason=reason))
        self.assertEqual(info["primary"], "temperature")
        self.assertEqual(info["blocked_pct"]["temperature"], 60)
        self.assertEqual(info["recovered_if_fixed_pct"]["temperature"], 60)

    def test_power_split_into_darkness_and_sunlight(self):
        reason = ["Insufficient power"] * 50 + ["Available"] * 50
        sun = np.array([False] * 30 + [True] * 70)
        info = self.explain(relay=dict(reason=reason), sunlight=sun)
        self.assertEqual(info["primary"], "power")
        self.assertEqual(info["power_in_darkness_pct"], 30)
        self.assertEqual(info["power_in_sunlight_pct"], 20)

    def test_overlapping_constraints_recover_less_than_their_share(self):
        earth = sample(rf=-10.0)                      # RF fails everywhere
        reason = ["Frozen"] * 50 + ["Available"] * 50  # temperature fails in half
        info = self.explain(earth=earth, relay=dict(reason=reason))
        self.assertEqual(info["primary"], "rf")
        self.assertEqual(info["recovered_if_fixed_pct"]["rf"], 50)          # the frozen half stays down
        self.assertEqual(info["recovered_if_fixed_pct"]["temperature"], 0)  # RF still holds it down


class DeploymentTests(TestCase):
    def run_report(self, heights, limit=15.0, route_limit=None):
        heights = np.asarray(heights, float)
        grid = TerrainGrid(heights, 0.0, 20.0 * len(heights), 20.0)
        dy, dx = np.gradient(grid.heights, grid.pixel_size_m, grid.pixel_size_m)
        slope = TerrainGrid(np.degrees(np.arctan(np.hypot(dx, dy))), grid.x_left, grid.y_top, grid.pixel_size_m)
        top = grid.y_top - 400
        candidate = dict(x=1500.0, y=top, distance=1.1, slope=2.0)
        return deployment_report(grid, slope, (400.0, top), candidate, Mission(slope_limit=limit, route_slope_limit=route_limit))

    def test_flat_ground_is_feasible(self):
        report = self.run_report(np.zeros((200, 200)))
        self.assertEqual(report["verdict"], "feasible")
        self.assertAlmostEqual(report["steep_pct"], 0)
        self.assertAlmostEqual(report["route_km"], report["straight_km"], places=3)

    def test_steep_wall_across_the_traverse_is_a_hazard(self):
        heights = np.zeros((200, 200))
        heights[:, 40:55] = np.linspace(0, 80, 15)      # a steep wall across the traverse
        heights[:, 55:] = 80
        report = self.run_report(heights)
        self.assertEqual(report["verdict"], "hazard")
        self.assertGreater(report["longest_steep_m"], 0)
        self.assertGreater(report["route_km"], report["straight_km"])
        self.assertGreater(report["ascent_m"], 0)
        self.assertFalse(next(c for c in report["checks"] if c["name"] == "Steepest traverse slope")["ok"])

    def test_route_slope_limit_overrides_the_mast_limit(self):
        flat = np.zeros((200, 200))
        self.assertEqual(self.run_report(flat, limit=15, route_limit=30)["slope_limit"], 30)
        self.assertEqual(self.run_report(flat, limit=15)["slope_limit"], 15)

    def test_missing_elevation_is_unverified_not_feasible(self):
        heights = np.zeros((200, 200))
        heights[:, 40:60] = np.nan
        self.assertEqual(self.run_report(heights)["verdict"], "unverified")


class PowerLossTests(TestCase):
    def setUp(self):
        self.mission = Mission(minutes=60)
        self.times = tuple(datetime(2026, 11, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(N))
        self.end = self.times[-1] + timedelta(hours=1)
        self.options = [dict(id="R01"), dict(id="R02")]

    def run_sim(self, relays, hours=10):
        return power_loss_simulation(np.zeros(N, bool), relays, (0, 1), self.options, self.times, self.end, self.mission, hours)

    def test_sole_relay_loss_blacks_out_the_window(self):
        result = self.run_sim([np.ones(N, bool), np.zeros(N, bool)])
        first, second = result["relays"]
        self.assertTrue(first["affects_network"])
        self.assertEqual(first["connected_during_pct"], 0)
        self.assertEqual(first["metrics"]["longest"], 10)
        self.assertEqual(first["blackout_increase"], 10)
        self.assertFalse(second["affects_network"])   # never available: losing it changes nothing

    def test_redundant_relay_keeps_the_network_connected(self):
        result = self.run_sim([np.ones(N, bool), np.ones(N, bool)])
        for row in result["relays"]:
            self.assertEqual(row["connected_during_pct"], 100)
            self.assertEqual(row["blackout_increase"], 0)
            self.assertIn("stays connected", row["note"])

    def test_worst_moment_is_when_the_relay_is_the_only_path(self):
        first = np.zeros(N, bool)
        first[:60] = True
        second = np.zeros(N, bool)
        second[:30] = True                              # the backup covers only the first 30 h
        row = self.run_sim([first, second], hours=10)["relays"][0]
        self.assertGreaterEqual(row["first"], 30)       # it loses power after the backup has gone
        self.assertEqual(row["metrics"]["longest"], 10 + (N - 60))


class IntegrationTests(TestCase):
    def test_scenario_reports_limits_deployment_and_power_loss(self):
        start = date(2026, 11, 1)
        mission = Mission(start=start, end=start + timedelta(days=3), minutes=60, max_relays=1)
        result = scenario(mission, include_map=False)
        for candidate in result["candidates"]:
            self.assertIn(candidate["deployment"]["verdict"], ("feasible", "hazard", "unverified"))
            self.assertTrue(candidate["limiting"]["summary"])
        self.assertLessEqual(set(result["deployment"]["counts"]), {"feasible", "hazard", "unverified"})
        self.assertEqual(result["power_loss"]["hours"], 24)
        self.assertLessEqual({row["id"] for row in result["power_loss"]["relays"]}, set(result["recommended"]))
        # Steep traverses are avoided by default: no selected relay may be a hazard. Turning it off can only add candidates.
        hazards = {c["id"] for c in result["candidates"] if c["deployment"]["verdict"] == "hazard"}
        self.assertTrue(result["deployment"]["enforced"])
        self.assertFalse(set(result["recommended"]) & hazards)
        relaxed = scenario(mission.model_copy(update=dict(avoid_hazard_routes=False)), include_map=False)
        self.assertFalse(relaxed["deployment"]["enforced"])
        self.assertGreaterEqual(relaxed["eligible"], result["eligible"])
        if result["deployment"]["if_steep_allowed"]:
            alternative = result["deployment"]["if_steep_allowed"]
            self.assertEqual(alternative["recommended"], relaxed["recommended"])   # the cost of the rule is what relaxing it gives
            self.assertGreaterEqual(alternative["network"]["availability"], result["network"]["availability"])
        # The workbook carries the diagnostics too.
        book = zipfile.ZipFile(BytesIO(export_workbook(ExportRequest(result=result))))
        self.assertIn("Relay Diagnostics", book.read("xl/workbook.xml").decode())


class SetupTests(TestCase):
    def test_preflight_checks_every_runtime_requirement(self):
        names = {row["name"].lower() for row in status.libraries()}
        for line in (status.ROOT / "requirements.txt").read_text().splitlines():
            line = line.split("#")[0].strip()
            if line:
                self.assertIn(line.replace(">", "=").replace("<", "=").split("=")[0].lower(), names)
        self.assertIn("xlsxwriter", names)

    def test_report_marks_missing_steps_and_gives_the_fix(self):
        text = setup.render([setup.step("Python packages", False, "XlsxWriter (not installed)", fix="pip install -r requirements.txt"),
                             setup.step("Web UI build", False, "missing", fix="npm ci", required=False),
                             setup.step("End-to-end smoke run", True, "ok")])
        self.assertIn("[MISS] Python packages", text)
        self.assertIn("[warn] Web UI build", text)
        self.assertIn("fix: pip install -r requirements.txt", text)
