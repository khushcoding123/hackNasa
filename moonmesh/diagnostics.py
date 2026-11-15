"""Why a relay is or is not useful, whether it could be reached, and what happens when it is lost.

Three self-contained helpers used by ``moonmesh.science.scenario``; none of them changes which relays are selected.

- ``explain_candidate``: for each relay, which constraint takes the path down and for how much of the mission:
  terrain, RF margin, power (night, or dust on the panels), or temperature (frozen).
- ``deployment_report``: slope, relief and a straight-line traverse from the lander, with the steep stretches counted.
- ``power_loss_simulation``: each selected relay goes dark for a stated number of hours, at the worst moment for the network.

All three are scenario calculations under the stated assumptions, not forecasts.
"""
import numpy as np

from .network import evaluate, metrics, passes, spans

# Headline constraint groups, in tie-break order, with the wording shown to the user.
FACTORS = {
    "terrain": "terrain blocks the line of sight",
    "rf": "the RF link margin is too low",
    "power": "the node has no power",
    "temperature": "the node is frozen",
    "data": "terrain or environment data is missing",
    "lander": "the lander itself is unavailable",
}
EARTH_TERRAIN = ("Terrain obstruction or clearance", "Earth below geometric horizon")


def _pct(flags):
    return float(np.mean(flags) * 100) if len(flags) else 0.0


def explain_candidate(los, surface_ok, surface_margin_ok, relay_sample, state, sunlight, lander_available, earth_margin_db):
    """Share of the mission each constraint is active, and how much would be recovered if only that one were fixed.

    ``state`` is the relay's thermal/power state for the primary case. Shares overlap (several constraints can hold in
    the same sample); ``recovered_if_fixed_pct`` is the share of samples where that constraint is the only one down.
    """
    steps = len(sunlight)
    reason = np.asarray(state["reason"])
    rf_margin = np.asarray(relay_sample["rf_margin"], dtype=float)
    earth_reason = np.asarray(relay_sample["reason"])
    unknown_earth = earth_reason == "Insufficient terrain coverage"
    # Earth side: terrain and RF are tested independently, so a sample with both problems counts for both.
    terrain = np.isin(earth_reason, EARTH_TERRAIN) | np.full(steps, los is False)
    rf = (rf_margin < earth_margin_db) | np.full(steps, los is True and not surface_margin_ok)
    data = unknown_earth | (reason == "Unknown environment") | np.full(steps, los is None)
    power = reason == "Insufficient power"
    temperature = reason == "Frozen"
    lander = ~np.asarray(lander_available, dtype=bool)
    groups = dict(terrain=terrain, rf=rf, power=power, temperature=temperature, data=data, lander=lander)
    any_down = np.logical_or.reduce(list(groups.values()))
    stack = np.array(list(groups.values()))
    only = {name: stack[i] & (stack.sum(axis=0) == 1) for i, name in enumerate(groups)}
    blocked = {name: _pct(flag) for name, flag in groups.items()}
    primary = max(groups, key=lambda name: (blocked[name], -list(groups).index(name))) if any_down.any() else None
    dark = power & ~np.asarray(sunlight, dtype=bool)
    out = dict(
        primary=primary, available_pct=_pct(~any_down),
        blocked_pct=blocked, recovered_if_fixed_pct={name: _pct(flag) for name, flag in only.items()},
        power_in_darkness_pct=_pct(dark), power_in_sunlight_pct=_pct(power & ~dark),
        surface_link=dict(terrain=los is False, rf=bool(los is True and not surface_margin_ok), unknown=los is None))
    out["summary"] = describe(out)
    return out


