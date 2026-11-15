"""The dynamic cost model: cost must move with the scenario, add up, and reach the website and the workbook."""
from datetime import date, timedelta
from io import BytesIO
from unittest import TestCase
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from fastapi.testclient import TestClient

from moonmesh import costmodel
from moonmesh.costs import cost_report
from moonmesh.excel import ExportRequest, export_workbook
from moonmesh.models import Mission
from moonmesh.science import scenario
from moonmesh.server import app

REFERENCE = dict(id="R1", role="relay", range_km=2.0, route_km=0.0, slope_deg=0.0, verdict="feasible", mast_m=5, battery_wh=288, solar_w=18, heater_w=4)
SATELLITE = dict(altitude_km=500, gravity=1.0, track_deg=0, beam_deg=180, mask_deg=10, coverage_pct=95)


def price(nodes=None, **kwargs):
    kwargs.setdefault("scope", "mission")
    kwargs.setdefault("days", 30)
    return costmodel.build_report([REFERENCE] if nodes is None else nodes, **kwargs)


def area_snapshot(n=4):
    nodes = [dict(id=i, role="gateway" if i == 0 else "relay", lat=-86.8 - 0.01 * i, lon=10 + 0.02 * i, elevation=100.0, outside=False) for i in range(n)]
    edges = [[i, i + 1] for i in range(n - 1)]
    return dict(site=dict(name="Drawn area", lat=-86.8, lon=10, area_km2=12.5), nodes=nodes, recommended="tree",
                layouts=dict(tree=dict(name="Tree", links=len(edges), link_km=1.5, worst_after_node_loss=0.5, worst_after_link_loss=0.5, edges=edges)), plan=dict(chosen=list(range(1, n))), cost=cost_report(n, None, None))


class ModelTests(TestCase):
    def test_a_reference_node_costs_the_flat_price_before_programme_lines(self):
        report = price()
        self.assertEqual(sum(i["total_usd"] for i in report["items"] if i["node"]), 100_000)
        self.assertEqual(report["nodes"][0]["total_usd"], 100_000)

    def test_items_add_up_to_the_total_and_categories(self):
        report = price([dict(REFERENCE, id=f"R{i}", range_km=1 + i) for i in range(4)], satellite=SATELLITE, area_km2=5)
        self.assertAlmostEqual(sum(i["total_usd"] for i in report["items"]), report["total_usd"], places=2)
        self.assertAlmostEqual(sum(report["categories"].values()), report["total_usd"], places=2)
        self.assertAlmostEqual(report["surface_usd"] + report["satellite_usd"], report["total_usd"], places=2)
        for item in report["items"]:
            self.assertAlmostEqual(item["quantity"] * item["unit_cost_usd"], item["total_usd"], places=2)

    def test_more_nodes_cost_more_and_the_cost_is_not_flat_per_node(self):
        totals = [price([dict(REFERENCE, id=f"R{i}") for i in range(n)])["total_usd"] for n in (1, 2, 4, 8)]
        self.assertEqual(totals, sorted(set(totals)))   # strictly increasing
        self.assertEqual(price([])["total_usd"], 0)
        # Fixed programme costs are shared, so the cost per node falls as the network grows.
        self.assertGreater(totals[0] / 1, totals[3] / 8)

    def test_distance_slope_and_route_hazard_each_raise_the_price(self):
        base = price()["total_usd"]
        self.assertGreater(price([dict(REFERENCE, range_km=8)])["total_usd"], base)
        self.assertGreater(price([dict(REFERENCE, route_km=6)])["total_usd"], base)
        self.assertGreater(price([dict(REFERENCE, slope_deg=14)])["total_usd"], base)
        self.assertGreater(price([dict(REFERENCE, verdict="hazard")])["total_usd"], price([dict(REFERENCE, verdict="unverified")])["total_usd"])
        self.assertGreater(price([dict(REFERENCE, verdict="unverified")])["total_usd"], base)
        self.assertGreater(price([dict(REFERENCE, mast_m=10)])["total_usd"], base)
        self.assertGreater(price([dict(REFERENCE, battery_wh=576)])["total_usd"], base)
        self.assertGreater(price([dict(REFERENCE, heater_w=8)])["total_usd"], base)

    def test_a_gateway_costs_more_than_a_relay(self):
        self.assertGreater(price([dict(REFERENCE, role="gateway")])["total_usd"], price()["total_usd"])

    def test_area_and_mission_length_raise_programme_costs(self):
        self.assertGreater(price(area_km2=40)["total_usd"], price(area_km2=4)["total_usd"])
        self.assertGreater(price(days=90)["total_usd"], price(days=10)["total_usd"])
        self.assertEqual(price(days=10_000)["drivers"]["contingency_pct"], costmodel.Coefficients().contingency_cap_pct)

    def test_coefficients_can_be_overridden_and_typos_are_rejected(self):
        double = price(overrides=dict(structure_usd=40_000))
        self.assertEqual(double["nodes"][0]["parts"][0]["usd"], 40_000)
        with self.assertRaisesRegex(ValueError, "Unknown cost coefficient"):
            price(overrides=dict(structure_usd_typo=1))

    def test_budget_headroom_is_reported(self):
        report = price(budget_usd=100_000)
        self.assertFalse(report["within_budget"])
        self.assertLess(report["remaining_usd"], 0)


