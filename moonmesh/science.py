"""Recompute a mission from NASA ephemerides and terrain."""
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from itertools import combinations

import numpy as np
from scipy.ndimage import maximum_filter

from lunar_core.geometry import Body, SurfacePoint, horizontal_arrays
from lunar_core.horizon import horizon_mask
from lunar_core.kernels import KERNELS, load_kernels
from lunar_core.terrain import (
    MOON_RADIUS_M, TerrainGrid, polar_stereographic_south, polar_stereographic_south_inverse, radial_points,
)

from .costs import cost_report
from .diagnostics import deployment_report, explain_candidate, power_loss_simulation
from .environment import CASES, node_states, public_state, optimize_environment
from .data import DATA, LOLA, PRODUCTS, SITES, activate
from .evidence import NOT_MODELLED, assess, check, gap
from .network import battery, evaluate, link_margin, metrics, optimize, passes, spans


@dataclass(frozen=True)
class Observer:
    lat: float
    lon: float
    ground: float
    mast: float


def nullable(values):
    return np.where(np.isfinite(values), values, None).tolist()


@lru_cache(maxsize=6)
def terrain(site, suffix):
    path = DATA / "terrain" / f"{site}_{suffix}.npz"
    if not path.is_file():
        raise FileNotFoundError("LOLA terrain is missing. Run python -m moonmesh.data from the repository.")
    return TerrainGrid.load(path)


@lru_cache(maxsize=500)
def mask_for(site, point, bias):
    return horizon_mask(point.lat, point.lon, terrain(site, "near20km"), terrain(site, "far"), point.mast,
                        elevation_bias_m=bias)


def slope_grid(grid):
    dy, dx = np.gradient(grid.heights, grid.pixel_size_m, grid.pixel_size_m)
    return np.degrees(np.arctan(np.hypot(dx, dy)))


def candidates(grid, slope, x, y, mission):
    pixel = grid.pixel_size_m
    span = int(mission.deployment_km * 1000 / pixel) + 12
    center_x, center_y = int((x - grid.x_left)/pixel), int((grid.y_top-y)/pixel)
    r0, r1 = max(0, center_y-span), min(grid.heights.shape[0], center_y+span+1)
    c0, c1 = max(0, center_x-span), min(grid.heights.shape[1], center_x+span+1)
    heights = grid.heights[r0:r1, c0:c1]
    peaks = heights == maximum_filter(np.nan_to_num(heights, nan=-np.inf), size=max(3, round(420/pixel)))
    rows, cols = np.nonzero(peaks & (slope[r0:r1, c0:c1] <= mission.slope_limit) & np.isfinite(heights))
    xs = grid.x_left + (cols+c0+0.5)*pixel
    ys = grid.y_top - (rows+r0+0.5)*pixel
    distances = np.hypot(xs-x, ys-y)
    ranked = np.argsort(heights[rows, cols])[::-1]
    selected = []
    for idx in ranked:
        distance = float(distances[idx])
        px, py = float(xs[idx]), float(ys[idx])
        if not 500 <= distance <= mission.deployment_km * 1000:
            continue
        if any(np.hypot(px-item["x"], py-item["y"]) < 500 for item in selected):
            continue
        lat, lon = polar_stereographic_south_inverse(px, py)
        selected.append(dict(id=f"R{len(selected)+1:02}", x=px, y=py, lat=float(lat), lon=float(lon),
                             distance=distance/1000, slope=float(slope[rows[idx]+r0, cols[idx]+c0]),
                             elevation=float(heights[rows[idx], cols[idx]])))
        if len(selected) == 15:
            break
    return selected


