"""Recompute a mission from NASA ephemerides and terrain."""
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from itertools import combinations

import numpy as np
from scipy.ndimage import maximum_filter

from lunar_core.geometry import Body, SurfacePoint, horizontal_arrays, satellite_earth_arrays, satellite_horizontal_arrays, satellite_vectors
from lunar_core.horizon import horizon_mask
from lunar_core.kernels import KERNELS, load_kernels
from lunar_core.orbit import PRESETS, Orbit
from lunar_core.terrain import (
    MOON_RADIUS_M, TerrainGrid, polar_stereographic_south, polar_stereographic_south_inverse, radial_points,
)

from .costs import cost_report
from .diagnostics import (add_path, attach_outages, earth_backhaul, gate, node_evidence,
                          optimization_evidence, sky_link)
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


def candidates(grid, slope, x, y, mission, screening=None):
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
    visited = 0
    if screening is not None:
        screening.update(finite_pixels=int(np.isfinite(heights).sum()), local_peaks=int(peaks.sum()),
            slope_and_data_pass=len(rows), rejected_radius=0, rejected_spacing=0,
            rules=dict(minimum_separation_m=500, minimum_lander_distance_m=500,
                       deployment_radius_km=mission.deployment_km, slope_limit_deg=mission.slope_limit,
                       catalog_cap=15, peak_filter_pixels=max(3, round(420/pixel))))
    for idx in ranked:
        visited += 1
        distance = float(distances[idx])
        px, py = float(xs[idx]), float(ys[idx])
        if not 500 <= distance <= mission.deployment_km * 1000:
            if screening is not None:
                screening['rejected_radius'] += 1
            continue
        if any(np.hypot(px-item["x"], py-item["y"]) < 500 for item in selected):
            if screening is not None:
                screening['rejected_spacing'] += 1
            continue
        lat, lon = polar_stereographic_south_inverse(px, py)
        selected.append(dict(id=f"R{len(selected)+1:02}", x=px, y=py, lat=float(lat), lon=float(lon),
                             distance=distance/1000, slope=float(slope[rows[idx]+r0, cols[idx]+c0]),
                             elevation=float(heights[rows[idx], cols[idx]])))
        if len(selected) == 15:
            break
    if screening is not None:
        screening['unvisited_after_cap'] = len(ranked) - visited
    return selected


def surface_path(grid, first, second, evidence=None):
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
        if evidence is not None:
            evidence.update(location=None, minimum_clearance_m=None,
                todo='TODO: surface-path terrain coverage is incomplete.')
        return None, distance/1000
    if evidence is not None:
        clearance = chord - MOON_RADIUS_M - samples
        index = int(np.argmin(clearance))
        lat, lon = radial_points(first.lat, first.lon, bearing, distance*fractions[index])
        map_x, map_y = polar_stereographic_south(lat, lon)
        evidence.update(minimum_clearance_m=float(clearance[index]), todo=None,
            location=dict(map_x_m=float(map_x), map_y_m=float(map_y), lat=float(lat), lon=float(lon), distance_m=float(distance*fractions[index]),
                          approximate=True, basis='Minimum clearance at a sampled surface-path point.'))
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


def build_orbit(config, start):
    """lunar_core Orbit from the mission's satellite block; the epoch is mission start."""
    if config.preset == "custom":
        elements = dict(semi_major_axis_km=config.semi_major_axis_km, eccentricity=config.eccentricity,
                        inclination_deg=config.inclination_deg, raan_deg=config.raan_deg,
                        arg_periapsis_deg=config.arg_periapsis_deg, mean_anomaly_deg=config.mean_anomaly_deg)
    else:
        elements = dict(PRESETS[config.preset])
        elements.update({k: v for k, v in dict(
            semi_major_axis_km=config.semi_major_axis_km, eccentricity=config.eccentricity,
            inclination_deg=config.inclination_deg, raan_deg=config.raan_deg,
            arg_periapsis_deg=config.arg_periapsis_deg, mean_anomaly_deg=config.mean_anomaly_deg).items() if v is not None})
    return Orbit(**elements, epoch=config.epoch.astimezone(UTC) if config.epoch else start)


