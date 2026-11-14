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

from .data import DATA, LOLA, PRODUCTS, SITES
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
    "power": "Battery starts at the configured charge. In sunlight the load operates and charges at a configurable net rate. In darkness it draws at a constant load.",
    "network": "Direct Earth or one independent two-hop surface relay path. No inter-relay hops. Straight-line separation and chord LOS approximate deployability.",
    "scope": "Site coordinates are approximate research points from the repository. Candidate optima are restricted to the screened DEM peaks. Planning prototype.",
    "related_work": {"NASA's 2024 candidate-region announcement": "https://www.nasa.gov/news-release/nasa-provides-update-on-artemis-iii-moon-landing-regions/",
                     "LunarRF pole-wide surface coverage study": "https://arxiv.org/abs/2610.34554"},
}


def scenario(mission, include_map=True):
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
    relays = []
    eligible = []
    for idx, candidate in enumerate(options):
        relay = Observer(candidate["lat"], candidate["lon"], candidate["elevation"], mission.mast_m)
        los, distance = surface_path(grid, lander, relay)
        local_margin = float(link_margin(mission.surface_radio, distance))
        rf, sunlight, _, relay_sample = visibility(mission.site, relay, times, mission)
        powered, reserve = battery(sunlight, mission.battery_hours, mission.charge_ratio, hours, mission.initial_charge_pct)
        link = rf & powered & bool(los and local_margin >= mission.surface_radio.margin)
        relays.append(link)
        if link.any():
            eligible.append(idx)
        candidate.update(los=los, surface_margin=local_margin, earth=metrics(rf, hours),
                         solar=metrics(sunlight, hours), power=metrics(powered, hours),
                         coverage=metrics(link, hours), recovered=float((link & ~direct).mean()*100),
                         temporal_coverage=link.tolist(), sunlight=sunlight.tolist(), powered=powered.tolist(),
                         reserve=reserve, minimum_reserve=min(reserve), minimum_earth_margin=min(relay_sample["rf_margin"]),
                         unknown_samples=relay_sample["status"].count("uncertain"))
    selection, feasible = optimize(direct, relays, eligible, mission, options)
    combined, network, worst, redundant = evaluate(direct, relays, selection, mission)
    _, base, _, _ = evaluate(direct, relays, (), mission)
    failures = []
    for disabled in selection:
        active = tuple(idx for idx in selection if idx != disabled)
        remaining, result, _, _ = evaluate(direct, relays, active, mission)
        failures.append(dict(id=options[disabled]["id"], metrics=result, meets=passes(result, mission),
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
                combined=online.tolist(), assisted=assisted.tolist(), metrics=values, meets=passes(values, mission))
    sun_metrics = metrics(solar, hours)
    details = site_details(grid, slope, x, y)
    result = dict(site=dict(name=name, lat=lat, lon=lon, elevation=ground, **details),
                  config=mission.model_dump(mode="json"), times=[stamp.isoformat() for stamp in times],
                  direct=base, solar=sun_metrics, network=network, worst=worst, redundant=redundant,
                  outages=intervals(direct, times, end, hours, sample["reason"]),
                  darkness=intervals(solar, times, end, hours),
                  recommended=[options[idx]["id"] for idx in selection], feasible=feasible, eligible=len(eligible),
                  darkness_meets=sun_metrics["longest"] <= mission.darkness_hours,
                  failures=failures, failure_states=failure_states, candidates=options,
                  horizon=dict(azimuth=horizon.azimuth_deg.tolist(), elevation=nullable(horizon.elevation_deg),
                               distance=nullable(horizon.distance_m)),
                  samples=dict(**sample, direct=direct.tolist(), solar=solar.tolist(), network=combined.tolist()),
                  provenance=PROVENANCE)
    if include_map:
        result["map"] = map_data(grid, slope, x, y)
    return result


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