def surface_path(grid, first, second):
    a, b = np.radians([first.lat, second.lat])
    delta = np.radians(second.lon-first.lon)
    bearing = np.degrees(np.arctan2(np.sin(delta)*np.cos(b), np.cos(a)*np.sin(b)-np.sin(a)*np.cos(b)*np.cos(delta)))
    angle = np.arccos(np.clip(np.sin(a)*np.sin(b)+np.cos(a)*np.cos(b)*np.cos(delta), -1, 1))
    distance = float(angle * MOON_RADIUS_M)
    fractions = np.linspace(0, 1, max(3, int(np.ceil(distance/grid.pixel_size_m))+1))[1:-1]
    samples = grid.sample(*polar_stereographic_south(*radial_points(first.lat, first.lon, bearing, distance*fractions)))
    r1, r2 = MOON_RADIUS_M + first.ground + first.mast, MOON_RADIUS_M + second.ground + second.mast
    # Intersection of the endpoint chord with each planetocentric radial ray.
    chord = (r1*r2*np.sin(angle)) / (r2*np.sin(angle*(1-fractions)) + r1*np.sin(angle*fractions))
    if not np.all(np.isfinite(samples)):
        return None, distance/1000
    return bool(np.all(chord > MOON_RADIUS_M + samples)), distance/1000


def visibility(site, point, times, mission):
    observer = SurfacePoint(point.lat, point.lon, point.ground + point.mast)
    horizon = mask_for(site, point, mission.dem_bias_m)
    earth_az, earth_el, earth_range = horizontal_arrays(Body.EARTH, observer, times)
    sun_az, sun_el, _ = horizontal_arrays(Body.SUN, observer, times)
    earth_limit, sun_limit = horizon.at(earth_az), horizon.at(sun_az)
    earth_clearance = earth_el-earth_limit-mission.clearance
    sun_clearance = sun_el-sun_limit-mission.clearance
    margin = link_margin(mission.earth_radio, earth_range)
    direct = (earth_clearance > 0) & (margin >= mission.earth_radio.margin)
    reason = np.select(
        [~np.isfinite(earth_limit), earth_clearance <= 0, margin < mission.earth_radio.margin],
        ["Insufficient terrain coverage", "Terrain obstruction or clearance", "Insufficient RF margin"],
        default="Available",
    )
    flat_limit = -np.degrees(np.arccos((MOON_RADIUS_M + point.ground) / (MOON_RADIUS_M + point.ground + point.mast)))
    reason[(earth_el <= flat_limit) & (earth_clearance <= 0)] = "Earth below geometric horizon"
    status = np.select([~np.isfinite(earth_limit), ~direct,
                        (margin < mission.earth_radio.margin+3) | (earth_clearance < 0.25)],
                       ["uncertain", "unavailable", "marginal"], default="reliable")
    sample = dict(earth_az=earth_az.tolist(), earth_el=earth_el.tolist(), earth_range_km=earth_range.tolist(),
                  earth_limit=nullable(earth_limit), obstruction_m=nullable(horizon.obstruction_distance(earth_az)),
                  earth_clearance=nullable(earth_clearance), sun_az=sun_az.tolist(), sun_el=sun_el.tolist(),
                  sun_limit=nullable(sun_limit), solar_clearance=nullable(sun_clearance),
                  solar_known=np.isfinite(sun_limit).tolist(), flat=(earth_el > flat_limit).tolist(),
                  terrain=(earth_clearance > 0).tolist(), reason=reason.tolist(), status=status.tolist(),
                  rf_margin=margin.tolist())
    return direct, sun_clearance > 0, horizon, sample


def map_data(grid, slope, x, y):
    edge = 10_000
    axis = (np.arange(128)+0.5)*(2*edge/128)-edge
    xs, ys = np.meshgrid(x+axis, y-axis)
    gradient = TerrainGrid(slope, grid.x_left, grid.y_top, grid.pixel_size_m)
    return dict(extent=edge, x=x, y=y, elevation=nullable(grid.sample(xs, ys)), slope=nullable(gradient.sample(xs, ys)))