def satellite_earth_link(config, orbit, times):
    """Shared satellite -> Earth hop: Moon occultation and (when a radio is given) link margin."""
    distance, occulted = satellite_earth_arrays(orbit, times)
    margin = link_margin(config.sat_to_earth_radio, distance) if config.sat_to_earth_radio else None
    ok = ~occulted
    if margin is not None:
        ok &= margin >= config.sat_to_earth_radio.margin
    marginal = ok & (margin < config.sat_to_earth_radio.margin + 3) if margin is not None else np.zeros_like(ok)
    return ok, dict(marginal=marginal.tolist(), available=ok.tolist(), reason=np.select([occulted, ~ok], ["Moon occultation", "Insufficient RF margin"], default="Available").tolist(), occulted=occulted.tolist(), range_km=distance.tolist(),
                    margin=None if margin is None else margin.tolist())


def satellite_access(site, point, times, mission, orbit):
    """Surface node -> satellite: above its terrain horizon and the elevation mask, with margin if a radio is set."""
    config = mission.satellite
    observer = SurfacePoint(point.lat, point.lon, point.ground + point.mast)
    az, el, distance = satellite_horizontal_arrays(orbit, observer, times)
    limit = mask_for(site, point, mission.dem_bias_m).at(az)
    ok = np.isfinite(limit) & (el - limit - mission.clearance > 0) & (el >= config.min_elevation_deg)
    margin = link_margin(config.surface_to_sat_radio, distance) if config.surface_to_sat_radio else None
    if margin is not None:
        ok &= margin >= config.surface_to_sat_radio.margin
    marginal = ok & ((el - limit - mission.clearance < 0.25) | (el - config.min_elevation_deg < 0.25))
    if margin is not None:
        marginal |= ok & (margin < config.surface_to_sat_radio.margin + 3)
    return ok, dict(marginal=marginal.tolist(), available=ok.tolist(), reason=np.select(
        [~np.isfinite(limit), el - limit - mission.clearance <= 0, el < config.min_elevation_deg, ~ok],
        ["Unknown terrain horizon", "Terrain obstruction", "Below elevation mask", "Insufficient RF margin"], default="Available").tolist(),
        az=az.tolist(), el=el.tolist(), range_km=distance.tolist(),
                    margin=None if margin is None else margin.tolist())


