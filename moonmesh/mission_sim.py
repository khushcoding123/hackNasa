"""Fixed Haworth reference run for simultaneous three-hop service."""
from datetime import UTC, date, datetime, timedelta

import numpy as np
import spiceypy as spice

from lunar_core.geometry import FRAME, Body, body_vectors
from lunar_core.kernels import SPICE_LOCK, load_kernels
from lunar_core.terrain import MOON_RADIUS_M, polar_stereographic_south

from .data import DATA, SITES, activate
from .models import Mission, Radio
from .network import link_margin, metrics
from .environment import node_states
from .science import Observer, intervals, surface_path, terrain, visibility

ALTITUDE_KM = 500
MOON_RADIUS_KM = MOON_RADIUS_M / 1000
LUNAR_GM = 4902.800118
EARTH_RADIUS_KM = 6378.137
EARTH_STATION = (35.4, -116.9)
EARTH_MASK_DEG = 10
ORBITER_MASK_DEG = 10
RELAY_SITE = (-86.76463526002901, 339.4856393404219)
ACCESS_RADIO = Radio(ghz=2.4, watts=10, tx_gain=17, rx_gain=17, sensitivity=-115, loss=3, margin=3)
BACKHAUL_RADIO = Radio(ghz=8.4, watts=20, tx_gain=30, rx_gain=68, sensitivity=-115, loss=3, margin=3)


def local_axes(lat, lon):
    lat, lon = np.radians([lat, lon])
    up = np.array([np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon), np.sin(lat)])
    east = np.array([-np.sin(lon), np.cos(lon), 0])
    return up, east, np.cross(up, east)


def observer_position(point):
    return local_axes(point.lat, point.lon)[0] * (MOON_RADIUS_M + point.ground + point.mast) / 1000


def trajectory(times, overhead):
    """Return orbiter and receiver positions in MOON_ME, with the receiver zenith."""
    with SPICE_LOCK:
        epochs = np.array([spice.str2et(t.isoformat().replace("+00:00", "")) for t in times])
        rotations = np.array([spice.pxform("J2000", FRAME, epoch) for epoch in epochs])
    up, _, north = local_axes(*overhead)
    radius = MOON_RADIUS_KM + ALTITUDE_KM
    phase = (epochs - epochs[0]) * np.sqrt(LUNAR_GM / radius**3)
    inertial = radius * (np.cos(phase)[:, None]*up + np.sin(phase)[:, None]*north) @ rotations[0]
    orbiter = np.einsum("nij,nj->ni", rotations, inertial)

    # Linear approximate GMST, treating UTC as UT1 and its equator as J2000.
    j2000 = datetime(2000, 1, 1, 12, tzinfo=UTC)
    days = np.array([(t - j2000).total_seconds() / 86400 for t in times])
    sidereal = (280.46061837 + 360.98564736629 * days + EARTH_STATION[1]) % 360
    lat, lon = np.radians(EARTH_STATION[0]), np.radians(sidereal)
    zenith_inertial = np.column_stack((np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon),
                                      np.full(len(times), np.sin(lat))))
    zenith = np.einsum("nij,nj->ni", rotations, zenith_inertial)
    receiver = body_vectors(Body.EARTH, times) + EARTH_RADIUS_KM * zenith
    return orbiter, receiver, zenith


def elevation_and_range(delta, up):
    distance = np.linalg.norm(delta, axis=1)
    cosine = np.sum(delta * up, axis=1) / distance
    return np.degrees(np.arcsin(np.clip(cosine, -1, 1))), distance


def moon_occults(orbiter, receiver):
    ray = receiver - orbiter
    fraction = -np.sum(orbiter * ray, axis=1) / np.sum(ray**2, axis=1)
    closest = orbiter + np.clip(fraction, 0, 1)[:, None] * ray
    return np.linalg.norm(closest, axis=1) <= MOON_RADIUS_KM


def hop(distance, radio, checks, **geometry):
    margin = link_margin(radio, distance)
    failures = [reason for failed, reason in checks if failed]
    if not np.isfinite(margin):
        failures.append("Unknown RF margin")
    elif margin < radio.margin:
        failures.append("Insufficient RF margin")
    status = "unknown" if any(reason.startswith("Unknown") for reason in failures) else (
        "unavailable" if failures else "available")
    return dict(status=status, reasons=failures, range_km=float(distance),
                margin_db=float(margin) if np.isfinite(margin) else None, **geometry)


def service_state(samples, times, end, hours, disabled=None):
    reasons = []
    for sample in samples:
        blockers = [f"{name}: {reason}" for name, link in sample["hops"].items() for reason in link["reasons"]]
        blockers.extend(f"{node}: {reason}" for node, reason in sample["node_failures"].items())
        if disabled:
            blockers.append(f"{disabled}: disabled")
        reasons.append(blockers)
    online = np.array([not blockers for blockers in reasons], dtype=bool)
    outages = intervals(online, times, end, hours)
    for outage in outages:
        outage["causes"] = sorted({cause for row in reasons[outage["first"]:outage["last"]] for cause in row})
    return dict(online=online.tolist(), metrics=metrics(online, hours), reasons=reasons, outages=outages)