def intervals(flags, times, end, hours, causes=None):
    stamps = (*times, end)
    return [
        dict(start=stamps[a].isoformat(), end=stamps[b].isoformat(), first=int(a), last=int(b),
             hours=float((b-a)*hours), causes=sorted(set(causes[a:b])) if causes is not None else [])
        for a, b in spans(flags)
    ]


PROVENANCE = {
    "terrain": [LOLA + product + ".LBL" for product in PRODUCTS],
    "kernels": KERNELS,
    "frame": "NAIF MOON_ME (DE440). LOLA DE421 ME coordinates are treated as coincident at DEM scale.",
    "projection": "Lunar south polar stereographic, center 90° S / 0° E, sphere radius 1,737,400 m.",
    "geometry": "Geometric center of Earth and Sun, without light-time corrections. Horizon uses 2° rays to 150 km, near DEM to 10 km.",
    "terrain_resolution": "LOLA 20 m near field and 240 m far field. Grid spacings are distinct from effective observational resolution.",
    "time": "UTC, uniformly sampled at interval starts. Changes between samples are unresolved.",
    "rf": "Free-space link estimate. Receiver threshold scales by 10 log10(rate/128 kbps). Fixed Earth receiver gains and losses. No station scheduling or antenna patterns.",
    "power": "Coupled battery/heater/thermal simulation at internal steps of at most 60 seconds. Endurance hours × node load gives nominal Wh; sunlight supplies (1 + charge ratio) × load before dust losses. Lander has its own endurance and initial charge. Earth receiver power is external.",
    "environment": "Version 1: assumed effective thermal sinks, illustrative ORA insulation and freeze limit. No Diviner products ingested; no hardware calibration. Four bounded scenarios, not a weather forecast or flight qualification. Dust affects solar output; no unsupported RF attenuation or thermal-coating coefficients.",
    "network": "Direct Earth or one independent two-hop surface relay path. No inter-relay hops. Straight-line separation and chord LOS approximate deployability.",
    "scope": "Site coordinates are approximate research points from the repository. Candidate optima are restricted to the screened DEM peaks. Planning prototype.",
    "related_work": {"NASA's 2024 candidate-region announcement": "https://www.nasa.gov/news-release/nasa-provides-update-on-artemis-iii-moon-landing-regions/",
                     "LunarRF pole-wide surface coverage study": "https://arxiv.org/abs/2610.34554"},
}


