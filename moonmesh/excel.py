"""Excel reports from the applied browser snapshot; no mission recomputation."""
from datetime import UTC, datetime
from io import BytesIO
import json
from typing import Literal

from pydantic import BaseModel, Field
import xlsxwriter

from . import costmodel
from .costs import cost_report


class ExportRequest(BaseModel):
    result: dict
    area: dict | None = None
    disabled: list[str] = Field(default_factory=list)
    scope: Literal["mission", "area"] = "mission"
    comparison: list[dict] | None = None
    sensitivity: list[dict] | None = None
    satellite: costmodel.SatelliteSpec | None = None      # priced as a relay-satellite segment when given
    coefficients: dict[str, float] | None = None          # overrides for the dynamic cost model


DRIVER_LABELS = [
    ("nodes", "Installed nodes", "Every node is priced from its own conditions below."),
    ("gateways", "Gateways", "Gateways carry the Earth-facing link and cost more communications equipment."),
    ("mines", "Mining-site nodes", "Priced like any other node."),
    ("longest_link_km", "Longest link (km)", "Sets the radio and antenna size of the nodes at either end."),
    ("total_route_km", "Total traverse (km)", "Summed distance nodes are carried from the lander or nearest gateway."),
    ("area_km2", "Area (km2)", "Drives the site survey. Empty for a mission recommendation, which has no drawn area."),
    ("mission_days", "Mission length (days)", "Drives contingency and satellite operations."),
    ("contingency_pct", "Contingency (%)", "Grows with mission length and with steep or unverified routes."),
]
SATELLITE_LABELS = [
    ("radius_km", "Orbital radius (km)", "Lunar radius plus altitude.", "{:,.1f}"),
    ("speed_km_s", "Orbital speed (km/s)", "sqrt(mu / r) for the chosen gravity.", "{:.3f}"),
    ("period_min", "Orbital period (min)", "2 pi sqrt(r^3 / mu).", "{:,.1f}"),
    ("gravity", "Gravity (x lunar)", "1 is the real Moon; other values are what-ifs.", "{:g}"),
    ("inclination_deg", "Inclination at the area (deg)", "From the track heading and the area's latitude.", "{:.1f}"),
    ("access_half_arc_deg", "Ground half-arc in view (deg)", "Central angle within which the satellite is above the elevation mask and inside the beam.", "{:.1f}"),
    ("duty_pct", "Best-case time in view per satellite (%)", "Half-arc divided by 180 deg, for a track passing overhead.", "{:.1f}"),
    ("max_range_km", "Longest slant range (km)", "Distance to a node at the elevation mask; sets the payload size factor.", "{:,.0f}"),
    ("range_factor", "Payload size factor", "Slant range over the reference range, between 0.5 and 4.", "{:.2f}"),
    ("coverage_target_pct", "Coverage target (%)", "Share of time you want the area reached.", "{:g}"),
    ("count", "Satellites", "Target divided by one satellite's time in view, rounded up, capped.", "{:d}"),
    ("achieved_pct", "Coverage achieved (%)", "Satellites times time in view, at most 100.", "{:.1f}"),
    ("delta_v_arrival_m_s", "Orbit insertion dv (m/s)", "sqrt(v_approach^2 + 2 mu / r) - sqrt(mu / r).", "{:,.0f}"),
    ("delta_v_keeping_m_s", "Station-keeping dv (m/s)", "Yearly allowance scaled by 500 km / altitude and the mission length.", "{:,.0f}"),
    ("delta_v_m_s", "Total dv (m/s)", "Insertion plus station-keeping.", "{:,.0f}"),
    ("dry_mass_kg", "Dry mass (kg)", "Bus plus payload scaled by the size factor.", "{:,.0f}"),
    ("propellant_kg", "Propellant (kg)", "Rocket equation: dry mass x (exp(dv / (Isp g0)) - 1).", "{:,.0f}"),
    ("wet_mass_kg", "Mass to deliver (kg)", "Dry mass plus propellant, priced per kilogram delivered.", "{:,.0f}"),
]


