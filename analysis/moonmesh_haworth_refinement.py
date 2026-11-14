#!/usr/bin/env python3
"""Refine the Haworth relay gate with 10-minute samples, margins, and simple power cases."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import numpy as np

from lunar_core.geometry import Body, SurfacePoint, horizontal_positions
from lunar_core.horizon import horizon_mask
from lunar_core.kernels import load_kernels
from lunar_core.terrain import TerrainGrid
from lunar_core.visibility import apparent_semidiameter_deg, SUN_RADIUS_KM

from moonmesh_geometry_spike import DATA, ROOT


START = datetime(2026, 11, 1, tzinfo=UTC)
END = datetime(2026, 12, 1, tzinfo=UTC)
STEP = timedelta(minutes=10)
TIMES = [START + i * STEP for i in range(int((END - START) / STEP))]

SITE = (-86.8, 337.2, 2.0)
RELAY = (-86.766101, 339.488393, 5.0)


def positions(body: Body, point: tuple[float, float, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = horizontal_positions(body, SurfacePoint(*point), TIMES)
    return (
        np.array([r.azimuth_deg for r in rows]),
        np.array([r.elevation_deg for r in rows]),
        np.array([r.distance_km for r in rows]),
    )


def longest_false(flags: np.ndarray) -> float:
    best = current = 0
    for flag in flags:
        current = 0 if flag else current + 1
        best = max(best, current)
    return best * STEP.total_seconds() / 3600.0


def metrics(flags: np.ndarray) -> dict[str, float]:
    return {
        "availability_pct": round(float(flags.mean() * 100), 2),
        "longest_outage_h": round(longest_false(flags), 2),
        "total_outage_h": round(float((~flags).sum()) * STEP.total_seconds() / 3600.0, 2),
    }


def battery_power(sun: np.ndarray, endurance_h: float) -> np.ndarray:
    if endurance_h <= 0:
        return sun.copy()
    capacity_steps = int(round(endurance_h * 3600 / STEP.total_seconds()))
    powered = np.zeros_like(sun)
    remaining = 0
    for i, lit in enumerate(sun):
        if lit:
            remaining = capacity_steps
            powered[i] = True
        elif remaining > 0:
            powered[i] = True
            remaining -= 1
    return powered


def main() -> None:
    load_kernels(DATA / "kernels")
    near = TerrainGrid.load(DATA / "terrain" / "haworth_near20km.npz")
    far = TerrainGrid.load(DATA / "terrain" / "haworth_far.npz")
    site_mask = horizon_mask(SITE[0], SITE[1], near, far, observer_height_m=SITE[2])
    relay_mask = horizon_mask(RELAY[0], RELAY[1], near, far, observer_height_m=RELAY[2])

    saz, sel, _ = positions(Body.EARTH, SITE)
    raz, rel, _ = positions(Body.EARTH, RELAY)
    sun_az, sun_el, sun_dist = positions(Body.SUN, RELAY)
    semidiameter = np.array(
        [apparent_semidiameter_deg(SUN_RADIUS_KM, distance) for distance in sun_dist]
    )
    relay_sun = sun_el + semidiameter > relay_mask.at(sun_az)

    margins: dict[str, object] = {}
    for margin in (0.0, 1.0, 2.0):
        direct = sel > site_mask.at(saz) + margin
        relay_earth = rel > relay_mask.at(raz) + margin
        upper = direct | relay_earth
        power_cases: dict[str, object] = {"unconstrained_upper_bound": metrics(upper)}
        for battery_h in (0.0, 24.0, 72.0, 168.0):
            powered = battery_power(relay_sun, battery_h)
            power_cases[f"solar_plus_{battery_h:g}h_storage"] = metrics(
                direct | (relay_earth & powered)
            )
        margins[f"{margin:g}deg"] = {
            "direct": metrics(direct),
            "relay_earth": metrics(relay_earth),
            "combined": power_cases,
        }

    result = {
        "period": {"start": START.isoformat(), "end": END.isoformat(), "step_minutes": 10},
        "site": {"latitude_deg": SITE[0], "longitude_deg": SITE[1], "mast_m": SITE[2]},
        "relay": {"latitude_deg": RELAY[0], "longitude_deg": RELAY[1], "mast_m": RELAY[2]},
        "relay_solar": {
            "sunlit_pct": round(float(relay_sun.mean() * 100), 2),
            "longest_darkness_h": round(longest_false(relay_sun), 2),
        },
        "clearance_margin_cases": margins,
        "limitations": [
            "Surface site-to-relay LOS was checked in the prior gate.",
            "No RF link budget, antenna pattern, deployment route, thermal model, or Earth station schedule.",
            "Battery model is duration-only and does not model energy generation or load.",
        ],
    }
    out = ROOT / "analysis" / "moonmesh_haworth_refinement.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