def via_satellite(earth_ok, sat_ok, sample, marginal=None):
    """Earth reachability for a node: direct OR satellite hop. Rescued samples are no longer 'unavailable'."""
    rescued = sat_ok & (~earth_ok | (np.array(sample["status"]) != "reliable"))
    for i in np.flatnonzero(rescued):
        if sample["status"][i] != "reliable":
            sample["status"][i] = "marginal" if marginal is not None and marginal[i] else "reliable"
            sample["reason"][i] = "Available via satellite"
    return earth_ok | sat_ok


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
    diagnostic = dict(version=1, status='simulated', primary_case='cold_dust', cases=list(CASES),
        sample_minutes=mission.minutes, interval_convention='[start, end), UTC',
        timing='Interval-start geometry; transitions between samples are unresolved. Boundary resolution is one configured sampling step, not a physical timing guarantee.',
        nodes={'Lander': node_evidence(lander, {}, sample['solar_known'], mission),
               'Earth': dict(location=None, todo='TODO: Earth receiver station is not specified.')},
        links={}, paths={})
    diagnostic['links']['Lander->Earth'] = sky_link('Lander', 'Earth', lander, horizon, sample, mission)
    orbit = sat_hop = sat_info = None
    direct_nosat = direct
    quality_nosat = np.array(sample["status"])
    if mission.satellite:
        orbit = build_orbit(mission.satellite, start)
        sat_hop, sat_earth_info = satellite_earth_link(mission.satellite, orbit, times)
        lander_sat, lander_sat_info = satellite_access(mission.site, lander, times, mission, orbit)
        direct = via_satellite(direct, lander_sat & sat_hop, sample,
            np.array(lander_sat_info["marginal"]) | np.array(sat_earth_info["marginal"]))
        sat_info = dict(lander=lander_sat_info, earth=sat_earth_info)
        diagnostic['nodes']['S1'] = dict(location=None, track_ref='satellite.track',
            assumptions='Satellite power and pointing assumed available; no hardware state model.')
        diagnostic['links']['Lander->S1'] = sky_link('Lander', 'S1', lander, horizon, lander_sat_info, mission)
        diagnostic['links']['S1->Earth'] = earth_backhaul(sat_earth_info, mission)
    slope = slopes(mission.site)
    screening = {}
    options = candidates(grid, slope, x, y, mission, screening)
    geometric_direct = direct.copy()
    quality = np.array(sample["status"])  # data quality per sample, before environment reasons are written in
    lander_states = node_states(mission, solar, sample['solar_known'], lander=True)
    diagnostic['nodes']['Lander'] = node_evidence(lander, lander_states, sample['solar_known'], mission)
    add_path(diagnostic, 'Lander->Earth', ['Lander', 'Earth'], ['Lander->Earth'])
    if orbit:
        add_path(diagnostic, 'Lander->S1->Earth', ['Lander', 'S1', 'Earth'], ['Lander->S1', 'S1->Earth'])
    direct_cases = {case: geometric_direct & lander_states[case]['available'] for case in CASES}
    direct_cases_nosat = {case: direct_nosat & lander_states[case]['available'] for case in CASES}
    relay_cases_nosat = {case: [] for case in CASES}
    relay_nosat_low, relay_nosat_high = {case: [] for case in CASES}, {case: [] for case in CASES}
    relay_cases = {case: [] for case in CASES}
    direct = direct_cases['cold_dust']
    lander_state = lander_states['cold_dust']
    for i in range(len(direct)):
        if not lander_state['available'][i]:
            sample['reason'][i] = 'Lander: ' + lander_state['reason'][i]
            sample['status'][i] = 'uncertain' if not sample['solar_known'][i] else 'unavailable'
    relays = []
    geometric_relays = []
    geometric_relays_nosat = []
    relays_low, relays_high = {case: [] for case in CASES}, {case: [] for case in CASES}
    eligible = []
    for idx, candidate in enumerate(options):
        relay = Observer(candidate["lat"], candidate["lon"], candidate["elevation"], mission.mast_m)
        surface_evidence = {}
        los, distance = surface_path(grid, lander, relay, surface_evidence)
        local_margin = float(link_margin(mission.surface_radio, distance))
        rf, sunlight, relay_horizon, relay_sample = visibility(mission.site, relay, times, mission)
        relay_id = candidate['id']
        diagnostic['links'][f'{relay_id}->Earth'] = sky_link(relay_id, 'Earth', relay, relay_horizon, relay_sample, mission)
        rf_nosat = rf
        relay_quality_nosat = np.array(relay_sample["status"])
        if orbit:
            relay_sat, relay_sat_info = satellite_access(mission.site, relay, times, mission, orbit)
            diagnostic['links'][f'{relay_id}->S1'] = sky_link(relay_id, 'S1', relay, relay_horizon, relay_sat_info, mission)
            rf = via_satellite(rf, relay_sat & sat_hop, relay_sample,
                np.array(relay_sat_info["marginal"]) | np.array(sat_earth_info["marginal"]))
        surface_ok = bool(los and local_margin >= mission.surface_radio.margin)
        legacy_power, _ = battery(sunlight, mission.battery_hours, mission.charge_ratio, hours, mission.initial_charge_pct)
        geometric_relays.append(rf & legacy_power & surface_ok)
        geometric_relays_nosat.append(rf_nosat & legacy_power & surface_ok)
        states = node_states(mission, sunlight, relay_sample['solar_known'], distance)
        diagnostic['nodes'][relay_id] = node_evidence(relay, states, relay_sample['solar_known'], mission)
        surface_link_id = f'Lander->{relay_id}'
        diagnostic['links'][surface_link_id] = dict(id=surface_link_id, nodes=['Lander', relay_id],
            range_km=[distance]*len(times), available=[surface_ok]*len(times), geometry_only=False,
            gates=[gate('surface_los', [los]*len(times)),
                   gate('rf_margin', [local_margin >= mission.surface_radio.margin]*len(times),
                        [local_margin]*len(times), mission.surface_radio.margin, 'dB')], **surface_evidence)
        add_path(diagnostic, f'Lander->{relay_id}->Earth', ['Lander', relay_id, 'Earth'],
                 [surface_link_id, f'{relay_id}->Earth'], relay_id)
        if orbit:
            add_path(diagnostic, f'Lander->{relay_id}->S1->Earth', ['Lander', relay_id, 'S1', 'Earth'],
                     [surface_link_id, f'{relay_id}->S1', 'S1->Earth'], relay_id)
        relay_quality = np.array(relay_sample["status"])
        for case in CASES:
            path = rf & states[case]['available'] & lander_states[case]['available'] & surface_ok
            relay_cases[case].append(path)
            relay_cases_nosat[case].append(rf_nosat & states[case]['available'] & lander_states[case]['available'] & surface_ok)
            relay_nosat_low[case].append(relay_cases_nosat[case][-1] & (relay_quality_nosat == 'reliable'))
            relay_nosat_high[case].append(relay_cases_nosat[case][-1] | ((relay_quality_nosat == 'uncertain') & states[case]['available'] & lander_states[case]['available'] & surface_ok))
            relays_low[case].append(path & (relay_quality == "reliable"))
            relays_high[case].append(path | ((relay_quality == "uncertain") & states[case]['available']
                                             & lander_states[case]['available'] & surface_ok))
        powered = states['cold_dust']['available']
        reserve = (states['cold_dust']['soc'] * mission.battery_hours).tolist()
        link = relay_cases['cold_dust'][-1]
        relays.append(link)
        if any(relay_cases[case][-1].any() for case in CASES):
            eligible.append(idx)
        candidate['eligibility'] = dict(eligible=idx in eligible, reasons=(
            ['Usable complete path in at least one environmental case'] if idx in eligible else
            (['Unknown surface terrain'] if los is None else []) +
            (['Surface terrain obstruction'] if los is False else []) +
            (['Surface RF below configured threshold'] if local_margin < mission.surface_radio.margin else []) +
            ['No usable complete path in any environmental case']))
        candidate.update(los=los, surface_margin=local_margin, earth=metrics(rf, hours),
                         solar=metrics(sunlight, hours), power=metrics(powered, hours),
                         coverage=metrics(link, hours), recovered=float((link & ~direct).mean()*100),
                         temporal_coverage=link.tolist(), sunlight=sunlight.tolist(), powered=powered.tolist(),
                         reserve=reserve, minimum_reserve=min(reserve), minimum_earth_margin=min(relay_sample["rf_margin"]),
                         unknown_samples=relay_sample["status"].count("uncertain"),
                         environment={case: public_state(states[case], mission) for case in CASES})
        if orbit:
            candidate["satellite"] = dict(access=metrics(relay_sat, hours), **relay_sat_info)
    baseline_selection, baseline_feasible = optimize(geometric_direct, geometric_relays,
        [i for i, path in enumerate(geometric_relays) if path.any()], mission, options,
        (direct_nosat, geometric_relays_nosat) if orbit else None)
    baseline_metrics = evaluate(geometric_direct, geometric_relays, baseline_selection, mission)[1]
    fallback = (direct_cases_nosat, relay_cases_nosat) if orbit else None
    search_records = []
    selection, feasible = optimize_environment(direct_cases, relay_cases, eligible, mission, fallback, search_records)
    optimization = optimization_evidence(search_records, selection, options, direct_cases, relay_cases, mission, fallback)
    screening.update(catalog_size=len(options), eligible=len(eligible))
    optimization['screening'] = screening
    environment_cases = {}
    for case in CASES:
        _, values, failure, _ = evaluate(direct_cases[case], relay_cases[case], selection, mission,
            (direct_cases_nosat[case], relay_cases_nosat[case]) if orbit else None)
        environment_cases[case] = dict(network=values, worst=failure,
                                       meets=passes(failure if mission.resilient else values, mission))
    combined, network, worst, redundant = evaluate(direct, relays, selection, mission,
        (direct_cases_nosat["cold_dust"], relay_cases_nosat["cold_dust"]) if orbit else None)
    satellite = None
    if orbit:
        _, lost, _, _ = evaluate(direct_cases_nosat['cold_dust'], relay_cases_nosat['cold_dust'], selection, mission)
        vectors = satellite_vectors(orbit, times)
        radius = np.linalg.norm(vectors, axis=1)
        sat_config = mission.satellite
        satellite = dict(
            status="simulated", preset=sat_config.preset, period_min=orbit.period_s/60,
            elements=dict(semi_major_axis_km=orbit.semi_major_axis_km, eccentricity=orbit.eccentricity,
                          inclination_deg=orbit.inclination_deg, raan_deg=orbit.raan_deg,
                          arg_periapsis_deg=orbit.arg_periapsis_deg, mean_anomaly_deg=orbit.mean_anomaly_deg,
                          epoch=orbit.epoch.isoformat()),
            rf_evaluated=dict(surface_to_sat=sat_config.surface_to_sat_radio is not None,
                              sat_to_earth=sat_config.sat_to_earth_radio is not None),
            min_elevation_deg=sat_config.min_elevation_deg,
            direct_earth=metrics(direct_cases_nosat["cold_dust"], hours),
            earth_hop=metrics(sat_hop, hours), lander_access=metrics(lander_sat, hours),
            lander_path=metrics(lander_sat & sat_hop, hours),
            network_without_satellite=lost, availability_lost_if_satellite_lost=network["availability"]-lost["availability"],
            meets_without_satellite=passes(lost, mission),
            track=dict(lat=np.degrees(np.arcsin(vectors[:, 2]/radius)).tolist(),
                       lon=np.degrees(np.arctan2(vectors[:, 1], vectors[:, 0])).tolist(),
                       altitude_km=(radius - 1737.4).tolist()),
            earth=sat_info["earth"], lander=sat_info["lander"],
            outages=intervals(lander_sat & sat_hop, times, end, hours),
            validation="Two-body analytic invariants and synthetic geometry only; no independent spacecraft ephemeris cross-check.",
            cost_usd=None, cost_todo="TODO: supply satellite service cost; excluded from surface relay budget.",
            assumptions="Satellite power and pointing continuously available; capacity, scheduling, handover and hardware survival not evaluated.")
    bands = {}
    for case in CASES:
        lander_ok = lander_states[case]['available']
        _, low, worst_low, _ = evaluate(direct_cases[case] & (quality == "reliable"), relays_low[case], selection, mission,
            (direct_cases_nosat[case] & (quality_nosat == "reliable"), relay_nosat_low[case]) if orbit else None)
        _, high, worst_high, _ = evaluate(direct_cases[case] | ((quality == "uncertain") & lander_ok), relays_high[case], selection, mission,
            (direct_cases_nosat[case] | ((quality_nosat == "uncertain") & lander_ok), relay_nosat_high[case]) if orbit else None)
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
    if orbit:
        lost_state = None
        for count in range(len(selection)+1):
            for removed in combinations(selection, count):
                active = tuple(idx for idx in selection if idx not in removed)
                online, values, _, _ = evaluate(direct_cases_nosat['cold_dust'], relay_cases_nosat['cold_dust'], active, mission)
                assisted = np.logical_or.reduce([relay_cases_nosat['cold_dust'][idx] for idx in active], axis=0) if active else np.zeros_like(direct)
                key = ",".join(sorted(["S1", *[options[idx]['id'] for idx in removed]]))
                state = dict(combined=online.tolist(), assisted=assisted.tolist(), metrics=values,
                    meets=all(passes(evaluate(direct_cases_nosat[c], relay_cases_nosat[c], active, mission)[1], mission) for c in CASES))
                failure_states[key] = state
                if not removed:
                    lost_state = state
        failures.append(dict(id="S1", metrics=lost_state['metrics'], meets=lost_state['meets'],
            windows_lost=int(len(spans(~(combined & ~np.array(lost_state['combined']))))),
            availability_lost=network['availability']-lost_state['metrics']['availability'],
            blackout_increase=lost_state['metrics']['longest']-network['longest']))
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
                  failures=failures, failure_states=failure_states, candidates=options,
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
                  satellite=satellite, provenance={**PROVENANCE, **({"network": "Simulated direct Earth, surface relay to Earth, or either surface node through one shared satellite to Earth.", "orbit": satellite["validation"] + " " + satellite["assumptions"]} if satellite else {})})
    result['optimization_evidence'] = optimization
    attach_outages(result, diagnostic, times, end, mission)
    from ora.status import git_state
    result['provenance']['code'] = git_state()
    result["assessment"] = mission_assessment(mission, environment_cases, bands, sun_metrics, details, selection, satellite)
    if include_map:
        result["map"] = map_data(grid, slope, x, y)
    return result