def write_dynamic_sheets(sheet, table, cell, heading, money, editable, wrapped, percent, total_format, bold, dynamic, flat):
    """Dynamic Cost (itemised, with formulas), Cost by Node, Cost Drivers and Cost Coefficients."""
    items = dynamic["items"]
    n = len(items)
    tab = sheet("Dynamic Cost", [16, 46, 62, 10, 18, 20])
    tab.write_row(0, 0, ["Category", "Item", "Driver", "Quantity", "Unit cost (USD)", "Total (USD)"], heading)
    tab.set_row(0, 30)
    for row, item in enumerate(items, 1):
        tab.write_string(row, 0, item["category"])
        tab.write_string(row, 1, item["item"])
        tab.write_string(row, 2, item["driver"])
        tab.write_number(row, 3, item["quantity"])
        tab.write_number(row, 4, item["unit_cost_usd"], editable)
        tab.write_formula(row, 5, f"=ROUND(D{row + 1}*E{row + 1},2)", money, item["total_usd"])
    tab.autofilter(0, 0, n, 5)
    total_row = n + 2
    tab.write(total_row, 1, "Total dynamic cost", heading)
    tab.write_formula(total_row, 5, f"=SUM(F2:F{n + 1})", total_format, dynamic["total_usd"])
    row = total_row + 2
    tab.write_row(row, 0, ["Category", "", "", "", "", "Subtotal (USD)"], heading)
    for category, value in dynamic["categories"].items():
        row += 1
        tab.write_string(row, 0, category)
        tab.write_formula(row, 5, f"=SUMIF($A$2:$A${n + 1},A{row + 1},$F$2:$F${n + 1})", money, value)
    row += 2
    tab.write_string(row, 1, "Flat estimate (same price for every node)")
    tab.write_number(row, 5, flat["total_usd"], money)
    tab.write_string(row + 1, 1, "Dynamic minus flat")
    tab.write_formula(row + 1, 5, f"=F{total_row + 1}-F{row + 1}", money, dynamic["total_usd"] - flat["total_usd"])
    if dynamic["budget_usd"] is not None:
        tab.write_string(row + 2, 1, "Budget")
        tab.write_number(row + 2, 5, dynamic["budget_usd"], money)
        tab.write_string(row + 3, 1, "Remaining after the dynamic estimate")
        tab.write_formula(row + 3, 5, f"=F{row + 3}-F{total_row + 1}", money, dynamic["remaining_usd"])
    tab.write_string(row + 5, 1, "Blue unit prices are editable; totals and subtotals update. Coefficients are on the Cost Coefficients sheet.")

    # One row per node, one column per component, so cost differences between nodes are visible.
    if dynamic["nodes"]:
        components = [p["component"] for p in dynamic["nodes"][0]["parts"]]
        by_node = sheet("Cost by Node", [14, 10, 14, 14, 12, 14] + [18] * len(components) + [20])
        first = 6
        last = first + len(components) - 1
        columns = ["Node", "Role", "Longest link (km)", "Traverse (km)", "Slope (deg)", "Route check", *components, "Node total (USD)"]
        by_node.write_row(0, 0, columns, heading)
        by_node.set_row(0, 42)
        for row, node in enumerate(dynamic["nodes"], 1):
            for col, value in enumerate([node["id"], node["role"], node["range_km"], node["route_km"], node["slope_deg"], node["verdict"] or "not checked"]):
                cell(by_node, row, col, round(value, 3) if isinstance(value, float) else value)
            for col, part in enumerate(node["parts"], first):
                by_node.write_number(row, col, part["usd"], money)
            letters = (chr(ord("A") + first), chr(ord("A") + last))
            by_node.write_formula(row, last + 1, f"=SUM({letters[0]}{row + 1}:{letters[1]}{row + 1})", money, node["total_usd"])
        end = len(dynamic["nodes"]) + 2
        by_node.write(end, 5, "Total", heading)
        for col in range(first, last + 2):
            letter = chr(ord("A") + col)
            cached = sum(node["parts"][col - first]["usd"] for node in dynamic["nodes"]) if col <= last else sum(node["total_usd"] for node in dynamic["nodes"])
            by_node.write_formula(end, col, f"=SUM({letter}2:{letter}{len(dynamic['nodes']) + 1})", total_format, cached)
        by_node.write_string(end + 2, 0, "Programme costs (survey, mobilisation, engineering, contingency) and the satellite segment are on the Dynamic Cost sheet.")

    drivers = sheet("Cost Drivers", [44, 22, 100])
    drivers.set_column(2, 2, 100, wrapped)
    row = table(drivers, 0, ["Driver", "Value", "What it does"], (
        [label, dynamic["drivers"].get(key), meaning] for key, label, meaning in DRIVER_LABELS))
    sat = dynamic["satellite"]
    if sat:
        row = table(drivers, row, ["Relay satellite", "Value", "How it is derived"], (
            [label, fmt.format(round(sat[key]) if fmt == "{:d}" else sat[key]), meaning] for key, label, meaning, fmt in SATELLITE_LABELS))
        drivers.write_string(row - 2, 0, "Note")
        drivers.write_string(row - 2, 2, sat["note"])
    else:
        drivers.write_string(row, 0, "No relay satellite is included in this estimate.")

    coeffs = sheet("Cost Coefficients", [34, 18, 100])
    coeffs.set_column(2, 2, 100, wrapped)
    table(coeffs, 0, ["Coefficient", "Value", "Meaning"], (
        [key, value, dynamic["coefficient_notes"].get(key, "")] for key, value in dynamic["coefficients"].items()))


