"""Benchmark the simulated Haworth mission against an independently calculated reference."""
import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess

import numpy as np
from scipy.integrate import solve_ivp
from scipy.interpolate import RegularGridInterpolator
import scipy
import spiceypy as spice
import yaml


ROOT = Path(__file__).resolve().parents[2]
HOPS = ("Lander → Relay", "Relay → Satellite", "Satellite → Earth")
KERNELS = ("naif0012.tls", "moon_de440_250416.tf", "moon_pa_de440_200625.bpc", "de440s.bsp")


def frame(lat, lon):
    p, l = np.deg2rad([lat, lon])
    return np.array([[np.cos(p)*np.cos(l), np.cos(p)*np.sin(l), np.sin(p)],
                     [-np.sin(l), np.cos(l), 0],
                     [-np.sin(p)*np.cos(l), -np.sin(p)*np.sin(l), np.cos(p)]])


def interpolate_raster(heights, left, top, pixel, radius):
    y = top - pixel*(np.arange(heights.shape[0])+0.5)
    x = left + pixel*(np.arange(heights.shape[1])+0.5)
    grid = RegularGridInterpolator((y, x), heights, bounds_error=False)

    def sample(direction):
        # South polar stereographic directly from a unit vector.
        # The horizontal Cartesian components project to northing, then easting.
        northing_easting = 2000 * radius * direction[..., :2] / (1 - direction[..., 2:3])
        return grid(northing_easting) / 1000

    return sample


def local_dem(path, radius):
    with np.load(path) as dem:
        return interpolate_raster(dem["heights"], float(dem["x_left"]), float(dem["y_top"]),
                                  float(dem["pixel_size_m"]), radius)


def surface_clearance(first, second, terrain, radius, spacing_km):
    length = np.linalg.norm(second - first)
    divisions = max(2, int(np.ceil(length / spacing_km)))
    point = first + (second-first) * (np.arange(1, divisions) / divisions)[:, None]
    distance = np.linalg.norm(point, axis=1)
    clearance = (distance - radius - terrain(point / distance[:, None])) * 1000
    if not np.isfinite(clearance).all():
        return None
    index = int(clearance.argmin())
    return {"clearance_m": float(clearance[index]), "from_lander_m": float(length*1000*(index+1)/divisions)}


def horizon(azimuth, axes, observer, near, far, radius):
    # Direct Cartesian ray in the actual azimuth, without 2° interpolation.
    near_dist = np.arange(0.010, 10.001, 0.010)
    far_dist = np.arange(10.120, 150.001, 0.120)
    max_angles, obstruction_distances = [], []
    for distances, grid in ((near_dist, near), (far_dist, far)):
        tangent = np.sin(azimuth)[:, None]*axes[1] + np.cos(azimuth)[:, None]*axes[2]
        angle = distances / radius
        direction = np.cos(angle)[None, :, None]*axes[0] + np.sin(angle)[None, :, None]*tangent[:, None, :]
        ground = radius + grid(direction)
        heights = np.rad2deg(np.arctan2(ground*np.cos(angle)-observer, ground*np.sin(angle)))
        index = np.argmax(np.where(np.isfinite(heights), heights, -np.inf), axis=1)
        max_angles.append(np.max(heights, axis=1))
        obstruction_distances.append(distances[index])
    use_near = max_angles[0] >= max_angles[1]
    return np.maximum(*max_angles), np.where(use_near, *obstruction_distances)


def margins(radio, distance_km):
    frequency, watts, tx, rx, sensitivity, losses, _ = radio
    # ITU-R P.525 basic free-space transmission loss using SI and exact c.
    loss = 20 * np.log10(4*np.pi * (distance_km*1000) * (frequency*1e9) / 299792458)
    return 10*np.log10(watts/0.001) + tx + rx - loss - losses - sensitivity


