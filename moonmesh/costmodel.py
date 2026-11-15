"""A dynamic cost model: what a given network costs, from what it is made of, instead of one price for every node.

The flat estimate in ``costs.py`` prices every node the same. This model prices each node from its own conditions:

- how many nodes there are, and their role (a gateway carries the Earth link and costs more to build);
- how far each node's longest link and its deployment traverse are (longer links need stronger radios and antennas;
  a longer traverse costs more to install; steep or unverified ground costs more again);
- how tall the mast is, how much battery, solar and heater the node needs, and how cold it gets;
- how big the area is (site survey) and how long the mission lasts (contingency);
- optionally, a relay satellite: its altitude (orbital radius), its speed (which follows from altitude and gravity),
  the propellant needed to enter and hold that orbit, the antenna reach, and how many satellites it takes to cover the
  area for the share of time you ask for.

Every number below is an ILLUSTRATIVE planning coefficient, not a quote or a market price. They are all listed in the
report (and in the Excel workbook's Coefficients sheet), and any of them can be overridden per request. What the model
adds over the flat figure is how the cost moves when the scenario changes, not a more certain total.
"""
from dataclasses import asdict, dataclass, fields, replace
from datetime import date

from pydantic import BaseModel, Field
from math import acos, asin, ceil, cos, degrees, exp, pi, radians, sin, sqrt

MOON_RADIUS_M = 1_737_400
MU = 4902.800118e9   # JPL lunar GM, m^3/s^2
G0 = 9.80665


@dataclass(frozen=True)
class Coefficients:
    # Reference node: the flat-price node, so a reference-condition node costs the same as the flat estimate.
    structure_usd: float = 20_000
    radios_usd: float = 20_000
    antennas_usd: float = 10_000
    batteries_usd: float = 15_000
    solar_usd: float = 10_000
    thermal_usd: float = 10_000
    install_usd: float = 15_000
    ref_mast_m: float = 5.0
    mast_exponent: float = 1.5             # taller masts cost more than linearly (loads, guying, mass)
    ref_range_km: float = 2.0
    rf_exponent: float = 0.5               # radio and antenna cost grow with the square root of link length
    rf_floor: float = 0.75
    rf_cap: float = 3.0
    gateway_comms_uplift: float = 0.40     # a gateway also carries the Earth-facing link
    ref_battery_wh: float = 288.0          # 48 h at 6 W
    ref_solar_w: float = 18.0
    ref_heater_w: float = 4.0
    thermal_floor: float = 0.5             # thermal cost = floor + (1 - floor) * heater / reference heater
    install_per_route_km: float = 0.12     # installation grows 12% per km of traverse
    install_slope_per_deg: float = 1 / 45  # up to +100% at 45 degrees
    hazard_uplift: float = 0.50            # straight traverse crosses terrain steeper than the limit
    unverified_uplift: float = 0.25        # part of the traverse has no elevation data
    survey_usd_per_km2: float = 6_000
    survey_floor_usd: float = 25_000
    mobilisation_usd: float = 25_000
    engineering_pct: float = 8.0           # of node hardware and installation
    contingency_base_pct: float = 10.0
    contingency_pct_per_day: float = 0.2
    contingency_cap_pct: float = 25.0
    contingency_hazard_pct: float = 5.0
    # Relay satellite
    sat_bus_usd: float = 8_000_000
    sat_payload_usd: float = 4_000_000
    sat_ref_range_km: float = 2_000.0
    sat_bus_kg: float = 150.0
    sat_payload_kg: float = 40.0
    sat_isp_s: float = 300.0
    sat_approach_speed_m_s: float = 800.0   # speed relative to the Moon on arrival, before orbit insertion
    sat_keeping_m_s_per_year: float = 40.0  # at 500 km; scales as (500 / altitude)
    sat_delivery_usd_per_kg: float = 300_000.0
    sat_ops_usd_per_day: float = 15_000.0
    sat_max_count: int = 12


