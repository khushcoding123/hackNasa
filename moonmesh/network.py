"""RF budgets, outage metrics, standalone relay power, and constrained search."""
from itertools import combinations

from .costs import cost_report, relay_limit

import numpy as np


def link_margin(radio, distance_km):
    fspl = 20 * np.log10(distance_km) + 20 * np.log10(radio.ghz) + 92.45
    sensitivity = radio.sensitivity + 10 * np.log10(radio.rate_kbps / 128)
    return 30 + 10 * np.log10(radio.watts) + radio.tx_gain + radio.rx_gain - fspl - radio.loss - sensitivity


def spans(flags):
    edges = np.flatnonzero(np.diff(np.r_[False, ~np.asarray(flags, dtype=bool), False]))
    return edges.reshape(-1, 2)


def metrics(flags, hours):
    outages = spans(flags)
    durations = (outages[:, 1] - outages[:, 0]) * hours
    return dict(availability=float(np.mean(flags) * 100), longest=float(durations.max(initial=0)),
                total=float(durations.sum()), intervals=len(outages), average=float(durations.mean()) if len(outages) else 0)


def battery(sun, endurance, ratio, step, initial_pct=0):
    state = endurance * initial_pct / 100
    powered, reserves = [], []
    for lit in sun:
        powered.append(bool(lit or state + 1e-9 >= step))
        state = min(endurance, state + ratio * step) if lit else max(0, state - step)
        reserves.append(state)
    return np.array(powered), reserves


def passes(values, mission):
    return values["availability"] >= mission.availability and values["longest"] <= mission.blackout_hours


def evaluate(direct, relays, selection, mission):
    paths = direct.astype(np.uint8).copy()
    for idx in selection:
        paths += relays[idx]
    online = paths > 0
    hours = mission.minutes / 60
    combined = metrics(online, hours)
    failed = [metrics(paths - relays[idx] > 0, hours) for idx in selection]
    worst = dict(availability=min(v["availability"] for v in failed), longest=max(v["longest"] for v in failed)) if failed else combined
    return online, combined, worst, float((paths >= 2).mean() * 100)


def optimize(direct, relays, eligible, mission, candidates=None):
    best, best_key = (), None
    for count in range(relay_limit(min(mission.max_relays, len(eligible)), mission.budget_usd, mission.relay_cost_usd) + 1):
        winners = []
        for selection in combinations(eligible, count):
            _, combined, worst, _ = evaluate(direct, relays, selection, mission)
            target = worst if mission.resilient else combined
            key = (target["availability"], -target["longest"], -count)
            if best_key is None or key > best_key:
                best, best_key = selection, key
            if passes(target, mission):
                secondary = infrastructure_key(selection, candidates) if candidates is not None else ()
                winners.append((secondary + key, selection))
        if winners:
            return max(winners, key=lambda item: item[0])[1], True
    return best, False


def infrastructure_key(selection, candidates):
    selected = [candidates[idx] for idx in selection]
    return (-sum(c["distance"] for c in selected),
            -sum(c["slope"] for c in selected),
            sum(c["solar"]["availability"] for c in selected),
            sum(min(c["surface_margin"], c["minimum_earth_margin"]) for c in selected),
            -sum(c["unknown_samples"] for c in selected))


def compare_configurations(result, mission, alternatives):
    """Compare installed subsets of the same screened catalog on one mission grid."""
    direct = np.asarray(result["samples"]["direct"], dtype=bool)
    candidates = result["candidates"]
    paths = [np.asarray(candidate["temporal_coverage"], dtype=bool) for candidate in candidates]
    indices = {candidate["id"]: i for i, candidate in enumerate(candidates)}
    rows = []
    layouts = [("Direct only", []), ("Planner recommendation", result["recommended"])]
    layouts.extend((row.name, row.relay_ids) for row in alternatives)
    for name, ids in layouts:
        missing = set(ids) - indices.keys()
        if missing:
            raise ValueError(f"Relay IDs are outside the screened results: {', '.join(sorted(missing))}.")
        selection = tuple(indices[relay_id] for relay_id in ids)
        online, network, worst, _ = evaluate(direct, paths, selection, mission)
        cost = cost_report(len(ids), mission.budget_usd, mission.relay_cost_usd)
        rows.append(dict(
            name=name, relay_ids=ids, network=network, worst_relay_failure=worst if ids else None,
            recovered_hours=float(np.count_nonzero(online & ~direct)*mission.minutes/60),
            meets_communications=passes(worst if mission.resilient else network, mission),
            within_relay_limit=len(ids) <= mission.max_relays, cost=cost, online=online.tolist(),
            relay_sites=[{key: candidates[i][key] for key in ("id", "lat", "lon", "distance")} for i in selection],
        ))
    return dict(site=result["site"], config=result["config"], times=result["times"], configurations=rows,
                architecture="Direct Earth or lander → surface relay → Earth",
                environment_case="cold_dust", provenance=result["provenance"])