def export_workbook(request: ExportRequest) -> bytes:
    result, area = request.result, request.area
    if request.scope == "area" and not area:
        raise ValueError("No applied area layout is available.")
    source = area if request.scope == "area" else result
    if not isinstance(source.get("cost"), dict):
        raise ValueError("The applied result has no cost estimate. Recalculate the network first.")
    if request.scope == "area":
        count = len(area["nodes"])
    else:
        count = len(result["recommended"])
    original = source["cost"]
    override = None if original.get("pricing_basis") == "illustrative" else original["relay_cost_usd"]
    cost = cost_report(count, original.get("budget_usd"), override)
    try:
        dynamic = costmodel.estimate(result, area, scope=request.scope, satellite=request.satellite, overrides=request.coefficients)
    except (KeyError, TypeError, IndexError, ZeroDivisionError):
        dynamic = None   # a sparse snapshot may only have a flat estimate
    except ValueError as error:
        if "coefficient" in str(error):
            raise
        dynamic = None
    state_key = ",".join(sorted(set(request.disabled))) or "nominal"
    state = result["failure_states"].get(state_key)
    if state is None:
        raise ValueError("The selected failure state is not part of this applied result.")

    output = BytesIO()
    with xlsxwriter.Workbook(output, {"constant_memory": True, "strings_to_formulas": False,
                                     "strings_to_urls": False}) as book:
        heading = book.add_format({"bold": True, "bg_color": "#17384B", "font_color": "white", "text_wrap": True})
        money = book.add_format({"num_format": '"$"#,##0.00'})
        editable = book.add_format({"num_format": '"$"#,##0.00', "font_color": "#1557B0"})
        wrapped = book.add_format({"text_wrap": True, "valign": "top"})
        date_format = book.add_format({"num_format": "yyyy-mm-dd hh:mm"})
        title = book.add_format({"bold": True, "font_size": 16, "font_color": "#17384B"})
        title_small = book.add_format({"bold": True, "font_color": "#17384B"})
        percent = book.add_format({"num_format": "0.0"})
        total_format = book.add_format({"bold": True, "num_format": '"$"#,##0.00', "top": 1})

        def sheet(name, widths):
            tab = book.add_worksheet(name)
            tab.hide_gridlines(2)
            tab.freeze_panes(1, 0)
            for col, width in enumerate(widths):
                tab.set_column(col, col, width)
            return tab

        def cell(tab, row, col, value):
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            if isinstance(value, str):
                tab.write_string(row, col, value[:32767])
            elif value is not None:
                tab.write(row, col, value)

        def table(tab, row, columns, records):
            tab.write_row(row, 0, columns, heading)
            tab.set_row(row, 30)
            row += 1
            for record in records:
                for col, value in enumerate(record):
                    cell(tab, row, col, value)
                row += 1
            return row + 2

        summary = sheet("Summary", [36, 85])
        summary.write(0, 0, "MOONMESH network installation", title)
        summary.write(2, 0, "Flat estimate (same price for every node)")
        summary.write_number(2, 1, cost["total_usd"], money)
        if dynamic:
            dynamic_total_row = len(dynamic["items"]) + 3   # one-based row of the total on Dynamic Cost
            summary.write(3, 0, "Dynamic estimate (priced from this scenario)", title_small)
            summary.write_formula(3, 1, f"='Dynamic Cost'!F{dynamic_total_row}", money, dynamic["total_usd"])
        else:
            summary.write(3, 0, "Dynamic estimate")
            summary.write(3, 1, "Not available for this snapshot.")
        summary.set_column(1, 1, 85, wrapped)
        summary.set_row(13, 72)
        summary.set_row(14, 42)
        if dynamic:
            summary.set_row(17, 96)
        table(summary, 4, ["Applied scenario", "Value"], [
            ["Cost scope", "Area network" if request.scope == "area" else "Mission relay recommendation"],
            ["Site", source["site"]["name"]],
            ["Installed network node count", count],
            ["Mission start (UTC)", result["config"]["start"]],
            ["Mission end (UTC)", result["config"]["end"]],
            ["Generated (UTC)", datetime.now(UTC).isoformat()],
            ["Simulated disabled mission relays", ", ".join(request.disabled) or "None"],
            ["Pricing basis", cost["pricing_basis"]],
            ["Scope and exclusions", cost["assumptions"]],
            *([["Editing prices", "Edit blue unit prices on Dynamic Cost to recalculate the dynamic total."],
               ["Dynamic estimate vs flat at export", f"${dynamic['total_usd'] - cost['total_usd']:+,.2f} ({(dynamic['total_usd'] / cost['total_usd'] - 1) * 100:+.1f}%)"
                if cost["total_usd"] else "No flat estimate to compare"],
               ["Relay satellite", (f"{dynamic['satellite']['count']} satellite(s) at {dynamic['satellite']['radius_km'] - costmodel.MOON_RADIUS_M / 1000:,.0f} km, "
                                    f"{dynamic['satellite']['speed_km_s']:.3f} km/s: ${dynamic['satellite_usd']:,.2f}")
                if dynamic["satellite"] else "Not included. Add one from the Cost tab to price it."],
               ["Dynamic model", dynamic["assumptions"]]] if dynamic else []),
        ])

        if dynamic:
            write_dynamic_sheets(sheet, table, cell, heading, money, editable, wrapped, percent, total_format, title_small, dynamic, cost)

        nodes = sheet("Nodes", [22, 20, 20, 20, 24, 24, 24])
        row = table(nodes, 0, ["Mission relay", "Latitude", "Longitude east", "Distance (km)", "Coverage (%)", "Selected", "Simulated disabled"], (
            [c["id"], c["lat"], c["lon"], c["distance"], c["coverage"]["availability"],
             c["id"] in result["recommended"], c["id"] in request.disabled] for c in result["candidates"]
        ))
        if area:
            table(nodes, row, ["Area node", "Role", "Latitude", "Longitude east", "Elevation (m)", "Chosen relay", "Outside area"], (
                [n["id"], n["role"], n["lat"], n["lon"], n["elevation"], n["id"] in area["plan"]["chosen"], n["outside"]]
                for n in area["nodes"]
            ))

        results = sheet("Mission Results", [24, 28, 28, 28, 28, 28, 30])
        metrics = [("Direct", result["direct"]), ("Recommended network", result["network"]),
                   ("Active failure simulation", state["metrics"]), ("Worst single failure", result["worst"])]
        row = table(results, 0, ["Network", "Availability (%)", "Longest blackout (h)"], (
            [name, m["availability"], m["longest"]] for name, m in metrics
        ))
        row = table(results, row, ["Direct blackout start (UTC)", "End (UTC)", "Duration (h)", "Causes"], (
            [o["start"], o["end"], o["hours"], o["causes"]] for o in result["outages"]
        ))
        row = table(results, row, ["Failed relay", "Availability (%)", "Longest blackout (h)", "Meets limits"], (
            [f["id"], f["metrics"]["availability"], f["metrics"]["longest"], f["meets"]] for f in result["failures"]
        ))
        if area:
            row = table(results, row, ["Area layout", "Links", "Link distance (km)", "Worst node loss served", "Worst link loss served"], (
                [m["name"], m["links"], m["link_km"], m["worst_after_node_loss"], m["worst_after_link_loss"]]
                for m in area["layouts"].values()
            ))
        for label, records in (("Site comparison", request.comparison), ("Sensitivity", request.sensitivity)):
            if records:
                row = table(results, row, [label, "Result"], ([r.get("label", r.get("site", {}).get("name", "")), r] for r in records))
        row = table(results, row, ["Timestamp (UTC)", "Direct online", "Active relay online", "Active network online"], []) - 2
        for i, timestamp in enumerate(result["times"]):
            results.write_datetime(row, 0, datetime.fromisoformat(timestamp).replace(tzinfo=None), date_format)
            results.write_row(row, 1, [result["samples"]["direct"][i], state["assisted"][i], state["combined"][i]])
            row += 1

        if result["candidates"] and "limiting" in result["candidates"][0]:
            diag = sheet("Relay Diagnostics", [12, 10, 16, 14, 12, 12, 14, 14, 12, 70, 16, 14, 14, 14, 12, 16, 70])
            row = table(diag, 0, ["Relay", "Selected", "Main limit", "Terrain blocks (%)", "RF blocks (%)", "Power blocks (%)", "Temperature blocks (%)",
                                  "Missing data (%)", "Lander (%)", "Why", "Traverse", "Straight (km)", "Terrain route (km)", "Steepest slope (deg)",
                                  "Steep share (%)", "Longest steep (m)", "Traverse note"], (
                [c["id"], c["id"] in result["recommended"], c["limiting"]["primary"] or "none", *(c["limiting"]["blocked_pct"][k] for k in
                  ("terrain", "rf", "power", "temperature", "data", "lander")), c["limiting"]["summary"], c["deployment"]["verdict"],
                 c["deployment"]["straight_km"], c["deployment"]["route_km"], c["deployment"]["max_slope"], c["deployment"]["steep_pct"],
                 c["deployment"]["longest_steep_m"], c["deployment"]["reason"]] for c in result["candidates"]))
            loss = result.get("power_loss")
            if loss:
                table(diag, row, ["Relay power loss", "Hours dark", "Start (UTC)", "End (UTC)", "Connected meanwhile (%)", "Availability (%)",
                                  "Longest blackout (h)", "Meets limits", "Note"], (
                    [r["id"], r["hours_off"], r.get("start"), r.get("end"), r.get("connected_during_pct"),
                     (r.get("metrics") or {}).get("availability"), (r.get("metrics") or {}).get("longest"), r.get("meets"), r["note"]]
                    for r in loss["relays"]))

        assumptions = sheet("Assumptions", [36, 110])
        assumptions.set_column(1, 1, 110, wrapped)
        assumptions.set_row(1, 60)
        row = table(assumptions, 0, ["Cost assumptions", "Value"], [["Pricing and exclusions", cost["assumptions"]]])
        row = table(assumptions, row, ["Applied configuration", "Value"], result["config"].items())
        table(assumptions, row, ["Provenance", "Source"], result["provenance"].items())

        environment = result.get("environment")
        if environment:
            tab = sheet("Environment", [24, 20, 24, 22, 18, 18, 18, 18, 18, 30])
            row = table(tab, 0, ["Case", "Network coverage (%)", "Longest blackout (h)", "Worst failure coverage (%)", "Meets limits"], (
                [case, c["network"]["availability"], c["network"]["longest"], c["worst"]["availability"], c["meets"]]
                for case, c in environment["cases"].items()
            ))
            row = table(tab, row, ["Node", "Case", "Timestamp (UTC)", "Temperature (C)", "Charge fraction", "Heater (W)", "Dust load", "Solar factor", "Available", "Reason"], []) - 2
            selected = [{"id": "lander", "environment": environment["lander"]}] + [c for c in result["candidates"] if c["id"] in result["recommended"]]
            for node in selected:
                for case, history in node["environment"].items():
                    for i, timestamp in enumerate(result["times"]):
                        tab.write_string(row, 0, node["id"])
                        tab.write_string(row, 1, case)
                        tab.write_datetime(row, 2, datetime.fromisoformat(timestamp).replace(tzinfo=None), date_format)
                        for col, key in enumerate(("temperature_c", "soc", "heater_w", "dust_load", "solar_factor", "available", "reason"), 3):
                            cell(tab, row, col, history[key][i])
                        row += 1
    return output.getvalue()
