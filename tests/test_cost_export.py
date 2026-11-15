"""Cost/export regressions without terrain downloads or mission recomputation."""
from io import BytesIO
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
from fastapi.testclient import TestClient

from moonmesh.costs import cost_report, relay_limit
from moonmesh.excel import ExportRequest, export_workbook
from moonmesh.models import Mission
from moonmesh.network import optimize
from moonmesh.server import app


def snapshot(override=None):
    metrics = dict(availability=100, longest=0)
    history = dict(temperature_c=[-10], soc=[0.8], heater_w=[4], dust_load=[0],
                   solar_factor=[1], available=[True], reason=["Available"])
    return dict(
        site=dict(name="Test site"), config=dict(site="test", start="2026-11-01", end="2026-11-02"),
        recommended=["R1", "R2"], cost=cost_report(2, 250000, override),
        direct=metrics, network=metrics, worst=metrics,
        failure_states={key: dict(metrics=metrics, assisted=[True], combined=[True]) for key in ("nominal", "R1")},
        candidates=[dict(id="R1", lat=-87, lon=30, distance=1, coverage=metrics, environment={"nominal": history})],
        outages=[], failures=[], times=["2026-11-01T00:00:00+00:00"],
        samples=dict(direct=[False]), provenance={"Source": "Illustrative test"},
        environment=dict(cases={"nominal": dict(network=metrics, worst=metrics, meets=True)}, lander={"nominal": history}),
    )


def cells(content, sheet):
    with ZipFile(BytesIO(content)) as archive:
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{sheet}.xml"))
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return {c.attrib["r"]: (c.findtext("s:f", namespaces=ns),
                            float(c.findtext("s:v", default="0", namespaces=ns)))
            for c in root.findall(".//s:c", ns)}


class CostTests(unittest.TestCase):
    def test_demo_itemization_and_zero_nodes(self):
        report = cost_report(2, 200000, None)
        self.assertEqual(report["total_usd"], 200000)
        self.assertEqual(sum(i["total_usd"] for i in report["items"]), report["total_usd"])
        self.assertEqual(len(report["items"]), 7)
        self.assertTrue(report["within_budget"])
        self.assertEqual(cost_report(0, None, None)["total_usd"], 0)

    def test_override_rounding_and_budget_boundaries(self):
        report = cost_report(3, 30.03, 10.005)
        self.assertEqual(report["total_usd"], 30.03)
        self.assertEqual(len(report["items"]), 1)
        self.assertEqual(relay_limit(4, 30.02, 10.005), 2)
        self.assertEqual(relay_limit(4, 30.03, 10.005), 3)
        self.assertEqual(relay_limit(4, 0, 0), 4)
        self.assertEqual(cost_report(2, None, 0)["total_usd"], 0)
        self.assertEqual(relay_limit(4, 199999.99, None), 1)
        self.assertEqual(relay_limit(4, 200000, None), 2)

    def test_budget_constrains_recommendation(self):
        mission = Mission(availability=100, blackout_hours=0, budget_usd=99999)
        self.assertEqual(optimize(np.array([False]), [np.array([True])], [0], mission), ((), False))
        mission = mission.model_copy(update={"budget_usd": 100000})
        self.assertEqual(optimize(np.array([False]), [np.array([True])], [0], mission), ((0,), True))

    def test_fixed_network_nodes_reduce_relay_budget(self):
        self.assertEqual(relay_limit(4, 499999, None, fixed_nodes=5), 0)
        self.assertEqual(relay_limit(4, 600000, None, fixed_nodes=5), 1)


class ExportTests(unittest.TestCase):
    def test_formulas_cached_totals_and_failure_cost(self):
        content = export_workbook(ExportRequest(result=snapshot(), disabled=["R1"]))
        costs = cells(content, 2)
        self.assertEqual(costs["E2"], ("ROUND(C2*D2,2)", 40000))
        self.assertEqual(costs["E10"], ("SUM(E2:E8)", 200000))
        self.assertEqual(cells(content, 1)["B3"], ("'Cost Breakdown'!E10", 200000))
        with ZipFile(BytesIO(content)) as archive:
            workbook = archive.read("xl/workbook.xml").decode()
        for name in ("Summary", "Cost Breakdown", "Nodes", "Mission Results", "Assumptions", "Environment"):
            self.assertIn(f'name="{name}"', workbook)

    def test_override_exports_one_bundled_line(self):
        content = export_workbook(ExportRequest(result=snapshot(123.45)))
        self.assertEqual(cells(content, 2)["E4"], ("SUM(E2:E2)", 246.9))

    def test_area_download_uses_area_count(self):
        area = dict(site=dict(name="Drawn area"), plan=dict(chosen=[1, 2, 3]),
                    cost=cost_report(5, None, None),
                    nodes=[dict(id=i, role="relay", lat=-87, lon=i, elevation=0, outside=False) for i in range(5)], layouts={})
        content = export_workbook(ExportRequest(result=snapshot(), area=area, scope="area"))
        self.assertEqual(cells(content, 1)["B3"][1], 500000)

    def test_download_endpoint_and_bad_snapshots(self):
        client = TestClient(app)
        response = client.post("/api/export/xlsx", json={"result": snapshot()})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"PK"))
        self.assertIn("spreadsheetml", response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(client.post("/api/export/xlsx", json={"result": {}}).status_code, 422)
        self.assertEqual(client.post("/api/export/xlsx", json={"result": snapshot(), "disabled": ["unknown"]}).status_code, 422)

    def test_labels_cannot_become_excel_formulas(self):
        result = snapshot()
        result["site"]["name"] = "=1+1"
        content = export_workbook(ExportRequest(result=result))
        with ZipFile(BytesIO(content)) as archive:
            xml = archive.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("=1+1", xml)
        self.assertNotIn("<f>1+1</f>", xml)