def ephemerides(config, times, radius, relay_axes):
    epochs = np.array([spice.str2et(stamp.isoformat()[:19]) for stamp in times])
    rotations = np.array([spice.pxform("J2000", "MOON_ME", epoch) for epoch in epochs])
    mu = config["lunar_gm_km3_s2"]
    orbit_radius = radius + config["altitude_km"]
    initial = np.r_[rotations[0].T @ relay_axes[0] * orbit_radius,
                    rotations[0].T @ relay_axes[2] * np.sqrt(mu/orbit_radius)]

    def gravity(_time, state):
        return np.r_[state[3:], -mu*state[:3]/np.linalg.norm(state[:3])**3]

    elapsed = epochs - epochs[0]
    orbit = solve_ivp(gravity, (0, elapsed[-1]), initial, t_eval=elapsed,
                      method="DOP853", atol=1e-11, rtol=1e-11)
    if not orbit.success:
        raise RuntimeError(orbit.message)
    orbiter = (rotations @ orbit.y[:3].T[:, :, None])[:, :, 0]

    # USNO approximate sidereal time, UTC used for UT1. Same spherical receiver
    # and mean-of-date/J2000 approximation as the scenario, evaluated independently.
    jd_midnight = np.array([t.replace(hour=0, minute=0, second=0).timestamp()/86400 + 2440587.5 for t in times])
    utc_hours = np.array([t.hour + t.minute/60 + t.second/3600 for t in times])
    days = jd_midnight - 2451545
    centuries = (days + utc_hours/24) / 36525
    sidereal_deg = 15*(6.697375 + 0.065709824279*days + 1.0027379*utc_hours + 0.0000258*centuries**2)
    lat, lon, earth_radius = config["earth"]
    zenith = np.array([frame(lat, lon+angle)[0] for angle in sidereal_deg])
    zenith = (rotations @ zenith[:, :, None])[:, :, 0]
    earth_center, _ = spice.spkpos("EARTH", epochs, "MOON_ME", "NONE", "MOON")
    return orbiter, earth_center + earth_radius*zenith, zenith


def measurements(config, inputs, pgda_path):
    radius = config["moon_radius_km"]
    start = datetime.fromisoformat(config["epoch"])
    count = config["hours"]*3600 // config["reference_seconds"]
    times = [start + timedelta(seconds=i*config["reference_seconds"]) for i in range(count)]
    near, far = (local_dem(path, radius) for path in inputs[-2:])
    lander, relay = config["lander"], config["relay"]
    lander_up = frame(*lander[:2])[0]
    relay_axes = frame(*relay[:2])
    relay_up = relay_axes[0]
    lander_ground, relay_ground = float(near(lander_up)[0]), float(near(relay_up)[0])
    first = lander_up * (radius + lander_ground + lander[2]/1000)
    second = relay_up * (radius + relay_ground + relay[2]/1000)
    surface = surface_clearance(first, second, near, radius, 0.005)
    if surface is None:
        raise ValueError("The frozen surface baseline lies outside the required DEM.")
    surface_range = radius*np.arctan2(np.linalg.norm(np.cross(lander_up, relay_up)), lander_up @ relay_up)

    for path in inputs[:-2]:
        spice.furnsh(str(path))
    orbiter, receiver, zenith = ephemerides(config, times, radius, relay_axes)
    delta = orbiter - second
    east, north = delta @ relay_axes[1], delta @ relay_axes[2]
    access_elev = np.rad2deg(np.arctan2(delta @ relay_up, np.hypot(east, north)))
    az = np.arctan2(east, north)
    limits, obstacles = [], []
    for block in np.array_split(az, max(1, np.ceil(len(az)/96).astype(int))):
        angles, distances = horizon(block, relay_axes, np.linalg.norm(second), near, far, radius)
        limits.extend(angles)
        obstacles.extend(distances)
    clearance = access_elev - limits - config["terrain_buffer_deg"]
    ray = receiver - orbiter
    earth_range = np.linalg.norm(ray, axis=1)
    radial = -np.sum(ray*zenith, axis=1)
    earth_elev = np.rad2deg(np.arctan2(radial, np.linalg.norm(np.cross(ray, zenith), axis=1)))
    # Solve the segment/sphere intersection quadratic using entry/exit parameters.
    direction = ray / earth_range[:, None]
    b = np.sum(orbiter*direction, axis=1)
    discriminant = b*b - (np.sum(orbiter**2, axis=1) - radius**2)
    root = np.sqrt(np.maximum(0, discriminant))
    occulted = (discriminant >= 0) & (-b+root >= 0) & (-b-root <= earth_range)
    ranges = np.column_stack((np.full(count, surface_range), np.linalg.norm(delta, axis=1), earth_range))
    radio = [config["radios"][key] for key in ("surface", "access", "backhaul")]
    rf = np.column_stack([margins(profile, ranges[:, i]) for i, profile in enumerate(radio)])
    reasons = []
    for i in range(count):
        blockers = [[], [], []]
        if surface["clearance_m"] <= 0:
            blockers[0].append("Terrain obstruction")
        if access_elev[i] < config["access_mask_deg"]:
            blockers[1].append("Below relay elevation mask")
        if not np.isfinite(clearance[i]):
            blockers[1].append("Unknown relay horizon terrain")
        elif clearance[i] <= 0:
            blockers[1].append("Terrain obstruction or clearance")
        if occulted[i]:
            blockers[2].append("Moon blocks Earth receiver")
        if earth_elev[i] < config["earth_mask_deg"]:
            blockers[2].append("Below Earth receiver elevation mask")
        for j in range(3):
            if rf[i, j] < radio[j][-1]:
                blockers[j].append("Insufficient RF margin")
        reasons.append(blockers)
    alt = cross_check(pgda_path, radius, lander, relay, lander_up, relay_up)
    return dict(times=times, reasons=reasons, ranges=ranges, margins=rf, clearance=clearance,
                elevations=np.column_stack((access_elev, earth_elev)), obstacles=np.asarray(obstacles),
                orbiter=orbiter, receiver=receiver, surface=surface, cross_source=alt)


