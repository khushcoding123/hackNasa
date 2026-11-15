"""Excel reports from the applied browser snapshot; no mission recomputation."""
from datetime import UTC, datetime
from io import BytesIO
import json
from typing import Literal

from pydantic import BaseModel, Field
import xlsxwriter

from .costs import cost_report
from .network import metrics as timeline_metrics


class ExportRequest(BaseModel):
    result: dict
    area: dict | None = None
    disabled: list[str] = Field(default_factory=list)
    scope: Literal["mission", "area"] = "mission"
    comparison: list[dict] | None = None
    sensitivity: list[dict] | None = None
    environment_case: Literal['nominal', 'cold', 'dust', 'cold_dust'] = 'cold_dust'
    preview_config_id: str | None = None
    outage_scope: str = 'network'


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
    state_key = ",".join(sorted(set(request.disabled))) or "nominal"
    state = result["failure_states"].get(state_key)
    if state is None:
        raise ValueError("The selected failure state is not part of this applied result.")
    case_state = state.get('cases', {}).get(request.environment_case)
    if case_state is not None:
        state = {**state, **case_state}
    optimization = result.get('optimization_evidence')
    if request.preview_config_id and (not optimization or request.preview_config_id not in optimization['configurations']):
        raise ValueError('The alternative preview is not part of this applied result.')

    diagnostic = result.get('diagnostics')
    if request.outage_scope != 'network':
        path = (diagnostic or {}).get('paths', {}).get(request.outage_scope)
        if path is None or (path['relay'] is not None and path['relay'] not in result['recommended']):
            raise ValueError('The outage scope is not a selected path in this applied result.')

    output = BytesIO()
    with xlsxwriter.Workbook(output, {"constant_memory": True, "strings_to_formulas": False,
                                     "strings_to_urls": False}) as book:
        heading = book.add_format({"bold": True, "bg_color": "#17384B", "font_color": "white", "text_wrap": True})
        money = book.add_format({"num_format": '"$"#,##0.00'})
        editable = book.add_format({"num_format": '"$"#,##0.00', "font_color": "#1557B0"})
        wrapped = book.add_format({"text_wrap": True, "valign": "top"})
        date_format = book.add_format({"num_format": "yyyy-mm-dd hh:mm"})
        title = book.add_format({"bold": True, "font_size": 16, "font_color": "#17384B"})

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

        total_row = len(cost["items"]) + 2  # zero-based row on Cost Breakdown
        summary = sheet("Summary", [36, 85])
        summary.write(0, 0, "MOONMESH network installation", title)
        summary.write(2, 0, "Total estimated installation cost")
        summary.write_formula(2, 1, f"='Cost Breakdown'!E{total_row + 1}", money, cost["total_usd"])
        summary.set_column(1, 1, 85, wrapped)
        summary.set_row(13, 72)
        summary.set_row(14, 42)
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
            ["Editing prices", "Blue unit prices are editable. Workbook totals update; website recommendations do not."],
        ])

        costs = sheet("Cost Breakdown", [22, 52, 14, 22, 24])
        costs.write_row(0, 0, ["Category", "Item", "Quantity", "Unit cost (USD)", "Total (USD)"], heading)
        costs.set_row(0, 30)
        for row, item in enumerate(cost["items"], 1):
            costs.write_string(row, 0, item["category"])
            costs.write_string(row, 1, item["item"])
            costs.write_number(row, 2, item["quantity"])
            costs.write_number(row, 3, item["unit_cost_usd"], editable)
            costs.write_formula(row, 4, f"=ROUND(C{row+1}*D{row+1},2)", money, item["total_usd"])
        costs.autofilter(0, 0, len(cost["items"]), 4)
        costs.write(total_row, 1, "Total installation cost", heading)
        costs.write_formula(total_row, 4, f"=SUM(E2:E{len(cost['items'])+1})", money, cost["total_usd"])
        costs.write(total_row + 2, 1, "Pricing basis")
        costs.write(total_row + 2, 2, cost["pricing_basis"])
        costs.write(total_row + 3, 1, "No launch or ongoing operations costs included.")

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
        selected_case = result.get('environment', {}).get('cases', {}).get(request.environment_case, {})
        metrics = [("Lander paths (selected case)", timeline_metrics(case_state['direct'], result['config']['minutes']/60) if case_state else result['direct']),
                   ("Recommended network (selected case)", selected_case.get('network', result['network'])),
                   ("Active failure simulation", state["metrics"]),
                   ("Worst single failure (selected case)", selected_case.get('worst', result['worst']))]
        row = table(results, 0, ["Network", "Availability (%)", "Longest blackout (h)"], (
            [name, m["availability"], m["longest"]] for name, m in metrics
        ))
        row = table(results, row, ["Active network blackout start (UTC)", "End (UTC)", "Duration (h)", "Causes"], (
            [o["start"], o["end"], o["hours"], o["causes"]] for o in state.get('outages', result["outages"])
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
            results.write_row(row, 1, [state.get('direct', result["samples"]["direct"])[i], state["assisted"][i], state["combined"][i]])
            row += 1

        assumptions = sheet("Assumptions", [36, 110])
        assumptions.set_column(1, 1, 110, wrapped)
        assumptions.set_row(1, 60)
        row = table(assumptions, 0, ["Cost assumptions", "Value"], [["Pricing and exclusions", cost["assumptions"]]])
        row = table(assumptions, row, ["Applied configuration", "Value"], result["config"].items())
        table(assumptions, row, ["Provenance", "Source"], result["provenance"].items())

        diagnostic = result.get('diagnostics')
        if diagnostic:
            report = sheet('Network Outages', [32, 32, 20, 90])
            row = table(report, 0, ['Simulated evidence', 'Value'], [
                ['Environmental case', request.environment_case], ['Disabled nodes', sorted(set(request.disabled))],
                ['Interval convention', diagnostic['interval_convention']], ['Timing', diagnostic['timing']],
                ['Sample minutes', diagnostic['sample_minutes']],
                ['Alternative preview (nominal only)', request.preview_config_id]])
            table(report, row, ['Start UTC', 'End UTC (exclusive)', 'Hours', 'Conditions across interval'], (
                [o['start'], o['end'], o['hours'], o['causes']] for o in state.get('outages', [])))
            scoped_outages = state.get('outages', []) if request.outage_scope == 'network' else state.get('path_outages', {}).get(request.outage_scope, [])
            causes = sheet('Outage Causes', [30, 30, 20, 40, 32, 26, 20])
            cause_row = table(causes, 0, ['Simulated evidence', 'Value'], [['Outage scope', request.outage_scope], ['Environmental case', request.environment_case]])
            table(causes, cause_row, ['Segment start UTC', 'Segment end UTC', 'Hours', 'Path', 'Component', 'Condition', 'State'], (
                [segment['start'], segment['end'], segment['hours'], path['id'], issue['component'], issue['code'], issue['status']]
                for outage in scoped_outages for segment in outage['segments']
                for path in segment['paths'] for issue in path['issues']))
            paths = [p for p in diagnostic['paths'].values() if p['relay'] is None or p['relay'] in result['recommended']]
            links = sorted({link for path in paths for link in path['links']})
            nodes_in_paths = sorted({node for path in paths for node in path['nodes']})
            link_sheet = sheet('Link Evidence', [28, 28, 35, 55, 55, 55, 75])
            def link_rows():
                for link_id in links:
                    link = diagnostic['links'][link_id]
                    for i, timestamp in enumerate(result['times']):
                        horizon = link.get('horizon')
                        location = link.get('location')
                        if horizon:
                            ray_indices = {horizon['left'][i]}
                            if horizon['weight'][i] != 0:
                                ray_indices.add(horizon['right'][i])
                            location = [horizon['rays'][j] for j in sorted(ray_indices)]
                        yield [link_id, timestamp, link['nodes'],
                            {g['code']: g['ok'][i] for g in link['gates']},
                            {g['code']: dict(value=None if g['values'] is None else g['values'][i], unit=g['unit']) for g in link['gates']},
                            {g['code']: g['threshold'] for g in link['gates']}, location or link.get('todo')]
            table(link_sheet, 0, ['Link', 'Timestamp UTC', 'Endpoints', 'Gate states (null = unknown)', 'Modeled values / units', 'Thresholds', 'Sampled location (approximate) / TODO'], link_rows())
            node_sheet = sheet('Node Evidence', [24, 28, 24, 55, 55, 45])
            table(node_sheet, 0, ['Node', 'Timestamp UTC', 'Case', 'Gate states (null = unknown)', 'Modeled values / units', 'Location'], (
                [node_id, timestamp, request.environment_case,
                 {g['code']: g['ok'][i] for g in node['cases'][request.environment_case]['gates']},
                 {g['code']: dict(value=None if g['values'] is None else g['values'][i], unit=g['unit']) for g in node['cases'][request.environment_case]['gates']},
                 dict(lat=node['lat'], lon=node['lon'])]
                for node_id in nodes_in_paths for node in [diagnostic['nodes'][node_id]] if 'cases' in node
                for i, timestamp in enumerate(result['times'])))
        if optimization:
            tab = sheet('Relay Alternatives', [30, 24, 24, 24, 24, 24, 24, 24, 65])
            row = table(tab, 0, ['Search evidence (simulated)', 'Value'], [
                ['Objective', optimization['objective']], ['Scope', optimization['scope']],
                ['Tie policy', optimization['tie_policy']], ['Tie count', optimization['tie_count']],
                ['Evaluated', optimization['evaluated']], ['Selected', optimization['selected']],
                ['Preview', request.preview_config_id], ['Screening', optimization['screening']]])
            table(tab, row, ['Configuration', 'Case', 'Relay count', 'Network %', 'Total blackout h', 'Longest blackout h', 'Worst loss %', 'Worst loss blackout h', 'Installed USD', 'Constraint violations', 'Comparison explanation'], (
                [c['id'], case, len(c['relay_ids']), report['network']['availability'], report['network']['total'], report['network']['longest'],
                 report['worst']['availability'], report['worst']['longest'], c['total_usd'], c['violations'], c.get('comparison_reason')]
                for c in optimization['configurations'].values() for case, report in c['cases'].items()))
            tab = sheet('Marginal Gains', [24, 28, 28, 28, 28, 30])
            table(tab, 0, ['Relay', 'Case', 'Without relay configuration', 'Coverage gain pp', 'Longest blackout reduction h', 'Recovered h', 'Best replacement configuration'], (
                [m['relay_id'], case, m['without'], values['availability_gain_pp'], values['blackout_reduction_hours'], values['recovered_hours'], m['replacement']]
                for m in optimization['marginal']
                for case, values in m.get('cases', {'cold_dust': m}).items()))

        satellite = result.get("satellite")
        if satellite:
            tab = sheet("Satellite (simulated)", [38, 24, 24, 24, 24, 24])
            elements = satellite["elements"]
            row = table(tab, 0, ["Simulated satellite", "Value"], [
                ["Status", "simulated: two-body orbit, not a flight orbit"], ["Preset", satellite["preset"]],
                ["Period (min)", satellite["period_min"]], ["Minimum elevation (deg)", satellite["min_elevation_deg"]],
                *[[k, v] for k, v in elements.items()],
                ["Surface-to-satellite RF evaluated", satellite["rf_evaluated"]["surface_to_sat"]],
                ["Satellite-to-Earth RF evaluated", satellite["rf_evaluated"]["sat_to_earth"]],
            ])
            row = table(tab, row, ["Hop", "Availability (%)", "Longest outage (h)", "Outage total (h)", "Intervals"], (
                [name, m["availability"], m["longest"], m["total"], m["intervals"]] for name, m in (
                    ("Satellite to Earth (shared)", satellite["earth_hop"]), ("Lander to satellite", satellite["lander_access"]),
                    ("Lander to satellite to Earth", satellite["lander_path"]),
                    ("Network with satellite", result["network"]), ("Network if satellite lost", satellite["network_without_satellite"]))
            ))
            row = table(tab, row, ["Mission relay", "Relay-to-satellite availability (%)", "Longest outage (h)"], (
                [c["id"], c["satellite"]["access"]["availability"], c["satellite"]["access"]["longest"]]
                for c in result["candidates"] if "satellite" in c
            ))
            row = table(tab, row, ["Assumption / gap", "Value"], [
                ["Validation", satellite.get("validation")], ["Operations", satellite.get("assumptions")],
                ["Satellite cost USD", satellite.get("cost_usd")], ["Cost TODO", satellite.get("cost_todo")]])
            row = table(tab, row, ["Satellite path blackout start", "End", "Hours"], (
                [o["start"], o["end"], o["hours"]] for o in satellite.get("outages", [])))
            row = table(tab, row, ["Timestamp (UTC)", "Node", "Latitude (deg)", "Longitude (deg)", "Altitude (km)",
                "Surface margin (dB)", "Sat elevation (deg)", "Sat range (km)", "Surface-sat margin (dB)",
                "Earth occulted", "Sat-Earth range (km)", "Sat-Earth margin (dB)", "Surface-sat state", "Sat-Earth state"], []) - 2
            nodes = [("Lander", satellite["lander"], None)] + [
                (c["id"], c["satellite"], c["surface_margin"]) for c in result["candidates"]
                if c["id"] in result["recommended"] and "satellite" in c]
            for i, timestamp in enumerate(result["times"]):
                for name, hop, surface_margin in nodes:
                    tab.write_datetime(row, 0, datetime.fromisoformat(timestamp).replace(tzinfo=None), date_format)
                    earth = satellite["earth"]
                    tab.write_row(row, 1, [name, satellite["track"]["lat"][i], satellite["track"]["lon"][i],
                        satellite["track"]["altitude_km"][i], surface_margin, hop["el"][i], hop["range_km"][i],
                        None if hop["margin"] is None else hop["margin"][i], earth["occulted"][i], earth["range_km"][i],
                        None if earth["margin"] is None else earth["margin"][i], hop.get("reason", [""]*len(result["times"]))[i],
                        earth.get("reason", [""]*len(result["times"]))[i]])
                    row += 1

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
