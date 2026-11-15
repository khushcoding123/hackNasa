"""RF budgets, outage metrics, standalone relay power, and constrained search."""
from itertools import combinations

from .costs import relay_limit

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


def evaluate(direct, relays, selection, mission, without_satellite=None):
    paths = direct.astype(np.uint8).copy()
    for idx in selection:
        paths += relays[idx]
    online = paths > 0
    hours = mission.minutes / 60
    combined = metrics(online, hours)
    failed = [metrics(paths - relays[idx] > 0, hours) for idx in selection]
    independent = paths >= 2
    if without_satellite is not None:
        base_direct, base_relays = without_satellite
        surviving, satellite_lost, _, _ = evaluate(base_direct, base_relays, selection, mission)
        failed.append(satellite_lost)
        independent &= surviving
    worst = dict(availability=min(v["availability"] for v in failed), longest=max(v["longest"] for v in failed)) if failed else combined
    return online, combined, worst, float(independent.mean() * 100)


def optimize(direct, relays, eligible, mission, candidates=None, without_satellite=None):
    best, best_key = (), None
    for count in range(relay_limit(min(mission.max_relays, len(eligible)), mission.budget_usd, mission.relay_cost_usd) + 1):
        winners = []
        for selection in combinations(eligible, count):
            _, combined, worst, _ = evaluate(direct, relays, selection, mission, without_satellite)
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
