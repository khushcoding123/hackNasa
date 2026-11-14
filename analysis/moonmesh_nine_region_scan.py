#!/usr/bin/env python3
"""Scan all nine approximate Artemis candidate-region centers for the MOONMESH gate."""

from __future__ import annotations

import json

import numpy as np

from lunar_core.horizon import horizon_mask
from lunar_core.kernels import load_kernels
from lunar_core.terrain import TerrainGrid

from moonmesh_geometry_spike import (
    DATA,
    ROOT,
    Site,
    body_arrays,
    shifted_points,
    summary,
    visibility,
)


SITES = (
    Site("cabeus-b", "Peak near Cabeus B", -83.6, 291.3),
    Site("haworth", "Haworth", -86.8, 337.2),
    Site("malapert", "Malapert Massif", -85.964, 357.681),
    Site("mouton-plateau", "Mons Mouton Plateau", -84.3, 30.6),
    Site("mons-mouton", "Mons Mouton", -85.4, 31.7),
    Site("nobile-rim-1", "Nobile Rim 1", -85.4, 37.3),
    Site("nobile-rim-2", "Nobile Rim 2", -84.0, 58.8),
    Site("de-gerlache-rim-2", "de Gerlache Rim 2", -88.2, 295.4),
    Site("slater-plain", "Slater Plain", -87.2, 125.5),
)


def scan(site: Site) -> dict[str, object]:
    near = TerrainGrid.load(DATA / "terrain" / f"{site.id}_near.npz")
    far = TerrainGrid.load(DATA / "terrain" / f"{site.id}_far.npz")
    az2, el2 = body_arrays(site.latitude_deg, site.longitude_deg, 2.0)
    flat = el2 > 0.0
    masks = {
        h: horizon_mask(site.latitude_deg, site.longitude_deg, near, far, observer_height_m=h)
        for h in (2.0, 5.0, 10.0)
    }
    terrain2 = visibility(masks[2.0], az2, el2)
    heights: dict[str, object] = {"2m": summary(terrain2)}
    for height in (5.0, 10.0):
        az, el = body_arrays(site.latitude_deg, site.longitude_deg, height)
        heights[f"{height:g}m"] = summary(visibility(masks[height], az, el))

    nearby: list[dict[str, object]] = []
    for direction, lat, lon in shifted_points(site):
        mask = horizon_mask(lat, lon, near, far, observer_height_m=2.0)
        az, el = body_arrays(lat, lon, 2.0)
        nearby.append({"direction": direction, **summary(visibility(mask, az, el))})
    nearby.sort(key=lambda x: (x["availability_pct"], -x["longest_outage_h"]), reverse=True)

    terrain_blocks = flat & ~terrain2
    terrain_opens = ~flat & terrain2
    return {
        "id": site.id,
        "name": site.name,
        "latitude_deg": site.latitude_deg,
        "longitude_deg": site.longitude_deg,
        "flat": summary(flat),
        "terrain_2m": summary(terrain2),
        "terrain_blocked_hours": round(float(terrain_blocks.sum()) * 0.5, 2),
        "terrain_opened_below_tangent_hours": round(float(terrain_opens.sum()) * 0.5, 2),
        "terrain_share_of_actual_outage_pct": round(
            float(terrain_blocks.sum() / max(1, (~terrain2).sum()) * 100.0), 2
        ),
        "horizon_range_deg": {
            "min": round(float(np.min(masks[2.0].elevations_deg)), 3),
            "max": round(float(np.max(masks[2.0].elevations_deg)), 3),
        },
        "antenna_height": heights,
        "best_200m_shift": nearby[0],
    }


def main() -> None:
    load_kernels(DATA / "kernels")
    rows = [scan(site) for site in SITES]
    rows.sort(key=lambda x: (x["terrain_blocked_hours"], x["terrain_share_of_actual_outage_pct"]), reverse=True)
    result = {
        "period": "2026-11-01 through 2026-12-01 UTC",
        "step_minutes": 30,
        "site_coordinate_note": "Approximate candidate-region centers; Malapert uses NASA notional study point.",
        "sites_ranked_by_terrain_blocking": rows,
    }
    out = ROOT / "analysis" / "moonmesh_nine_region_scan.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
