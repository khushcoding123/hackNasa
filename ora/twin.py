"""Network digital twin: step the chosen network through time and ask "what if?".

State per node and step: illumination, battery charge, temperature and operating mode. Links
are up when both ends are up. The twin runs on simulated telemetry; the same state layout could
be fed by real telemetry later. Illumination comes from SPICE Sun geometry against each node's
terrain horizon, so its fidelity is bounded by the 20 m terrain grid.
"""
from dataclasses import replace

import networkx as nx
import numpy as np

from .blast import blast_effects
from .configure import SiteFeatures, configure, parts_list, swaps as config_swaps
from .dust import failure_step
from .features import CHEAP
from .links import Optical, PowerBeam, beam_budget, optical_margin_db
from .thermal import LUNAR_NIGHT_H, NodeDesign, simulate

MODE = {0: "off", 1: "hibernate", 2: "active"}
# Site-condition parameters for the configurator. Proxies to tune or sweep, not measurements.
DUST_RADIUS_M = 1_500.0  # relays this close to a mining site count as dusty
BLAST_RADIUS_M = 600.0  # relays this close to a mining site count as blast-exposed
ROCK_SLOPE_DEG = 25.0  # slope that maps to rockiness 1: steep ground sheds regolith and exposes rock.
# There is no boulder data at 20 m; LRO Diviner rock abundance would be the measured source.
TRUNK_LINK_M = 3_000.0  # a relay with a link this long is a trunk node


def site_features(network, v, lit_fraction=None, blast_risk=None, dust_risk=None):
    """Surface conditions at relay ``v`` for the configurator, from the terrain and the plan."""
    px = network["grid"].pixel_size_m
    mines = network["sites"][network["demand"]]
    nearest = float(np.min(np.hypot(*(mines - network["sites"][v]).T)) * px) if len(mines) else np.inf
    row = network["features"][v]
    members = set(network["plan"]["chosen"]) | set(network["gateways"]) | set(network["demand"])
    longest = max((d["dist"] for w, d in network["G"][v].items() if w in members), default=0.0)
    role = "gateway" if v in network["gateways"] else "trunk" if longest >= TRUNK_LINK_M else "mesh"
    return SiteFeatures(
        lit_fraction=float(network["sun_frac"][v] if lit_fraction is None else lit_fraction),
        earth_vis=float(network["earth_frac"][v]), slope_deg=float(row[CHEAP.index("slope")]),
        rockiness=float(np.clip(row[CHEAP.index("slope")] / ROCK_SLOPE_DEG, 0, 1)),
        dust_risk=float((0.7 if nearest <= DUST_RADIUS_M else 0.2) if dust_risk is None else dust_risk),
        blast_risk=bool(nearest <= BLAST_RADIUS_M if blast_risk is None else blast_risk), role=role)


def node_configs(network):
    """Parts list for every chosen relay and gateway under the baseline conditions of the scenario."""
    relays = list(network["plan"]["chosen"]) + [g for g in network["gateways"] if g not in network["plan"]["chosen"]]
    out = []
    for v in relays:
        features = site_features(network, v)
        out.append(dict(id=int(v), role=features.role, features=vars(features), **parts_list(configure(features))))
    return out


def _longest_false(flags, step_h):
    best = run = 0
    for flag in flags:
        run = 0 if flag else run + 1
        best = max(best, run)
    return best * step_h