COEFFICIENT_NOTES = {
    "structure_usd": "Reference node structure and mast at the reference mast height.",
    "mast_exponent": "Mast cost scales with (mast height / reference height) to this power.",
    "rf_exponent": "Radio and antenna cost scale with (longest link / reference link) to this power, between the floor and cap.",
    "gateway_comms_uplift": "Extra communications cost for a gateway's Earth-facing link.",
    "install_per_route_km": "Installation cost grows by this fraction per kilometre of traverse from the lander or nearest gateway.",
    "hazard_uplift": "Installation uplift when the straight traverse crosses terrain steeper than the slope limit.",
    "contingency_pct_per_day": "Contingency grows with mission length, up to the cap.",
    "sat_approach_speed_m_s": "Hyperbolic approach speed used for the orbit-insertion burn: dv = sqrt(v^2 + 2 mu / r) - sqrt(mu / r).",
    "sat_keeping_m_s_per_year": "Yearly station-keeping at 500 km; lower orbits need more, scaled by 500 km / altitude.",
    "sat_delivery_usd_per_kg": "Cost to deliver one kilogram (including propellant) to the Moon.",
}


def coefficients(overrides=None):
    """Defaults, with any known keys replaced. Unknown keys are an error, so a typo cannot silently do nothing."""
    base = Coefficients()
    if not overrides:
        return base
    known = {f.name for f in fields(Coefficients)}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise ValueError("Unknown cost coefficient: " + ", ".join(unknown))
    return replace(base, **{k: float(v) for k, v in overrides.items()})


def _clip(value, low, high):
    return max(low, min(high, value))


def _money(value):
    return round(float(value) + 0.0, 2)


def node_lines(node, c):
    """Price every part of one node from its own conditions. Returns [(component, base, factor, why, category)]."""
    mast = node.get("mast_m") or c.ref_mast_m
    reach = max(node.get("range_km") or 0.0, 0.0)
    rf = _clip((reach / c.ref_range_km) ** c.rf_exponent if reach else 1.0, c.rf_floor, c.rf_cap)
    gateway = c.gateway_comms_uplift if node.get("role") == "gateway" else 0.0
    battery = (node.get("battery_wh") or c.ref_battery_wh) / c.ref_battery_wh
    solar = (node.get("solar_w") or c.ref_solar_w) / c.ref_solar_w
    heater = node.get("heater_w")
    thermal = c.thermal_floor + (1 - c.thermal_floor) * ((heater if heater is not None else c.ref_heater_w) / c.ref_heater_w)
    route = max(node.get("route_km") or 0.0, 0.0)
    slope = max(node.get("slope_deg") or 0.0, 0.0)
    uplift = {"hazard": c.hazard_uplift, "unverified": c.unverified_uplift}.get(node.get("verdict"), 0.0)
    install = (1 + c.install_per_route_km * route) * (1 + c.install_slope_per_deg * slope) * (1 + uplift)
    return [
        ("Structure and mast", c.structure_usd, (mast / c.ref_mast_m) ** c.mast_exponent, f"mast {mast:g} m", "Hardware"),
        ("Radios and electronics", c.radios_usd, rf * (1 + gateway), f"longest link {reach:.2f} km" + (", gateway" if gateway else ""), "Communications"),
        ("Antennas", c.antennas_usd, rf * (1 + gateway), f"longest link {reach:.2f} km" + (", gateway" if gateway else ""), "Communications"),
        ("Batteries", c.batteries_usd, battery, f"{battery * c.ref_battery_wh:.0f} Wh", "Power"),
        ("Solar equipment", c.solar_usd, solar, f"{solar * c.ref_solar_w:.0f} W panel", "Power"),
        ("Thermal protection", c.thermal_usd, thermal, f"heater {heater if heater is not None else c.ref_heater_w:g} W", "Thermal"),
        ("Deployment, integration and installation", c.install_usd, install,
         f"traverse {route:.2f} km, slope {slope:.1f} deg" + (f", {node['verdict']} route" if uplift else ""), "Installation"),
    ]


# ---- relay satellite -------------------------------------------------------------------------------------------