def bounded(name, target, nominal, low, high, better_high, unit, basis):
    """Requirement check decided only if it holds with marginal samples lost, or fails even with unknown samples kept."""
    ok = (lambda v: v >= target) if better_high else (lambda v: v <= target)
    met = True if ok(low) else False if not ok(high) else None
    value = dict(nominal=nominal, if_marginal_samples_lost=low, if_unknown_samples_available=high, unit=unit)
    return check(name, f"{'≥' if better_high else '≤'} {target:g} {unit}", value, met, basis)


CASE_NAMES = {"nominal": "nominal", "cold": "cold sink", "dust": "dust event", "cold_dust": "cold sink with dust"}


def mission_assessment(mission, cases, bands, solar, details, selection, satellite=None):
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
    if mission.resilient and (selection or satellite):
        for label, metric, target, better, unit in (("Availability after the worst single node loss", "availability", mission.availability, True, "%"),
                                                    ("Blackout after the worst single node loss", "longest", mission.blackout_hours, False, "h")):
            nominal, low, high, limiting = across("worst", "worst_low", "worst_high", metric, better)
            checks.append(bounded(f"{label} ({limiting})", target, nominal, low, high, better, unit, basis))
    gaps = [gap("Assumed environment (version 1)", "Thermal sinks, insulation, heater, freeze limit and dust deposition are planning "
                "assumptions with no Diviner data or hardware calibration. The status holds under these values only.", False)]
    if details.get("terrain_coverage", 100) < 100:
        gaps.append(gap("Terrain coverage at the landing point", f"{details['terrain_coverage']:.0f}% of the 220 m patch has elevation data.", False))
    if mission.dem_bias_m:
        gaps.append(gap("Terrain bias applied", f"Horizon terrain raised by {mission.dem_bias_m:g} m as a sensitivity case.", False))
    gaps.append(gap("Sampling interval", f"Samples every {mission.minutes} min; changes between samples are unresolved.", False))
    modelled = ("Equipment temperature", "Dust accumulation") + (("Lunar orbiting relay",) if satellite else ())
    architecture = PROVENANCE["network"]
    evaluated = ["Terrain obstruction of Earth (LOLA 20 m near field, 240 m far field)", "Earth and Sun position (NAIF SPICE, DE440)",
                 "Free-space RF margin to Earth and between surface nodes", "Darkness at each node from terrain shadowing",
                 "Battery, heater and node temperature through darkness (lumped model, assumed sinks, four cases)",
                 "Local dust deposition reducing solar output (assumed event)", "Loss of any one selected relay (what-if)"]
    satellite_assumptions = []
    if satellite:
        architecture = ("Direct Earth, or surface node to a simulated orbiting relay satellite to Earth, or lander to one surface relay "
                        "(either of which may itself use the satellite). One satellite shared by every path; no inter-satellite links.")
        evaluated.append("Simulated satellite: two-body orbit, terrain-masked access from each node, Moon occultation of the satellite-Earth link")
        evaluated.append("Loss of the satellite (what-if)")
        gaps.append(gap("Satellite validation and operations", satellite["validation"] + " " + satellite["assumptions"], False))
        gaps.append(gap("Satellite cost unknown", satellite["cost_todo"], False))
        satellite_assumptions = [f"Satellite {satellite['preset']}: a={satellite['elements']['semi_major_axis_km']:.0f} km, e={satellite['elements']['eccentricity']:g}, "
                                 f"i={satellite['elements']['inclination_deg']:g}°, period {satellite['period_min']:.1f} min, minimum elevation {satellite['min_elevation_deg']:g}°. "
                                 "Point-mass Moon, no perturbations, no light-time, orbit simulated (not a flight orbit)"]
        gaps.append(gap("Satellite loss", f"Without the satellite, availability falls by {satellite['availability_lost_if_satellite_lost']:.1f} points "
                        f"(longest blackout {satellite['network_without_satellite']['longest']:g} h); requirements "
                        f"{'still hold' if satellite['meets_without_satellite'] else 'would not hold'}.", False))
        for hop, key in (("Surface-to-satellite", "surface_to_sat"), ("Satellite-to-Earth", "sat_to_earth")):
            if not satellite["rf_evaluated"][key]:
                gaps.append(gap(f"{hop} RF not evaluated", "No verified radio parameters are configured (TODO), so this hop is geometry only: "
                                "access and occultation without link margin. Availability through the satellite is an upper bound.", True))
    return assess(
        checks, gaps, kind="scenario outcome", subject="mission communication under the assumed environment envelopes",
        architecture=architecture, evaluated=evaluated,
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
                     PROVENANCE["power"], PROVENANCE["rf"]] + satellite_assumptions)


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