def cross_check(path, radius, lander, relay, lander_up, relay_up):
    if not path.is_file():
        return {"status": "unverified", "detail": "PGDA 78 raster unavailable"}
    import rasterio
    with rasterio.open(path) as dataset:
        crs = dataset.crs.to_dict()
        transform = dataset.transform
        if (crs.get("proj"), crs.get("lat_0"), crs.get("lon_0"), crs.get("R")) != ("stere", -90, 0, radius*1000):
            raise ValueError(f"Unexpected PGDA projection: {dataset.crs}")
        if transform.a != -transform.e or transform.b or transform.d:
            raise ValueError("Unexpected PGDA pixel geometry.")
        terrain = interpolate_raster(dataset.read(1, masked=True).astype(float).filled(np.nan),
                                     transform.c, transform.f, transform.a, radius)
    heights = [float(terrain(up)[0]) for up in (lander_up, relay_up)]
    if not np.isfinite(heights).all():
        return {"status": "unverified", "detail": "PGDA 78 does not cover both fixed antennas"}
    points = [up*(radius+height+site[2]/1000) for up, height, site in
              zip((lander_up, relay_up), heights, (lander, relay), strict=True)]
    result = surface_clearance(*points, terrain, radius, 0.0025)
    if result is None:
        return {"status": "unverified", "detail": "PGDA 78 has missing pixels along the chord"}
    return {"status": "compared", **result, "ground_elevation_m": [height*1000 for height in heights]}


def windows(flags, seconds):
    edges = np.flatnonzero(np.diff(np.r_[False, flags, False])).reshape(-1, 2)
    return (edges * seconds).tolist()