def satellite_geometry(sat, site_lat):
    """Orbit numbers and how much of the time one satellite can reach the area."""
    gravity = sat.get("gravity", 1.0)
    mu = MU * gravity
    r = MOON_RADIUS_M + sat["altitude_km"] * 1000
    mask = radians(sat.get("mask_deg", 10.0))
    beam = sat.get("beam_deg", 180.0)
    speed = sqrt(mu / r)
    period = 2 * pi * sqrt(r ** 3 / mu)
    # Ground half-arc (central angle) from which the satellite is above the elevation mask.
    lam_mask = acos(_clip(MOON_RADIUS_M / r * cos(mask), -1, 1)) - mask
    lam = lam_mask
    if beam < 180:   # antenna aimed straight down: the ground within the beam half-width
        ratio = r / MOON_RADIUS_M * sin(radians(beam))
        if ratio < 1:
            lam = min(lam, asin(ratio) - radians(beam))
    lam = max(lam, 0.0)
    # Inclination where the track crosses the area, from the heading (0 = polar). The track is assumed to pass overhead,
    # as in the map preview, so this is a best case: one pass per orbit keeps the satellite in view for 2 * lam of 360 degrees.
    heading = radians(sat.get("track_deg", 0.0))
    inclination = degrees(acos(_clip(sin(heading) * cos(radians(site_lat)), -1, 1)))
    duty = _clip(lam / pi, 0, 1)
    slant = -MOON_RADIUS_M * sin(mask) + sqrt((MOON_RADIUS_M * sin(mask)) ** 2 + 2 * MOON_RADIUS_M * (r - MOON_RADIUS_M) + (r - MOON_RADIUS_M) ** 2)
    return dict(radius_km=r / 1000, speed_km_s=speed / 1000, period_min=period / 60, gravity=gravity, mu=mu,
                inclination_deg=inclination, access_half_arc_deg=degrees(lam), duty_pct=duty * 100,
                max_range_km=slant / 1000)


def satellite_lines(sat, site_lat, days, c):
    g = satellite_geometry(sat, site_lat)
    r = g["radius_km"] * 1000
    arrival = sqrt(c.sat_approach_speed_m_s ** 2 + 2 * g["mu"] / r) - sqrt(g["mu"] / r)
    years = days / 365.25
    keeping = c.sat_keeping_m_s_per_year * (500 / max(sat["altitude_km"], 1)) * years
    dv = arrival + keeping
    range_factor = _clip(g["max_range_km"] / c.sat_ref_range_km, 0.5, 4.0)
    dry = c.sat_bus_kg + c.sat_payload_kg * range_factor
    wet = dry * exp(dv / (c.sat_isp_s * G0))
    target = _clip(sat.get("coverage_pct", 95.0), 1, 100) / 100
    if g["duty_pct"] <= 0:
        count, note = 0, "With this elevation mask and beam width the satellite never sees the area, so no number of satellites gives coverage."
    else:
        needed = ceil(target / (g["duty_pct"] / 100) - 1e-9)
        count = min(needed, c.sat_max_count)
        note = (f"{needed} satellites would be needed; the model is capped at {c.sat_max_count}." if needed > c.sat_max_count else
                f"Best case, each satellite reaches the area {g['duty_pct']:.1f}% of the time (track passing overhead, antenna aimed at the surface); "
                f"{count} give about {min(100, count * g['duty_pct']):.0f}% (target {target * 100:.0f}%), assuming their passes do not overlap.")
    g.update(count=count, note=note, delta_v_arrival_m_s=arrival, delta_v_keeping_m_s=keeping, delta_v_m_s=dv, dry_mass_kg=dry, wet_mass_kg=wet,
             propellant_kg=wet - dry, range_factor=range_factor, coverage_target_pct=target * 100, achieved_pct=min(100, count * g["duty_pct"]))
    delivery_fixed = wet * c.sat_delivery_usd_per_kg
    lines = [
        ("Satellite", "Spacecraft bus", count, c.sat_bus_usd, "per satellite"),
        ("Satellite", "Relay payload and antenna", count, c.sat_payload_usd * range_factor, f"reach {g['max_range_km']:,.0f} km, factor {range_factor:.2f}"),
        ("Satellite", "Delivery to lunar orbit (incl. propellant)", count, delivery_fixed, f"wet mass {wet:,.0f} kg, dv {dv:,.0f} m/s at {sat['altitude_km']:,.0f} km"),
        ("Satellite", "Satellite operations over the mission", count, c.sat_ops_usd_per_day * days, f"{days:g} days"),
    ]
    return g, lines


# ---- whole network ---------------------------------------------------------------------------------------------