def scenario(mission, include_map=True):
    activate(mission.site)
    if mission.site not in SITES:
        raise ValueError("Select a supported landing region.")
    load_kernels(DATA / "kernels")
    grid = terrain(mission.site, "near20km")
    name, lat, lon = SITES[mission.site]
    if mission.latitude is not None:
        name, lat, lon = f"Custom point near {name}", mission.latitude, mission.longitude % 360
    x, y = map(float, polar_stereographic_south(lat, lon))
    ground = float(grid.sample(x, y)[0])
    if not np.isfinite(ground):
        raise ValueError("Custom coordinates are outside the selected site's terrain coverage.")
    start, end = [datetime.combine(day, time(), UTC) for day in (mission.start, mission.end)]
    step = timedelta(minutes=mission.minutes)
    times = tuple(start+i*step for i in range((end-start)//step))
    hours = mission.minutes/60
    lander = Observer(lat, lon, ground, mission.observer_m)
    direct, solar, horizon, sample = visibility(mission.site, lander, times, mission)
    slope = slopes(mission.site)
    options = candidates(grid, slope, x, y, mission)
    slope_surface = TerrainGrid(slope, grid.x_left, grid.y_top, grid.pixel_size_m)
    geometric_direct = direct.copy()
    quality = np.array(sample["status"])  # data quality per sample, before environment reasons are written in
    lander_states = node_states(mission, solar, sample['solar_known'], lander=True)
    direct_cases = {case: geometric_direct & lander_states[case]['available'] for case in CASES}
    relay_cases = {case: [] for case in CASES}
    direct = direct_cases['cold_dust']
    lander_state = lander_states['cold_dust']
    for i in range(len(direct)):
        if not lander_state['available'][i]:
            sample['reason'][i] = 'Lander: ' + lander_state['reason'][i]
            sample['status'][i] = 'uncertain' if not sample['solar_known'][i] else 'unavailable'
    relays = []
    geometric_relays = []
    relays_low, relays_high = {case: [] for case in CASES}, {case: [] for case in CASES}
    eligible, usable = [], []   # usable = could carry traffic; eligible = usable and allowed by the traverse rule
    for idx, candidate in enumerate(options):
        relay = Observer(candidate["lat"], candidate["lon"], candidate["elevation"], mission.mast_m)
        los, distance = surface_path(grid, lander, relay)
        local_margin = float(link_margin(mission.surface_radio, distance))
        rf, sunlight, _, relay_sample = visibility(mission.site, relay, times, mission)
        surface_ok = bool(los and local_margin >= mission.surface_radio.margin)
        legacy_power, _ = battery(sunlight, mission.battery_hours, mission.charge_ratio, hours, mission.initial_charge_pct)
        geometric_relays.append(rf & legacy_power & surface_ok)
        states = node_states(mission, sunlight, relay_sample['solar_known'], distance)
        relay_quality = np.array(relay_sample["status"])
        for case in CASES:
            path = rf & states[case]['available'] & lander_states[case]['available'] & surface_ok
            relay_cases[case].append(path)
            relays_low[case].append(path & (relay_quality == "reliable"))
            relays_high[case].append(path | ((relay_quality == "uncertain") & states[case]['available']
                                             & lander_states[case]['available'] & surface_ok))
        powered = states['cold_dust']['available']
        reserve = (states['cold_dust']['soc'] * mission.battery_hours).tolist()
        link = relay_cases['cold_dust'][-1]
        relays.append(link)
        deployment = deployment_report(grid, slope_surface, (x, y), candidate, mission)
        if any(relay_cases[case][-1].any() for case in CASES):
            usable.append(idx)
            if not (mission.avoid_hazard_routes and deployment["verdict"] == "hazard"):
                eligible.append(idx)
        candidate.update(los=los, surface_margin=local_margin, earth=metrics(rf, hours),
                         solar=metrics(sunlight, hours), power=metrics(powered, hours),
                         coverage=metrics(link, hours), recovered=float((link & ~direct).mean()*100),
                         temporal_coverage=link.tolist(), sunlight=sunlight.tolist(), powered=powered.tolist(),
                         reserve=reserve, minimum_reserve=min(reserve), minimum_earth_margin=min(relay_sample["rf_margin"]),
                         unknown_samples=relay_sample["status"].count("uncertain"),
                         environment={case: public_state(states[case], mission) for case in CASES},
                         deployment=deployment,
                         limiting=explain_candidate(los, surface_ok, local_margin >= mission.surface_radio.margin, relay_sample,
                                                    states['cold_dust'], sunlight, lander_states['cold_dust']['available'],
                                                    mission.earth_radio.margin))
    baseline_selection, baseline_feasible = optimize(geometric_direct, geometric_relays,
        [i for i, path in enumerate(geometric_relays) if path.any()], mission, options)
    baseline_metrics = evaluate(geometric_direct, geometric_relays, baseline_selection, mission)[1]
    selection, feasible = optimize_environment(direct_cases, relay_cases, eligible, mission)
    alternative = None
    if mission.avoid_hazard_routes and set(usable) != set(eligible):
        # What the plan would be if steep traverses were allowed, so the cost of the rule is visible.
        other, other_feasible = optimize_environment(direct_cases, relay_cases, usable, mission)
        alternative = dict(recommended=[options[i]["id"] for i in other], feasible=other_feasible,
                           network=evaluate(direct, relays, other, mission)[1], excluded=[options[i]["id"] for i in usable if i not in eligible])
    environment_cases = {}
    for case in CASES:
        _, values, failure, _ = evaluate(direct_cases[case], relay_cases[case], selection, mission)
        environment_cases[case] = dict(network=values, worst=failure,
                                       meets=passes(failure if mission.resilient else values, mission))
    combined, network, worst, redundant = evaluate(direct, relays, selection, mission)
    bands = {}
    for case in CASES:
        lander_ok = lander_states[case]['available']
        _, low, worst_low, _ = evaluate(direct_cases[case] & (quality == "reliable"), relays_low[case], selection, mission)
        _, high, worst_high, _ = evaluate(direct_cases[case] | ((quality == "uncertain") & lander_ok), relays_high[case], selection, mission)
        bands[case] = dict(low=low, high=high, worst_low=worst_low, worst_high=worst_high)
    _, base, _, _ = evaluate(direct, relays, (), mission)
    def all_cases_pass(active):
        return all(passes(evaluate(direct_cases[c], relay_cases[c], active, mission)[1], mission) for c in CASES)
    failures = []
    for disabled in selection:
        active = tuple(idx for idx in selection if idx != disabled)
        remaining, result, _, _ = evaluate(direct, relays, active, mission)
        failures.append(dict(id=options[disabled]["id"], metrics=result, meets=all_cases_pass(active),
                             windows_lost=int(len(spans(~(combined & ~remaining)))),
                             availability_lost=network["availability"]-result["availability"],
                             blackout_increase=result["longest"]-network["longest"]))
    failure_states = {}
    for count in range(len(selection)+1):
        for removed in combinations(selection, count):
            active = tuple(idx for idx in selection if idx not in removed)
            online, values, _, _ = evaluate(direct, relays, active, mission)
            assisted = np.logical_or.reduce([relays[idx] for idx in active], axis=0) if active else np.zeros_like(direct)
            key = ",".join(sorted(options[idx]["id"] for idx in removed)) or "nominal"
            failure_states[key] = dict(
                combined=online.tolist(), assisted=assisted.tolist(), metrics=values, meets=all_cases_pass(active))
    power_loss = power_loss_simulation(direct, relays, selection, options, times, end, mission, mission.power_loss_hours)
    sun_metrics = metrics(solar, hours)
    details = site_details(grid, slope, x, y)
    result = dict(site=dict(name=name, lat=lat, lon=lon, elevation=ground, **details),
                  config=mission.model_dump(mode="json"), times=[stamp.isoformat() for stamp in times],
                  direct=base, solar=sun_metrics, network=network, worst=worst, redundant=redundant,
                  outages=intervals(direct, times, end, hours, sample["reason"]),
                  darkness=intervals(solar, times, end, hours),
                  recommended=[options[idx]["id"] for idx in selection], feasible=feasible, eligible=len(eligible),
                  cost=cost_report(len(selection), mission.budget_usd, mission.relay_cost_usd),
                  darkness_meets=sun_metrics["longest"] <= mission.darkness_hours,
                  failures=failures, failure_states=failure_states, power_loss=power_loss, candidates=options,
                  deployment=deployment_summary(options, selection, mission, alternative),
                  horizon=dict(azimuth=horizon.azimuth_deg.tolist(), elevation=nullable(horizon.elevation_deg),
                               distance=nullable(horizon.distance_m)),
                  samples=dict(**sample, direct=direct.tolist(), solar=solar.tolist(), network=combined.tolist()),
                  environment=dict(version=1, status='assumed', validated=False,
                                   primary_case='cold_dust', cases=environment_cases,
                                   lander={case: public_state(lander_states[case], mission) for case in CASES},
                                   geometric_direct=metrics(geometric_direct, hours),
                                   simplified_baseline=dict(network=baseline_metrics, feasible=baseline_feasible,
                                       recommended=[options[i]['id'] for i in baseline_selection]),
                                   assumptions='Effective sink envelopes and dust deposition are user assumptions. No Diviner data or hardware calibration; Earth receiver power is external.'),
                  provenance=PROVENANCE)
    result["assessment"] = mission_assessment(mission, environment_cases, bands, sun_metrics, details, selection)
    if include_map:
        result["map"] = map_data(grid, slope, x, y)
    return result


def deployment_summary(options, selection, mission, alternative=None):
    counts = {}
    for option in options:
        counts[option["deployment"]["verdict"]] = counts.get(option["deployment"]["verdict"], 0) + 1
    chosen = [options[idx]["deployment"] for idx in selection]
    return dict(counts=counts, enforced=mission.avoid_hazard_routes,
                slope_limit=mission.route_slope_limit if mission.route_slope_limit is not None else mission.slope_limit,
                if_steep_allowed=alternative, selected_all_feasible=all(item["verdict"] == "feasible" for item in chosen),
                longest_route_km=max((item["route_km"] for item in chosen), default=0.0),
                assumption="Straight-line traverse over a 20 m DEM from the lander to each mast; no route planning, regolith strength or sub-pixel hazards.")


def bounded(name, target, nominal, low, high, better_high, unit, basis):
    """Requirement check decided only if it holds with marginal samples lost, or fails even with unknown samples kept."""
    ok = (lambda v: v >= target) if better_high else (lambda v: v <= target)
    met = True if ok(low) else False if not ok(high) else None
    value = dict(nominal=nominal, if_marginal_samples_lost=low, if_unknown_samples_available=high, unit=unit)
    return check(name, f"{'≥' if better_high else '≤'} {target:g} {unit}", value, met, basis)


CASE_NAMES = {"nominal": "nominal", "cold": "cold sink", "dust": "dust event", "cold_dust": "cold sink with dust"}


def mission_assessment(mission, cases, bands, solar, details, selection):
    """Requirements status for the selected plan, taking the worst of the four assumed environment cases.

    Each check is decided under the stated assumptions (radio budgets, battery, thermal sinks, dust), which are
    listed with the result. It is undecided only when marginal or missing samples could change the answer.
    """
    basis = ("Worst of the four assumed environment cases. Decided only when the answer is the same with marginal samples "
             "(within 3 dB of the RF threshold or 0.25° of the horizon) counted as lost and with samples lacking terrain counted as available.")
    worst_case = lambda key, metric, pick: pick(cases, key=lambda c: cases[c][key][metric])

    def across(key, low_key, high_key, metric, better_high):
        pick = min if better_high else max
        nominal = pick(cases[c][key][metric] for c in cases)
        low = pick(bands[c][low_key][metric] for c in cases)
        high = pick(bands[c][high_key][metric] for c in cases)
        if len({round(cases[c][key][metric], 9) for c in cases}) == 1:
            return nominal, low, high, "all four cases agree"
        limiting = worst_case(key, metric, pick)
        return nominal, low, high, f"limiting case: {CASE_NAMES.get(limiting, limiting)}"

    checks = []
    for label, metric, target, better, unit in (("Network availability", "availability", mission.availability, True, "%"),
                                                ("Longest communication blackout", "longest", mission.blackout_hours, False, "h")):
        nominal, low, high, limiting = across("network", "low", "high", metric, better)
        checks.append(bounded(f"{label} ({limiting})", target, nominal, low, high, better, unit, basis))
    checks.append(check("Longest darkness at the landing point", f"≤ {mission.darkness_hours:g} h", solar["longest"],
                        solar["longest"] <= mission.darkness_hours, "Sun geometry against the terrain horizon; no band applied."))
    if mission.resilient and selection:
        for label, metric, target, better, unit in (("Availability after the worst single relay loss", "availability", mission.availability, True, "%"),
                                                    ("Blackout after the worst single relay loss", "longest", mission.blackout_hours, False, "h")):
            nominal, low, high, limiting = across("worst", "worst_low", "worst_high", metric, better)
            checks.append(bounded(f"{label} ({limiting})", target, nominal, low, high, better, unit, basis))
    gaps = [gap("Assumed environment (version 1)", "Thermal sinks, insulation, heater, freeze limit and dust deposition are planning "
                "assumptions with no Diviner data or hardware calibration. The status holds under these values only.", False)]
    if details.get("terrain_coverage", 100) < 100:
        gaps.append(gap("Terrain coverage at the landing point", f"{details['terrain_coverage']:.0f}% of the 220 m patch has elevation data.", False))
    if mission.dem_bias_m:
        gaps.append(gap("Terrain bias applied", f"Horizon terrain raised by {mission.dem_bias_m:g} m as a sensitivity case.", False))
    gaps.append(gap("Sampling interval", f"Samples every {mission.minutes} min; changes between samples are unresolved.", False))
    modelled = ("Equipment temperature", "Dust accumulation")
    return assess(
        checks, gaps, kind="scenario outcome", subject="mission communication under the assumed environment envelopes",
        architecture=PROVENANCE["network"],
        evaluated=["Terrain obstruction of Earth (LOLA 20 m near field, 240 m far field)", "Earth and Sun position (NAIF SPICE, DE440)",
                   "Free-space RF margin to Earth and between surface nodes", "Darkness at each node from terrain shadowing",
                   "Battery, heater and node temperature through darkness (lumped model, assumed sinks, four cases)",
                   "Local dust deposition reducing solar output (assumed event)", "Loss of any one selected relay (what-if), and a timed power loss of each relay",
                   "Slope at each mast, and slope along a straight traverse from the lander (screening, not route planning)",
                   "Which constraint limits each candidate: terrain, RF, power or temperature"],
        not_evaluated=["Calibrated thermal and dust behaviour (current values are assumptions)"]
                      + [h for h in NOT_MODELLED if not h.startswith(modelled)],
        assumptions=[f"Earth link: {mission.earth_radio.ghz:g} GHz, {mission.earth_radio.watts:g} W, receiver gain {mission.earth_radio.rx_gain:g} dBi, "
                     f"required margin {mission.earth_radio.margin:g} dB",
                     f"Surface link: {mission.surface_radio.ghz:g} GHz, {mission.surface_radio.watts:g} W, required margin {mission.surface_radio.margin:g} dB",
                     f"Relay battery {mission.battery_hours:g} h at {mission.node_load_w:g} W, charge ratio {mission.charge_ratio:g}, starting at {mission.initial_charge_pct:g}%; "
                     f"lander {mission.lander_battery_hours:g} h starting at {mission.lander_initial_charge_pct:g}%",
                     f"Thermal sinks: dark {mission.dark_sink_k:g} K, cold case {mission.cold_sink_k:g} K, lit {mission.lit_sink_k:g} K; "
                     f"heater on below {mission.heater_on_k:g} K, freeze at {mission.freeze_k:g} K",
                     f"Dust event from hour {mission.dust_start_h:g} for {mission.dust_duration_h:g} h within {mission.dust_radius_km:g} km, "
                     f"{mission.dust_deposition_per_day:g}/day deposition, {mission.dust_solar_loss:g} solar loss at full load",
                     f"Lander antenna {mission.observer_m:g} m, relay mast {mission.mast_m:g} m, clearance {mission.clearance:g}°",
                     PROVENANCE["power"], PROVENANCE["rf"]])


@lru_cache(maxsize=3)
def slopes(site):
    return slope_grid(terrain(site, "near20km"))


def site_details(grid, slope, x, y):
    r, c = int((grid.y_top-y)/grid.pixel_size_m), int((x-grid.x_left)/grid.pixel_size_m)
    patch = slope[max(0, r-5):r+6, max(0, c-5):c+6]
    finite = patch[np.isfinite(patch)]
    return dict(mean_slope=float(finite.mean()) if finite.size else None,
                max_slope=float(finite.max()) if finite.size else None,
                terrain_coverage=float(finite.size/121*100), resolution=grid.pixel_size_m)