def run(network, design=NodeDesign(), kill=(), disk=None, night=None, beam=False, beam_design=PowerBeam(),
        optical=False, optical_design=Optical(), dust_db=0.0, rf_backup=True, blast=None, dust=None):
    """Simulate the chosen network.

    kill: [(node, hour)] hardware deaths. disk: (col, row, radius_m, hour) regional failure such as
    a landslide or ejecta. night: (start_hour, duration_hours) forces darkness on every node, to test
    a full lunar night at a site or season where the geometry stays lit. beam: power shadowed relays
    by laser from a sunlit relay in view. optical: upgrade long relay hops to optical trunks; dust_db
    degrades them; rf_backup says whether a failed optical trunk falls back to its RF link.
    blast: (node, hour, kill_radius_m, damage_radius_m) a scheduled blast centred on a node,
    usually a mining site: relays inside the kill radius die, relays inside the damage radius
    collect dust and fail more easily. dust: dict(rate_per_day, base_hazard_per_h, k_dust, seed)
    turns on dust build-up and dust-driven random failures. All dust numbers are parameters.
    """
    scenario, G, plan = network["scenario"], network["G"], network["plan"]
    step_h, times = scenario.step_h, network["times"]
    steps = len(times)
    relays = [v for v in plan["chosen"]] + [g for g in network["gateways"] if g not in plan["chosen"]]
    demand, gateways = list(network["demand"]), list(network["gateways"])
    members = relays + demand
    H = G.subgraph(members)
    px = network["grid"].pixel_size_m
    lit = {v: network["lit"][v].copy() for v in members}
    if night:
        a, b = int(night[0] / step_h), int((night[0] + night[1]) / step_h)
        for v in members:
            lit[v][a:b] = 0.0
    dead_at = {v: steps for v in members}
    for v, hour in kill:
        if v in dead_at:
            dead_at[v] = min(dead_at[v], int(hour / step_h))
    if disk:
        cx, cy, radius, hour = disk
        for v in relays:
            if np.hypot(network["sites"][v][0] - cx, network["sites"][v][1] - cy) * px <= radius:
                dead_at[v] = min(dead_at[v], int(hour / step_h))

    # Blast and dust. Baseline node configurations come first: a dust shield halves deposition.
    baseline = {v: site_features(network, v) for v in relays}
    shielded = {v for v in relays if "dust:shield" in configure(baseline[v]).modules}
    blast_report, hurt, blast_step = None, set(), steps
    if blast:
        center_node, hour, r_kill, r_dmg = blast
        blast_step = int(hour / step_h)
        pos = {v: tuple(network["sites"][v] * px) for v in relays}
        dead, hurt_list = blast_effects(None, pos, tuple(network["sites"][center_node] * px), r_kill, r_dmg)
        hurt = set(hurt_list)
        for v in dead:
            dead_at[v] = min(dead_at[v], blast_step)
        blast_report = dict(center=int(center_node), hour=hour, kill_radius_m=r_kill, damage_radius_m=r_dmg,
                            destroyed=[int(v) for v in dead], damaged=[int(v) for v in hurt_list])
    dust_load, dust_failures = {v: np.zeros(steps) for v in relays}, []
    if dust or blast:
        settings = dict(rate_per_day=0.0, base_hazard_per_h=0.0, k_dust=3.0, seed=0)
        settings.update(dust or {})
        rng = np.random.default_rng(settings["seed"])
        after_blast = np.arange(steps) >= blast_step
        for v in relays:
            rate = settings["rate_per_day"] / 24 * (1.0 if baseline[v].dust_risk >= 0.4 else 0.25) * (0.5 if v in shielded else 1.0)
            load = np.clip(rate * step_h * np.arange(1, steps + 1) + (0.5 * after_blast if v in hurt else 0.0), 0, 1)
            dust_load[v] = load
            charging = np.where(after_blast & (v in hurt), 3.0, 1.0)
            failed = failure_step(rng, settings["base_hazard_per_h"], load, step_h, settings["k_dust"], charging)
            if 0 <= failed < dead_at[v]:
                dead_at[v] = failed
                dust_failures.append(dict(node=int(v), hour=failed * step_h, dust=float(load[failed])))

    # Laser power: each mostly-shadowed relay is fed from the nearest sunlit relay it can see.
    beams, beam_w = [], {v: np.zeros(steps) for v in members}
    if beam:
        sunny = [v for v in relays if lit[v].mean() >= 0.5]
        for v in relays:
            if lit[v].mean() >= 0.2:
                continue
            sources = sorted((H[v][s]["dist"], s) for s in sunny if H.has_edge(v, s))
            if sources:
                dist, source = sources[0]
                budget = beam_budget(dist, beam_design)
                beam_w[v] = budget["delivered_w"] * (lit[source] > 0.5) * (np.arange(steps) < dead_at[source])
                beams.append(dict(source=source, target=v, distance_m=dist, delivered_w=budget["delivered_w"],
                                  spot_radius_m=budget["spot_radius_m"], efficiency=budget["end_to_end_efficiency"]))

    state = {}
    for v in relays:
        state[v] = simulate(design, lit[v], step_h, beam_w=beam_w[v])
        state[v]["mode"][dead_at[v]:] = 0
    for v in demand:  # mining equipment brings its own power; it is a load on the network, not a relay
        state[v] = dict(soc=np.ones(steps), temp_k=np.full(steps, 273.15), mode=np.full(steps, 2.0), heater_w=np.zeros(steps),
                        frozen_step=-1)
        state[v]["mode"][dead_at[v]:] = 0
    relaying = {v: (state[v]["mode"] == 2) | ((state[v]["mode"] == 1) & (design.duty > 0)) for v in members}

    edges = []
    for a, b, data in H.edges(data=True):
        kind, usable = "rf", True
        if optical and a in relays and b in relays and data["dist"] >= 3000:
            margin = float(optical_margin_db(data["dist"], optical_design, dust_db))
            clean = float(optical_margin_db(data["dist"], optical_design, 0.0))
            if clean >= optical_design.required_margin_db:
                kind = "optical"
                if margin < optical_design.required_margin_db:
                    kind, usable = ("rf-backup", True) if rf_backup else ("optical-down", False)
        edges.append(dict(a=a, b=b, dist=data["dist"], kind=kind, up=(relaying[a] & relaying[b]) & usable))

    to_gateway, to_earth = np.zeros((steps, len(demand)), bool), np.zeros((steps, len(demand)), bool)
    for t in range(steps):
        live = nx.Graph()
        live.add_nodes_from(v for v in members if relaying[v][t])
        live.add_edges_from((e["a"], e["b"]) for e in edges if e["up"][t])
        for flags, sources in ((to_gateway, [g for g in gateways if g in live]),
                               (to_earth, [g for g in gateways if g in live and network["earth"][g][t]])):
            reach = set()
            for g in sources:
                if g not in reach:
                    reach |= nx.node_connected_component(live, g)
            flags[t] = [d in reach for d in demand]
    served_gateway, served_earth = to_gateway.mean(axis=1), to_earth.mean(axis=1)
    frozen = [v for v in relays if state[v]["frozen_step"] >= 0]
    # Re-run the configurator with the conditions this run created and report what must be swapped.
    module_swaps = []
    for v in relays:
        changed = site_features(network, v, lit_fraction=float(lit[v].mean()),
                                blast_risk=True if v in hurt else None,
                                dust_risk=max(baseline[v].dust_risk, float(dust_load[v].max()), 0.7 if v in hurt else 0.0))
        delta = config_swaps(configure(baseline[v]), configure(changed))
        if delta["add"] or delta["remove"]:
            reasons = [text for flag, text in ((changed.lit_fraction < 0.5 <= baseline[v].lit_fraction, "site went dark"),
                                               (changed.blast_risk and not baseline[v].blast_risk, "inside the blast damage radius"),
                                               (changed.dust_risk >= 0.4 > baseline[v].dust_risk, "dust load rose")) if flag]
            module_swaps.append(dict(node=int(v), reason=", ".join(reasons) or "conditions changed", **delta))
    roles = {**{v: "relay" for v in relays}, **{g: "gateway" for g in gateways}, **{d: "mine" for d in demand}}
    return dict(
        times=[stamp.isoformat() for stamp in times], step_h=step_h,
        nodes=[dict(id=int(v), role=roles[v], x=float(network["sites"][v][0]), y=float(network["sites"][v][1]),
                    lat=float(network["lat"][v]), lon=float(network["lon"][v]), elevation=float(network["ground"][v]),
                    sun_fraction=float(lit[v].mean()), earth_fraction=float(network["earth"][v].mean()),
                    lit=np.round(lit[v], 2).tolist(), soc=np.round(state[v]["soc"], 3).tolist(),
                    temp_c=np.round(state[v]["temp_k"] - 273.15, 1).tolist(), mode=state[v]["mode"].astype(int).tolist(),
                    min_temp_c=float(state[v]["temp_k"].min() - 273.15), min_soc=float(state[v]["soc"].min()),
                    frozen_h=None if state[v]["frozen_step"] < 0 else state[v]["frozen_step"] * step_h,
                    killed_h=None if dead_at[v] >= steps else dead_at[v] * step_h,
                    dust=float(dust_load[v][-1]) if v in dust_load else 0.0,
                    earth=network["earth"][v].astype(int).tolist() if v in gateways else None) for v in members],
        edges=[dict(a=int(e["a"]), b=int(e["b"]), distance_m=e["dist"], kind=e["kind"],
                    up="".join("1" if flag else "0" for flag in e["up"])) for e in edges],
        beams=[{**b, "source": int(b["source"]), "target": int(b["target"])} for b in beams],
        blast=blast_report, dust_failures=dust_failures, module_swaps=module_swaps,
        served_gateway=served_gateway.tolist(), served_earth=served_earth.tolist(),
        summary=dict(gateway_availability=float(served_gateway.mean() * 100), earth_availability=float(served_earth.mean() * 100),
                     always_connected=bool(served_gateway.min() >= 1.0),
                     longest_gateway_outage_h=_longest_false(served_gateway >= 1.0, step_h),
                     longest_earth_outage_h=_longest_false(served_earth >= 1.0, step_h),
                     relays=len(relays), frozen=[int(v) for v in frozen],
                     drained=[int(v) for v in relays if state[v]["soc"].min() <= 0 and v not in frozen],
                     optical_trunks=sum(e["kind"] != "rf" for e in edges), beams=len(beams),
                     dust_failures=len(dust_failures), module_swaps=len(module_swaps),
                     blast_destroyed=len(blast_report["destroyed"]) if blast_report else 0))