def reference_mission():
    mission = Mission(start=date(2026, 11, 1), end=date(2026, 11, 2), minutes=5, initial_charge_pct=100)
    start = datetime.combine(mission.start, datetime.min.time(), UTC)
    end = datetime.combine(mission.end, datetime.min.time(), UTC)
    step = timedelta(minutes=mission.minutes)
    hours = step.total_seconds() / 3600
    times = tuple(start + i*step for i in range((end - start) // step))

    activate(mission.site)
    load_kernels(DATA / "kernels")
    grid = terrain(mission.site, "near20km")
    _, lat, lon = SITES[mission.site]
    lander = Observer(lat, lon, float(grid.sample(*polar_stereographic_south(lat, lon))[0]), mission.observer_m)
    relay = Observer(*RELAY_SITE, float(grid.sample(*polar_stereographic_south(*RELAY_SITE))[0]), mission.mast_m)
    if not np.isfinite([lander.ground, relay.ground]).all():
        raise ValueError("Reference lander or relay terrain is missing.")

    los, distance = surface_path(grid, lander, relay)
    surface = hop(distance, mission.surface_radio,
                  [(los is None, "Unknown surface terrain"), (los is False, "Terrain obstruction")])
    direct, lander_sun, _, lander_data = visibility(mission.site, lander, times, mission)
    relay_dte, relay_sun, horizon, relay_data = visibility(mission.site, relay, times, mission)
    lander_state = node_states(mission, lander_sun, lander_data["solar_known"], lander=True)["cold_dust"]
    relay_state = node_states(mission, relay_sun, relay_data["solar_known"], distance)["cold_dust"]
    lander_ok = lander_state["available"] & lander_data["solar_known"]
    relay_ok = relay_state["available"] & relay_data["solar_known"]

    orbiter, receiver, zenith = trajectory(times, RELAY_SITE)
    delta = orbiter - observer_position(relay)
    up, east, north = local_axes(relay.lat, relay.lon)
    azimuth = np.degrees(np.arctan2(delta @ east, delta @ north)) % 360
    elevation, access_range = elevation_and_range(delta, up)
    terrain_clearance = elevation - horizon.at(azimuth) - mission.clearance
    receiver_elev, earth_range = elevation_and_range(orbiter - receiver, zenith)
    occulted = moon_occults(orbiter, receiver)
    samples = []
    for i, stamp in enumerate(times):
        access = hop(access_range[i], ACCESS_RADIO, [
            (elevation[i] < ORBITER_MASK_DEG, "Below relay elevation mask"),
            (not np.isfinite(terrain_clearance[i]), "Unknown relay horizon terrain"),
            (terrain_clearance[i] <= 0, "Terrain obstruction or clearance"),
        ], elevation_deg=float(elevation[i]), azimuth_deg=float(azimuth[i]),
            clearance_deg=float(terrain_clearance[i]) if np.isfinite(terrain_clearance[i]) else None)
        backhaul = hop(earth_range[i], BACKHAUL_RADIO, [
            (occulted[i], "Moon blocks Earth receiver"),
            (receiver_elev[i] < EARTH_MASK_DEG, "Below Earth receiver elevation mask"),
        ], elevation_deg=float(receiver_elev[i]))
        node_failures = {}
        for node, available, data, state in (
            ("Lander", lander_ok, lander_data, lander_state), ("Relay", relay_ok, relay_data, relay_state)
        ):
            if not available[i]:
                node_failures[node] = state["reason"][i] if data["solar_known"][i] else "Unknown illumination"
        samples.append(dict(time=stamp.isoformat(), orbiter_km=orbiter[i].tolist(), receiver_km=receiver[i].tolist(),
                            hops={"Lander → Relay": surface, "Relay → Satellite": access, "Satellite → Earth": backhaul},
                            node_failures=node_failures))

    direct &= lander_ok
    via_relay = relay_dte & relay_ok & lander_ok & (surface["status"] == "available")
    baselines = {name: dict(online=flags.tolist(), metrics=metrics(flags, hours))
                 for name, flags in (("Direct Earth", direct), ("Surface relay → Earth", via_relay))}
    return dict(
        site="Haworth", start=start.isoformat(), end=end.isoformat(), minutes=mission.minutes,
        lander_km=observer_position(lander).tolist(), relay_km=observer_position(relay).tolist(),
        samples=samples, baselines=baselines,
        states={key: service_state(samples, times, end, hours, disabled) for key, disabled in (
            ("nominal", None), ("relay", "Relay"), ("satellite", "Satellite"))},
        assumptions=dict(
            frame=f"Moon-centered {FRAME}; Cartesian positions in km",
            orbit=dict(model="Hypothetical two-body circular orbit, northbound above the fixed relay at epoch",
                       altitude_km=ALTITUDE_KM, inclination_deg=90, epoch_utc=start.isoformat(),
                       overhead_lat_lon=RELAY_SITE, gm_km3_s2=LUNAR_GM, source="https://ssd.jpl.nasa.gov/astro_par.html"),
            receiver=dict(model="Hypothetical fixed receiver; spherical Earth, approximate sidereal rotation",
                          lat_lon=EARTH_STATION, radius_km=EARTH_RADIUS_KM, mask_deg=EARTH_MASK_DEG,
                          rotation_reference="https://aa.usno.navy.mil/faq/GAST"),
            inputs=mission.model_dump(mode="json"),
            rf=dict(access=ACCESS_RADIO.model_dump(), backhaul=BACKHAUL_RADIO.model_dump(),
                    relay_elevation_mask_deg=ORBITER_MASK_DEG,
                    model="Free-space budgets; ideal tracking, simultaneous single-stream forwarding at 128 kbps"),
            power="Cold + dust surface model; both surface batteries start full. Satellite assumed powered and operational throughout the run; Earth receiver has external power.",
            terrain="Existing LOLA near/far horizon model and sampled surface chord LOS.",
            ephemerides="DE440 Earth and Sun; instantaneous geometric positions. No light-time, orbit perturbations, or topographic limb model.",
            baselines="Direct and surface-relay baselines use the existing Earth-center geometry and RF model, with the same sample times and surface node states.",
            sampling="UTC interval starts; outages and transitions are estimated at the 5-minute cadence.",
        ),
    )