def build_report(nodes, *, scope, area_km2=None, days=30.0, site_lat=-86.8, satellite=None, budget_usd=None, flat_cost=None,
                 overrides=None, user_node_cost=None, site_name=""):
    """Itemised cost of a network. ``nodes`` are dicts: id, role, range_km, route_km, slope_deg, verdict, mast_m, battery_wh, solar_w, heater_w."""
    c = coefficients(overrides)
    lines, per_node = [], []
    for node in nodes:
        parts = []
        node_total = 0.0
        for component, base, factor, why, category in node_lines(node, c):
            unit = _money(base * factor)
            parts.append(dict(component=component, category=category, base_usd=base, factor=round(factor, 4), usd=unit, driver=why))
            lines.append(dict(category=category, item=f"{node['id']} · {component}", quantity=1, unit_cost_usd=unit, total_usd=unit,
                              driver=why, node=str(node["id"]), role=node.get("role", "relay")))
            node_total += unit
        per_node.append(dict(id=str(node["id"]), role=node.get("role", "relay"), range_km=node.get("range_km"), route_km=node.get("route_km"),
                             slope_deg=node.get("slope_deg"), verdict=node.get("verdict"), total_usd=_money(node_total), parts=parts))
    hardware = sum(l["total_usd"] for l in lines)
    program = []
    if nodes:
        if area_km2 is not None:
            program.append(("Programme", "Site survey and terrain mapping", max(c.survey_floor_usd, c.survey_usd_per_km2 * area_km2),
                            f"{area_km2:,.2f} km2 at ${c.survey_usd_per_km2:,.0f}/km2, minimum ${c.survey_floor_usd:,.0f}"))
        program.append(("Programme", "Mobilisation and logistics", c.mobilisation_usd, "one-off"))
        program.append(("Programme", "Engineering and integration", hardware * c.engineering_pct / 100, f"{c.engineering_pct:g}% of node costs"))
    risky = any(n.get("verdict") in ("hazard", "unverified") for n in nodes)
    base_total = hardware + sum(v for _, _, v, _ in program)
    pct = _clip(c.contingency_base_pct + c.contingency_pct_per_day * days + (c.contingency_hazard_pct if risky else 0.0), 0, c.contingency_cap_pct)
    if nodes:
        program.append(("Programme", "Contingency", base_total * pct / 100,
                        f"{pct:.1f}% = {c.contingency_base_pct:g}% + {c.contingency_pct_per_day:g}%/day x {days:g} days"
                        + (f" + {c.contingency_hazard_pct:g}% for steep or unverified routes" if risky else "")))
    for category, item, value, why in program:
        lines.append(dict(category=category, item=item, quantity=1, unit_cost_usd=_money(value), total_usd=_money(value), driver=why, node=None, role=None))
    surface = sum(l["total_usd"] for l in lines)
    sat_info = None
    if satellite:
        sat_info, sat_lines = satellite_lines(satellite, site_lat, days, c)
        for category, item, count, unit, why in sat_lines:
            unit = _money(unit)   # the workbook multiplies the rounded unit price, so the totals must too
            lines.append(dict(category=category, item=item, quantity=count, unit_cost_usd=unit, total_usd=_money(unit * count),
                              driver=why, node=None, role=None))
    total = _money(sum(l["total_usd"] for l in lines))
    surface = _money(surface)
    categories = {}
    for line in lines:
        categories[line["category"]] = _money(categories.get(line["category"], 0) + line["total_usd"])
    remaining = None if budget_usd is None else _money(budget_usd - total)
    return dict(
        model="dynamic", version=1, currency="USD", scope=scope, pricing_basis="illustrative",
        total_usd=total, surface_usd=surface, satellite_usd=_money(total - surface),
        flat_reference_usd=None if flat_cost is None else flat_cost["total_usd"],
        budget_usd=budget_usd, remaining_usd=remaining, within_budget=remaining is None or remaining >= 0,
        items=lines, nodes=per_node, categories=categories, satellite=sat_info,
        drivers=dict(nodes=len(nodes), gateways=sum(1 for n in nodes if n.get("role") == "gateway"), mines=sum(1 for n in nodes if n.get("role") == "mine"),
                     longest_link_km=max((n.get("range_km") or 0 for n in nodes), default=0), total_route_km=sum(n.get("route_km") or 0 for n in nodes),
                     area_km2=area_km2, mission_days=days, site=site_name, contingency_pct=round(pct, 2) if nodes else 0.0),
        coefficients={k: v for k, v in asdict(c).items()}, coefficient_notes=COEFFICIENT_NOTES,
        user_node_cost_ignored=user_node_cost,
        assumptions=("Illustrative planning coefficients, not supplier quotes or market prices; every coefficient is listed and can be overridden. "
                     "Costs move with node count and role, link and traverse distance, slope and route hazard, mast and power sizing, area, mission length"
                     + (" and the relay satellite's orbit." if satellite else ".")
                     + " Excludes the lander, launch of the surface payload, ongoing surface operations and broader mission costs."
                     + (" A user-entered all-in node estimate is shown in the flat estimate; this model does not use it." if user_node_cost else "")),
    )