def describe(info):
    """One plain sentence naming the binding constraint and what else matters."""
    primary = info["primary"]
    if primary is None:
        return "Nothing limits this relay: terrain, RF, power and temperature all hold for the whole mission."
    share = info["blocked_pct"][primary]
    surface = info["surface_link"]
    if primary == "terrain" and surface["terrain"]:
        head = "Limited by terrain: the lander-to-relay path is blocked, so the relay is unreachable for the whole mission"
    elif primary == "rf" and surface["rf"]:
        head = "Limited by RF: the lander-to-relay link margin is below the requirement for the whole mission"
    elif primary == "power":
        night = info["power_in_darkness_pct"]
        cause = "mostly in darkness" if night >= info["power_in_sunlight_pct"] else "mostly in sunlight, where dust cuts the charging"
        head = f"Limited by power: the node cannot sustain service for {share:.0f}% of the mission, {cause}"
    elif primary == "data":
        head = f"Insufficient data: terrain or environment samples are missing for {share:.0f}% of the mission"
    else:
        head = f"Limited by {primary}: {FACTORS[primary]} in {share:.0f}% of the samples"
    others = [f"{name} {info['blocked_pct'][name]:.0f}%" for name in FACTORS if name != primary and info["blocked_pct"][name] >= 0.5]
    gain = info["recovered_if_fixed_pct"][primary]
    tail = f"Fixing only that would recover {gain:.0f}% of the mission." if gain >= 0.5 else "Other constraints would still keep it down."
    return head + (f". Also active: {', '.join(others)}" if others else "") + f". {tail}"


def _runs(flags, step_m):
    """Lengths in metres of the consecutive True stretches."""
    return [float((b - a) * step_m) for a, b in spans(~np.asarray(flags, dtype=bool))] if len(flags) else []


def deployment_report(grid, slope_grid, lander_xy, candidate, mission):
    """Can this relay realistically be put there? Straight-line traverse from the lander plus the ground around the mast.

    The traverse is a straight line over the 20 m DEM, not a planned route: there is no path finder, no regolith strength,
    no boulder or small-crater data below the DEM resolution, and no rover speed. A "feasible" verdict therefore means only
    that no sampled stretch exceeds the slope limit.
    """
    limit = mission.route_slope_limit if mission.route_slope_limit is not None else mission.slope_limit
    pixel = grid.pixel_size_m
    (x0, y0), (x1, y1) = lander_xy, (candidate["x"], candidate["y"])
    straight = float(np.hypot(x1 - x0, y1 - y0))
    count = max(2, int(np.ceil(straight / pixel)) + 1)
    fractions = np.linspace(0, 1, count)
    xs, ys = x0 + (x1 - x0) * fractions, y0 + (y1 - y0) * fractions
    heights, slopes = grid.sample(xs, ys), slope_grid.sample(xs, ys)
    known = np.isfinite(heights) & np.isfinite(slopes)
    step = straight / (count - 1)
    steep = known & (slopes > limit)
    drops = np.diff(heights)
    drops = drops[np.isfinite(drops)]
    legs = np.hypot(step, np.diff(heights))
    route = float(np.nansum(legs)) / 1000
    # Ground around the mast: relief and steep area within 100 m.
    radius = max(1, int(round(100 / pixel)))
    cx, cy = int((candidate["x"] - grid.x_left) / pixel), int((grid.y_top - candidate["y"]) / pixel)
    r0, c0 = max(0, cy - radius), max(0, cx - radius)
    patch = grid.heights[r0:cy + radius + 1, c0:cx + radius + 1]
    patch_slope = slope_grid.heights[r0:cy + radius + 1, c0:cx + radius + 1]
    finite = np.isfinite(patch)
    relief = float(np.nanmax(patch) - np.nanmin(patch)) if finite.any() else None
    local_steep = _pct(patch_slope[finite] > limit) if finite.any() else None
    unknown_pct = _pct(~known)
    longest_steep = max(_runs(steep, step), default=0.0) if steep.any() else 0.0
    checks = [
        dict(name="Deployment distance", value=candidate["distance"], limit=mission.deployment_km, unit="km", ok=candidate["distance"] <= mission.deployment_km),
        dict(name="Slope at the mast", value=candidate["slope"], limit=mission.slope_limit, unit="°", ok=candidate["slope"] <= mission.slope_limit),
        dict(name="Steepest traverse slope", value=float(np.max(slopes[known])) if known.any() else None, limit=limit, unit="°",
             ok=None if not known.any() else not steep.any()),
        dict(name="Traverse with unknown terrain", value=unknown_pct, limit=0, unit="%", ok=unknown_pct == 0),
    ]
    if steep.any():
        verdict = "hazard"
    elif unknown_pct > 0:
        verdict = "unverified"
    else:
        verdict = "feasible"
    reasons = {
        "feasible": "No sampled stretch of the straight traverse exceeds the slope limit.",
        "hazard": f"{_pct(steep):.0f}% of the straight traverse is steeper than {limit:g}°, longest stretch {longest_steep:,.0f} m. The traverse must cross it or detour; slopes are from a 20 m DEM, so a single steep cell can be DEM noise.",
        "unverified": f"{unknown_pct:.0f}% of the traverse has no elevation data.",
    }
    return dict(verdict=verdict, reason=reasons[verdict], slope_limit=limit,
                straight_km=straight / 1000, route_km=route, detour_factor=route / (straight / 1000) if straight else 1.0,
                max_slope=checks[2]["value"], mean_slope=float(np.mean(slopes[known])) if known.any() else None,
                steep_pct=_pct(steep), longest_steep_m=longest_steep, unknown_pct=unknown_pct,
                ascent_m=float(drops[drops > 0].sum()), descent_m=float(-drops[drops < 0].sum()),
                net_elevation_m=float(heights[-1] - heights[0]) if np.isfinite(heights[0]) and np.isfinite(heights[-1]) else None,
                local_relief_m=relief, local_steep_pct=local_steep, checks=checks,
                assumption="Straight-line traverse over a 20 m DEM: no route planning, regolith strength, rocks or craters below DEM resolution.")