class SatelliteTests(TestCase):
    def sat(self, **change):
        return price(area_km2=10, site_lat=-86.8, satellite={**SATELLITE, **change})["satellite"]

    def test_speed_follows_altitude_and_gravity(self):
        self.assertAlmostEqual(self.sat()["speed_km_s"], 1.480, places=2)       # sqrt(mu / r) at 500 km
        self.assertGreater(self.sat(altitude_km=100)["speed_km_s"], self.sat(altitude_km=2000)["speed_km_s"])
        self.assertGreater(self.sat(gravity=2.0)["speed_km_s"], self.sat()["speed_km_s"])
        self.assertAlmostEqual(self.sat(altitude_km=500)["period_min"], 2 * 3.141592653589793 * (self.sat()["radius_km"] * 1000) ** 1.5 / (costmodel.MU ** 0.5) / 60, places=2)

    def test_higher_orbits_see_more_of_the_surface_so_fewer_satellites_are_needed(self):
        low, high = self.sat(altitude_km=150), self.sat(altitude_km=3000)
        self.assertLess(low["duty_pct"], high["duty_pct"])
        self.assertGreaterEqual(low["count"], high["count"])

    def test_a_narrow_beam_or_high_mask_needs_more_satellites(self):
        self.assertGreater(self.sat(beam_deg=15)["count"], self.sat()["count"])
        self.assertGreaterEqual(self.sat(mask_deg=30)["count"], self.sat(mask_deg=0)["count"])

    def test_an_unreachable_coverage_target_is_capped_and_said_so(self):
        sat = price(area_km2=10, satellite={**SATELLITE, "altitude_km": 100, "beam_deg": 5, "mask_deg": 45})["satellite"]
        self.assertEqual(sat["count"], costmodel.Coefficients().sat_max_count)
        self.assertIn("capped", sat["note"])
        self.assertLess(sat["achieved_pct"], sat["coverage_target_pct"])

    def test_propellant_follows_the_rocket_equation_and_lower_orbits_cost_more_dv(self):
        s = self.sat()
        c = costmodel.Coefficients()
        self.assertAlmostEqual(s["wet_mass_kg"], s["dry_mass_kg"] * 2.718281828459045 ** (s["delta_v_m_s"] / (c.sat_isp_s * costmodel.G0)), places=3)
        self.assertGreater(self.sat(altitude_km=100)["delta_v_m_s"], self.sat(altitude_km=2000)["delta_v_m_s"])

    def test_the_satellite_adds_to_the_surface_cost_without_changing_it(self):
        without = price(area_km2=10)
        with_sat = price(area_km2=10, satellite=SATELLITE)
        self.assertEqual(with_sat["surface_usd"], without["total_usd"])
        self.assertGreater(with_sat["total_usd"], without["total_usd"])


class AdapterTests(TestCase):
    def test_area_nodes_take_link_and_traverse_distances_from_the_layout(self):
        area = area_snapshot()
        nodes = costmodel.area_nodes(area, Mission().model_dump(mode="json"))
        self.assertEqual(len(nodes), 4)
        self.assertGreater(nodes[1]["range_km"], 0)
        self.assertEqual(nodes[0]["route_km"], 0.0)          # the gateway itself
        self.assertGreater(nodes[3]["route_km"], nodes[1]["route_km"])   # further from the gateway, longer traverse
        self.assertAlmostEqual(nodes[0]["battery_wh"], 288)
        self.assertAlmostEqual(nodes[0]["solar_w"], 18)

    def test_estimate_prices_an_area_with_its_drawn_size(self):
        area = area_snapshot()
        result = dict(config=Mission().model_dump(mode="json"), site=area["site"], cost=area["cost"])
        small = costmodel.estimate(result, area, scope="area")
        area["site"]["area_km2"] = 50
        large = costmodel.estimate(result, area, scope="area")
        self.assertGreater(large["total_usd"], small["total_usd"])
        self.assertEqual(small["flat_reference_usd"], 400_000)
        self.assertEqual(small["drivers"]["nodes"], 4)

    def test_a_real_mission_is_priced_from_its_relays(self):
        start = date(2026, 11, 1)
        result = scenario(Mission(start=start, end=start + timedelta(days=3), minutes=60, max_relays=2, avoid_hazard_routes=False), include_map=False)
        report = costmodel.estimate(result, scope="mission", satellite=SATELLITE)
        self.assertEqual(report["drivers"]["nodes"], len(result["recommended"]))
        self.assertEqual(report["drivers"]["mission_days"], 3)
        self.assertAlmostEqual(sum(i["total_usd"] for i in report["items"]), report["total_usd"], places=2)
        self.assertEqual(report["flat_reference_usd"], result["cost"]["total_usd"])