# ---- adapters: a mission result or an area result -> nodes ---------------------------------------------------------

def _greatcircle_km(a, b):
    p, q = radians(a["lat"]), radians(b["lat"])
    d = radians(b["lon"] - a["lon"])
    return MOON_RADIUS_M / 1000 * acos(_clip(sin(p) * sin(q) + cos(p) * cos(q) * cos(d), -1, 1))


def mission_nodes(result):
    """Selected relays of a mission result, with the conditions that drive their price."""
    config = result["config"]
    by_id = {c["id"]: c for c in result["candidates"]}
    nodes = []
    for relay_id in result["recommended"]:
        c = by_id[relay_id]
        deploy = c.get("deployment") or {}
        nodes.append(dict(id=relay_id, role="relay", range_km=c["distance"], route_km=deploy.get("route_km", c["distance"]),
                          slope_deg=c.get("slope"), verdict=deploy.get("verdict"), mast_m=config.get("mast_m"),
                          battery_wh=config.get("battery_hours", 0) * config.get("node_load_w", 0) or None,
                          solar_w=(1 + config.get("charge_ratio", 0)) * config.get("node_load_w", 0) or None, heater_w=config.get("heater_w")))
    return nodes


def area_nodes(area, mission_defaults):
    """Every installed node of an area layout. Link lengths come from the recommended layout; traverse is the distance to the nearest gateway."""
    nodes = {n["id"]: n for n in area["nodes"]}
    layout = area["layouts"][area["recommended"]]
    reach = {i: 0.0 for i in nodes}
    for a, b in layout["edges"]:
        if a in nodes and b in nodes:
            d = _greatcircle_km(nodes[a], nodes[b])
            reach[a], reach[b] = max(reach[a], d), max(reach[b], d)
    gateways = [n for n in nodes.values() if n["role"] == "gateway"]
    out = []
    for i, n in nodes.items():
        others = [g for g in gateways if g["id"] != i]
        route = min((_greatcircle_km(n, g) for g in others), default=0.0)
        out.append(dict(id=i, role=n["role"], range_km=reach[i], route_km=route, slope_deg=None, verdict=None, mast_m=mission_defaults.get("mast_m"),
                        battery_wh=(mission_defaults.get("battery_hours") or 0) * (mission_defaults.get("node_load_w") or 0) or None,
                        solar_w=(1 + (mission_defaults.get("charge_ratio") or 0)) * (mission_defaults.get("node_load_w") or 0) or None,
                        heater_w=mission_defaults.get("heater_w")))
    return out


def mission_days(config):
    """Mission length in days from the applied configuration (ISO dates)."""
    try:
        return max((date.fromisoformat(str(config["end"])[:10]) - date.fromisoformat(str(config["start"])[:10])).days, 1)
    except (KeyError, ValueError):
        return 30.0


class SatelliteSpec(BaseModel):
    """The relay satellite to price, in the same terms as the orbit controls."""
    altitude_km: float = Field(ge=50, le=10000)
    gravity: float = Field(default=1.0, ge=0.1, le=5)
    track_deg: float = Field(default=0.0, ge=0, le=90)
    beam_deg: float = Field(default=180.0, ge=5, le=180)
    mask_deg: float = Field(default=10.0, ge=0, le=45)
    coverage_pct: float = Field(default=95.0, ge=1, le=100)


def estimate(result, area=None, *, scope="mission", satellite=None, overrides=None):
    """The dynamic cost of an applied mission or area result. ``satellite`` is a SatelliteSpec or dict, or None for no satellite segment."""
    source = area if scope == "area" else result
    if scope == "area" and not area:
        raise ValueError("No applied area layout is available.")
    config = result["config"]
    if isinstance(satellite, SatelliteSpec):
        satellite = satellite.model_dump()
    flat = source.get("cost") if isinstance(source.get("cost"), dict) else None
    nodes = area_nodes(area, config) if scope == "area" else mission_nodes(result)
    return build_report(nodes, scope=scope, area_km2=area["site"].get("area_km2") if scope == "area" else None, days=mission_days(config),
                        site_lat=source["site"]["lat"], satellite=satellite, budget_usd=flat.get("budget_usd") if flat else None, flat_cost=flat,
                        overrides=overrides, user_node_cost=config.get("relay_cost_usd"), site_name=source["site"].get("name", ""))