def validate(app, reference, config):
    step = config["app_minutes"] * 60
    fine = config["reference_seconds"]
    indices = np.arange(0, len(reference["times"]), step//fine)
    actual = app["samples"]
    if [row["time"] for row in actual] != [reference["times"][i].isoformat() for i in indices]:
        raise ValueError("Application and benchmark time grids differ.")
    hop_rows = [[sample["hops"][name] for name in HOPS] for sample in actual]
    observed = np.asarray([sample["orbiter_km"] for sample in actual])
    receiver = np.asarray([sample["receiver_km"] for sample in actual])
    nominal = app["states"]["nominal"]
    expected_fine = np.array([not any(row) for row in reference["reasons"]])
    actual_online = np.asarray(nominal["online"])
    contacts = windows(expected_fine, fine)
    actual_contacts = windows(actual_online, step)
    contact_error = float(np.abs(np.array(contacts)-actual_contacts).max()) if len(contacts) == len(actual_contacts) else None
    longest = max((end-start for start, end in windows(~expected_fine, fine)), default=0)
    actual_longest = nominal["metrics"]["longest"] * 3600
    errors = dict(
        orbiter_position_km=float(np.linalg.norm(observed-reference["orbiter"][indices], axis=1).max()),
        earth_receiver_position_km=float(np.linalg.norm(receiver-reference["receiver"][indices], axis=1).max()),
        range_km=float(np.max(np.abs(np.array([[h["range_km"] for h in row] for row in hop_rows])-reference["ranges"][indices]))),
        elevation_deg=float(np.max(np.abs(np.array([[h["elevation_deg"] for h in row[1:]] for row in hop_rows])-reference["elevations"][indices]))),
        margin_db=float(np.max(np.abs(np.array([[h["margin_db"] for h in row] for row in hop_rows])-reference["margins"][indices]))),
        event_seconds=contact_error, availability_pp=float(abs(expected_fine.mean()-actual_online.mean())*100),
        longest_blackout_seconds=abs(longest-actual_longest),
    )
    checks = {key: {"error": errors[key], "limit": limit, "pass": errors[key] is not None and errors[key] <= limit}
              for key, limit in config["limits"].items()}
    reason_differences = [actual[j]["time"] for j, i in enumerate(indices)
                          if [h["reasons"] for h in hop_rows[j]] != reference["reasons"][i]]
    status_mismatches = int(np.count_nonzero(expected_fine[indices] != actual_online))
    failure_checks = {node: not any(app["states"][node]["online"]) and
                      all(any(r == f"{node.capitalize()}: disabled" for r in row)
                          for row in app["states"][node]["reasons"]) for node in ("relay", "satellite")}
    terrain = reference["cross_source"]
    cross_check_pass = terrain["status"] == "compared" and (
        (terrain["clearance_m"] > 0) == (reference["surface"]["clearance_m"] > 0))
    return dict(checks=checks, expected_contacts=contacts, actual_contacts=actual_contacts,
                expected_availability_pct=float(expected_fine.mean()*100),
                actual_availability_pct=nominal["metrics"]["availability"],
                expected_longest_blackout_seconds=longest, actual_longest_blackout_seconds=actual_longest,
                status_mismatches=status_mismatches, blocker_disagreements=reason_differences,
                surface=reference["surface"], cross_source=terrain, cross_check_pass=cross_check_pass,
                failure_checks=failure_checks,
                result="pass" if all(c["pass"] for c in checks.values()) and not status_mismatches
                and not reason_differences and all(failure_checks.values()) and cross_check_pass else "incomplete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT/"training/configs/haworth_mission_validation.yaml")
    parser.add_argument("--data-root", type=Path, default=Path(os.environ.get("MOONMESH_DATA_ROOT", ROOT/"data")))
    parser.add_argument("--output", type=Path, help="Write the JSON comparison and adjacent sampled reference CSV")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    pgda = args.data_root/config["pgda_file"]
    cache = ROOT/".moonmesh-data"
    inputs = [cache/"kernels"/name for name in KERNELS]
    inputs += [cache/"terrain"/f"haworth_{suffix}.npz" for suffix in ("near20km", "far")]
    reference = measurements(config, inputs, pgda)
    # Production imports occur only after the reference is independently calculated.
    from moonmesh.mission_sim import reference_mission

    report = validate(reference_mission(), reference, config)
    files = inputs + ([pgda] if pgda.is_file() else [])
    code = [Path(__file__), ROOT/"moonmesh/mission_sim.py", ROOT/"moonmesh/science.py",
            ROOT/"moonmesh/environment.py", ROOT/"moonmesh/network.py", ROOT/"lunar_core/horizon.py"]
    report.update(evidence="simulated benchmark", sources=config["sources"],
                  versions={"scipy": scipy.__version__, "spice": spice.tkvrsn("TOOLKIT")},
                  config_sha256=hashlib.sha256(args.config.read_bytes()).hexdigest(),
                  code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  source_sha256={p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in code},
                  input_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    output = args.output or args.data_root/"reports/haworth_mission_validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    columns = np.column_stack((reference["orbiter"], reference["receiver"], reference["ranges"],
                               reference["elevations"], reference["margins"], reference["clearance"],
                               reference["obstacles"]))
    csv = output.with_suffix(".csv")
    with csv.open("w", encoding="utf-8") as stream:
        stream.write("timestamp_utc,orbiter_x_km,orbiter_y_km,orbiter_z_km,receiver_x_km,receiver_y_km,"
                     "receiver_z_km,surface_range_km,access_range_km,backhaul_range_km,access_el_deg,"
                     "receiver_el_deg,surface_margin_db,access_margin_db,backhaul_margin_db,"
                     "terrain_clearance_deg,obstruction_distance_km,path_available\n")
        for stamp, row, reasons in zip(reference["times"], columns, reference["reasons"], strict=True):
            stream.write(stamp.isoformat()+","+",".join(f"{v:.9f}" for v in row)+f",{int(not any(reasons))}\n")
    print(f"{report['result']} · {output} · {csv}")
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