def night_design(network, design=NodeDesign(), target=1.0, night=(48.0, LUNAR_NIGHT_H),
                 duties=(1.0, 0.5, 0.2, 0.1, 0.0), batteries=(100, 200, 400, 600, 900, 1300, 1800, 2500, 3500, 5000), **options):
    """Smallest battery per duty cycle that keeps at least ``target`` of mining sites linked all night.

    Forces a full lunar night on every node, then scans battery sizes for each hibernation duty
    cycle (1.0 means never hibernate). Returns one row per duty cycle.
    """
    rows = []
    for duty in duties:
        candidate = replace(design, hibernate=duty < 1.0, duty=duty)
        found = None
        for battery in batteries:
            result = run(network, replace(candidate, battery_wh=float(battery)), night=night, **options)
            a, b = int(night[0] / result["step_h"]), int((night[0] + night[1]) / result["step_h"])
            worst = min(result["served_gateway"][a:b])
            if worst >= target - 1e-9 and not result["summary"]["frozen"]:
                found = dict(battery_wh=battery, worst_served=worst)
                break
        rows.append(dict(duty=duty, hibernate=duty < 1.0, battery_wh=found["battery_wh"] if found else None,
                         feasible=found is not None,
                         wake_latency_min=0.0 if duty >= 1.0 else (None if duty <= 0 else round((1 - duty) * 60, 1))))
    return rows