def power_loss_simulation(direct, relays, selection, options, times, end, mission, hours_off, max_starts=240):
    """Each selected relay loses power for ``hours_off`` hours, starting when it hurts the network most.

    The relay is dark for the whole window (a battery or solar failure that keeps it off), then recovers. Worst moment =
    the start that gives the longest network blackout, then the lowest availability. The other relays and the direct
    path carry on as normal. Nothing here is a probability.
    """
    hours = mission.minutes / 60
    length = max(1, int(round(hours_off / hours)))
    steps = len(direct)
    _, nominal, _, _ = evaluate(direct, relays, selection, mission)
    starts = np.unique(np.linspace(0, max(0, steps - 1), min(steps, max_starts)).astype(int))
    stamps = (*times, end)
    rows = []
    for idx in selection:
        contribution = relays[idx].astype(bool)
        worst = None
        for start in starts:
            stop = min(steps, start + length)
            if not contribution[start:stop].any():
                continue   # losing power while the relay carried nothing changes nothing
            mask = np.ones(steps, dtype=bool)
            mask[start:stop] = False
            altered = list(relays)
            altered[idx] = relays[idx] & mask
            online, values, _, _ = evaluate(direct, altered, selection, mission)
            key = (values["longest"], -values["availability"])
            if worst is None or key > worst[0]:
                worst = (key, start, stop, values, online)
        if worst is None:
            rows.append(dict(id=options[idx]["id"], affects_network=False, hours_off=hours_off,
                             note="This relay is never part of an available path in this mission, so losing its power changes nothing."))
            continue
        _, start, stop, values, online = worst
        window = online[start:stop]
        rows.append(dict(
            id=options[idx]["id"], affects_network=True, hours_off=(stop - start) * hours,
            start=stamps[start].isoformat(), end=stamps[min(stop, steps)].isoformat(), first=int(start), last=int(stop),
            connected_during_pct=_pct(window), metrics=values, meets=passes(values, mission),
            availability_lost=nominal["availability"] - values["availability"], blackout_increase=values["longest"] - nominal["longest"],
            note=("The network stays connected through the loss." if window.all() else
                  f"Contact is lost for {(~window).sum() * hours:g} h of the {(stop - start) * hours:g} h the relay is dark.")))
    return dict(hours=hours_off, nominal=nominal, relays=rows,
                basis="Each relay in turn is dark for the stated hours, starting at the moment that gives the network its longest blackout. "
                      "Other relays and the direct path are unchanged. A what-if, not a failure probability.")