def sheet_names(content):
    with ZipFile(BytesIO(content)) as archive:
        return archive.read("xl/workbook.xml").decode()


def cells(content, index):
    with ZipFile(BytesIO(content)) as archive:
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return {c.attrib["r"]: (c.findtext("s:f", namespaces=ns), c.findtext("s:v", default="", namespaces=ns)) for c in root.findall(".//s:c", ns)}


class ExportAndEndpointTests(TestCase):
    @classmethod
    def setUpClass(cls):
        start = date(2026, 11, 1)
        cls.result = scenario(Mission(start=start, end=start + timedelta(days=3), minutes=60, max_relays=2, avoid_hazard_routes=False), include_map=False)

    def test_the_workbook_breaks_the_dynamic_cost_down(self):
        content = export_workbook(ExportRequest(result=self.result, satellite=costmodel.SatelliteSpec(altitude_km=500)))
        workbook = sheet_names(content)
        for name in ("Dynamic Cost", "Cost by Node", "Cost Drivers", "Cost Coefficients"):
            self.assertIn(f'name="{name}"', workbook)
        dynamic = costmodel.estimate(self.result, scope="mission", satellite=costmodel.SatelliteSpec(altitude_km=500))
        n = len(dynamic["items"])
        formula, cached = cells(content, 3)[f"F{n + 3}"]
        self.assertEqual(formula, f"SUM(F2:F{n + 1})")
        self.assertAlmostEqual(float(cached), dynamic["total_usd"], places=2)
        summary = cells(content, 1)
        self.assertTrue(summary["B3"][0].startswith("'Cost Breakdown'!E"))
        self.assertEqual(summary["B4"][0], f"'Dynamic Cost'!F{n + 3}")
        self.assertAlmostEqual(float(summary["B4"][1]), dynamic["total_usd"], places=2)

    def test_the_workbook_without_a_satellite_still_has_the_dynamic_sheets(self):
        content = export_workbook(ExportRequest(result=self.result))
        self.assertIn('name="Dynamic Cost"', sheet_names(content))

    def test_estimate_endpoint(self):
        client = TestClient(app)
        response = client.post("/api/cost/estimate", json={"result": self.result, "satellite": dict(altitude_km=1200, gravity=1.1)})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["model"], "dynamic")
        self.assertGreater(body["satellite_usd"], 0)
        self.assertEqual(client.post("/api/cost/estimate", json={"result": self.result, "coefficients": {"bogus": 1}}).status_code, 422)
        self.assertEqual(client.post("/api/cost/estimate", json={"result": self.result, "satellite": dict(altitude_km=5)}).status_code, 422)
        self.assertEqual(client.post("/api/cost/estimate", json={"result": {}}).status_code, 422)


class AreaExportTests(TestCase):
    """An area export with a satellite: the node sheet's formulas must add the right columns, and totals must agree with the model."""

    def test_area_workbook_node_rows_sum_their_components(self):
        from tests.test_cost_export import snapshot
        area = area_snapshot()
        content = export_workbook(ExportRequest(result=snapshot(), area=area, scope="area", satellite=costmodel.SatelliteSpec(altitude_km=800)))
        self.assertIn('name="Cost by Node"', sheet_names(content))
        dynamic = costmodel.estimate(snapshot(), area, scope="area", satellite=costmodel.SatelliteSpec(altitude_km=800))
        node_sheet = cells(content, 4)
        self.assertEqual(node_sheet["N2"][0], "SUM(G2:M2)")                       # seven components, columns G to M
        self.assertAlmostEqual(float(node_sheet["N2"][1]), dynamic["nodes"][0]["total_usd"], places=2)
        self.assertEqual(node_sheet[f"N{len(dynamic['nodes']) + 3}"][0], f"SUM(N2:N{len(dynamic['nodes']) + 1})")
        dyn = cells(content, 3)
        n = len(dynamic["items"])
        self.assertEqual(dyn["F2"][0], "ROUND(D2*E2,2)")
        self.assertAlmostEqual(float(dyn[f"F{n + 3}"][1]), dynamic["total_usd"], places=2)
        self.assertGreater(dynamic["drivers"]["area_km2"], 0)
        self.assertIn("Site survey and terrain mapping", [i["item"] for i in dynamic["items"]])
