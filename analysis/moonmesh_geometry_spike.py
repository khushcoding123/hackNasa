#!/usr/bin/env python3
"""MOONMESH geometry gate: test terrain outages and simple mitigations on real lunar data.

This is deliberately a geometry-only upper bound. It uses NASA/JPL SPICE for Earth
positions and NASA GSFC LOLA terrain for horizon masks. It does not claim RF link
closure, power availability, deployability, or flight readiness.
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from scipy.ndimage import maximum_filter

from lunar_core.geometry import Body, SurfacePoint, horizontal_positions
from lunar_core.horizon import HorizonMask, horizon_mask
from lunar_core.kernels import load_kernels
from lunar_core.terrain import (
    MOON_RADIUS_M,
    TerrainGrid,
    polar_stereographic_south,
    polar_stereographic_south_inverse,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".moonmesh-data"
START = datetime(2026, 11, 1, tzinfo=UTC)
END = datetime(2026, 12, 1, tzinfo=UTC)
STEP = timedelta(minutes=30)
TIMES = [START + i * STEP for i in range(int((END - START) / STEP))]


@dataclass(frozen=True)
class Site:
    id: str
    name: str
    latitude_deg: float
    longitude_deg: float


SITES = (
    Site("malapert", "Malapert Massif", -85.964, 357.681),
    Site("mons-mouton", "Mons Mouton", -85.4, 31.7),
)


def longest_false_run(flags: np.ndarray) -> float:
    longest = current = 0
    for flag in flags:
        if flag:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest * STEP.total_seconds() / 3600.0


def summary(flags: np.ndarray) -> dict[str, float]:
    return {
        "availability_pct": round(float(flags.mean() * 100.0), 2),
        "longest_outage_h": round(longest_false_run(flags), 2),
        "total_outage_h": round(float((~flags).sum() * STEP.total_seconds() / 3600.0), 2),
    }


def body_arrays(lat: float, lon: float, height_m: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    positions = horizontal_positions(Body.EARTH, SurfacePoint(lat, lon, height_m), TIMES)
    az = np.array([p.azimuth_deg for p in positions], dtype=float)
    el = np.array([p.elevation_deg for p in positions], dtype=float)
    return az, el


def visibility(mask: HorizonMask, az: np.ndarray, el: np.ndarray, clearance_deg: float = 0.0) -> np.ndarray:
    return el > (mask.at(az) + clearance_deg)


def shifted_points(site: Site, radius_m: float = 200.0) -> list[tuple[str, float, float]]:
    x0, y0 = polar_stereographic_south(site.latitude_deg, site.longitude_deg)
    points: list[tuple[str, float, float]] = []
    for name, dx, dy in (
        ("north", 0.0, radius_m),
        ("northeast", radius_m / math.sqrt(2), radius_m / math.sqrt(2)),
        ("east", radius_m, 0.0),
        ("southeast", radius_m / math.sqrt(2), -radius_m / math.sqrt(2)),
        ("south", 0.0, -radius_m),
        ("southwest", -radius_m / math.sqrt(2), -radius_m / math.sqrt(2)),
        ("west", -radius_m, 0.0),
        ("northwest", -radius_m / math.sqrt(2), radius_m / math.sqrt(2)),
    ):
        lat, lon = polar_stereographic_south_inverse(float(x0) + dx, float(y0) + dy)
        points.append((name, float(lat), float(lon)))
    return points


def angular_distance_and_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians((lon2 - lon1 + 180.0) % 360.0 - 180.0)
    cos_c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dlon)
    c = math.acos(max(-1.0, min(1.0, cos_c)))
    y = math.sin(dlon) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)
    return c, math.atan2(y, x)


def destination(lat: float, lon: float, bearing: float, angle: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p = math.radians(lat)
    l = math.radians(lon)
    sin_lat = math.sin(p) * np.cos(angle) + math.cos(p) * np.sin(angle) * math.cos(bearing)
    out_lat = np.arcsin(np.clip(sin_lat, -1.0, 1.0))
    out_lon = l + np.arctan2(
        math.sin(bearing) * np.sin(angle) * math.cos(p),
        np.cos(angle) - math.sin(p) * sin_lat,
    )
    return np.degrees(out_lat), np.mod(np.degrees(out_lon), 360.0)


def xyz(lat: float, lon: float, radius: float) -> np.ndarray:
    p, l = math.radians(lat), math.radians(lon)
    return radius * np.array([math.cos(p) * math.cos(l), math.cos(p) * math.sin(l), math.sin(p)])


def local_line_of_sight(
    lat1: float,
    lon1: float,
    h1_ground: float,
    mast1: float,
    lat2: float,
    lon2: float,
    h2_ground: float,
    mast2: float,
    terrain: TerrainGrid,
) -> bool:
    angle, bearing = angular_distance_and_bearing(lat1, lon1, lat2, lon2)
    distance = angle * MOON_RADIUS_M
    if distance < 50.0:
        return True
    count = max(3, int(distance / terrain.pixel_size_m))
    fractions = np.linspace(0.0, 1.0, count + 1)[1:-1]
    p1 = xyz(lat1, lon1, MOON_RADIUS_M + h1_ground + mast1)
    p2 = xyz(lat2, lon2, MOON_RADIUS_M + h2_ground + mast2)
    chord = p1[None, :] * (1.0 - fractions[:, None]) + p2[None, :] * fractions[:, None]
    chord_radius = np.linalg.norm(chord, axis=1)
    lats, lons = destination(lat1, lon1, bearing, angle * fractions)
    xs, ys = polar_stereographic_south(lats, lons)
    ground = terrain.sample(xs, ys)
    clearance = chord_radius - (MOON_RADIUS_M + ground)
    return bool(np.all(clearance > 0.0))


def candidate_points(site: Site, terrain: TerrainGrid, limit: int = 15) -> list[dict[str, float]]:
    x0, y0 = polar_stereographic_south(site.latitude_deg, site.longitude_deg)
    heights = terrain.heights.astype(float)
    # A 420 m neighborhood suppresses multiple pixels from the same ridge top.
    size = max(3, int(round(420.0 / terrain.pixel_size_m)))
    maxima = heights == maximum_filter(heights, size=size, mode="nearest")
    gy, gx = np.gradient(heights, terrain.pixel_size_m, terrain.pixel_size_m)
    slope_deg = np.degrees(np.arctan(np.hypot(gx, gy)))
    rows, cols = np.nonzero(maxima & (slope_deg < 15.0))
    xs = terrain.x_left + (cols + 0.5) * terrain.pixel_size_m
    ys = terrain.y_top - (rows + 0.5) * terrain.pixel_size_m
    distances = np.hypot(xs - float(x0), ys - float(y0))
    keep = (distances >= 500.0) & (distances <= 8_000.0)
    rows, cols, xs, ys, distances = rows[keep], cols[keep], xs[keep], ys[keep], distances[keep]
    order = np.argsort(heights[rows, cols])[::-1]
    selected: list[dict[str, float]] = []
    for idx in order:
        x, y = float(xs[idx]), float(ys[idx])
        if any(math.hypot(x - c["x_m"], y - c["y_m"]) < 500.0 for c in selected):
            continue
        lat, lon = polar_stereographic_south_inverse(x, y)
        selected.append(
            {
                "x_m": x,
                "y_m": y,
                "latitude_deg": float(lat),
                "longitude_deg": float(lon),
                "ground_m": float(heights[rows[idx], cols[idx]]),
                "slope_deg": float(slope_deg[rows[idx], cols[idx]]),
                "distance_m": float(distances[idx]),
            }
        )
        if len(selected) >= limit:
            break
    return selected


def best_combinations(direct: np.ndarray, relays: list[np.ndarray], max_relays: int = 3) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for count in range(1, min(max_relays, len(relays)) + 1):
        best: tuple[tuple[int, ...], np.ndarray] | None = None
        for combo in itertools.combinations(range(len(relays)), count):
            flags = direct.copy()
            for idx in combo:
                flags |= relays[idx]
            if best is None:
                best = (combo, flags)
                continue
            candidate_key = (flags.mean(), -longest_false_run(flags))
            best_key = (best[1].mean(), -longest_false_run(best[1]))
            if candidate_key > best_key:
                best = (combo, flags)
        assert best is not None
        results.append({"relay_indices": list(best[0]), **summary(best[1])})
    return results


def analyze_site(site: Site) -> dict[str, object]:
    terrain_dir = DATA / "terrain"
    near = TerrainGrid.load(terrain_dir / f"{site.id}_near20km.npz")
    far = TerrainGrid.load(terrain_dir / f"{site.id}_far.npz")
    az2, el2 = body_arrays(site.latitude_deg, site.longitude_deg, 2.0)
    flat = el2 > 0.0

    height_results: dict[str, object] = {}
    direct2: np.ndarray | None = None
    for height in (2.0, 5.0, 10.0):
        mask = horizon_mask(site.latitude_deg, site.longitude_deg, near, far, observer_height_m=height)
        az, el = (az2, el2) if height == 2.0 else body_arrays(site.latitude_deg, site.longitude_deg, height)
        flags = visibility(mask, az, el)
        if height == 2.0:
            direct2 = flags
        height_results[f"{height:g}m"] = summary(flags)
    assert direct2 is not None

    nearby: list[dict[str, object]] = []
    for direction, lat, lon in shifted_points(site):
        mask = horizon_mask(lat, lon, near, far, observer_height_m=2.0)
        az, el = body_arrays(lat, lon, 2.0)
        flags = visibility(mask, az, el)
        nearby.append({"direction": direction, "distance_m": 200.0, **summary(flags)})
    nearby.sort(key=lambda row: (row["availability_pct"], -row["longest_outage_h"]), reverse=True)

    x0, y0 = polar_stereographic_south(site.latitude_deg, site.longitude_deg)
    site_ground = float(near.sample(np.atleast_1d(x0), np.atleast_1d(y0))[0])
    raw_candidates = candidate_points(site, near)
    usable_candidates: list[dict[str, object]] = []
    relay_flags: list[np.ndarray] = []
    for candidate in raw_candidates:
        los = local_line_of_sight(
            site.latitude_deg,
            site.longitude_deg,
            site_ground,
            2.0,
            float(candidate["latitude_deg"]),
            float(candidate["longitude_deg"]),
            float(candidate["ground_m"]),
            5.0,
            near,
        )
        if not los:
            continue
        lat, lon = float(candidate["latitude_deg"]), float(candidate["longitude_deg"])
        mask = horizon_mask(lat, lon, near, far, observer_height_m=5.0)
        az, el = body_arrays(lat, lon, 5.0)
        flags = visibility(mask, az, el)
        usable_candidates.append(
            {
                "latitude_deg": round(lat, 6),
                "longitude_deg": round(lon, 6),
                "distance_m": round(float(candidate["distance_m"]), 1),
                "slope_deg": round(float(candidate["slope_deg"]), 2),
                "ground_m": round(float(candidate["ground_m"]), 1),
                "earth_visibility_pct": round(float(flags.mean() * 100.0), 2),
            }
        )
        relay_flags.append(flags)

    terrain_only = flat & ~direct2
    orbital_or_flat_horizon = ~flat
    return {
        "site": asdict(site),
        "period": {"start": START.isoformat(), "end": END.isoformat(), "step_minutes": 30},
        "outage_decomposition_2m": {
            "flat_horizon_availability_pct": round(float(flat.mean() * 100.0), 2),
            "terrain_horizon_availability_pct": round(float(direct2.mean() * 100.0), 2),
            "terrain_blocked_while_above_flat_horizon_pct_of_time": round(float(terrain_only.mean() * 100.0), 2),
            "below_flat_horizon_pct_of_time": round(float(orbital_or_flat_horizon.mean() * 100.0), 2),
            "terrain_share_of_all_outage_pct": round(
                float(terrain_only.sum() / max(1, (~direct2).sum()) * 100.0), 2
            ),
        },
        "antenna_height": height_results,
        "best_200m_shift": nearby[0],
        "all_200m_shifts": nearby,
        "relay_test_scope": {
            "candidate_rule": "local maxima, slope <15 deg, 0.5-8 km, >=500 m separation",
            "site_mast_m": 2.0,
            "relay_mast_m": 5.0,
            "raw_candidate_count": len(raw_candidates),
            "surface_los_candidate_count": len(usable_candidates),
            "limitations": "geometry-only upper bound; no RF budget, power, access, thermal, or ground-station scheduling",
        },
        "relay_candidates": usable_candidates,
        "best_relay_combinations": best_combinations(direct2, relay_flags),
    }


def main() -> None:
    load_kernels(DATA / "kernels")
    results = {
        "method": "NASA/JPL SPICE DE440 + NASA GSFC LOLA 20 m near field / 240 m far field",
        "sites": [analyze_site(site) for site in SITES],
    }
    out = ROOT / "analysis" / "moonmesh_geometry_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
