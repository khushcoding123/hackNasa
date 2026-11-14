#!/usr/bin/env python3
"""Run the relay upper-bound gate on the two terrain-limited region centers."""

from __future__ import annotations

import json

from lunar_core.kernels import load_kernels

from moonmesh_geometry_spike import DATA, ROOT, Site, analyze_site


def main() -> None:
    load_kernels(DATA / "kernels")
    sites = (
        Site("nobile-rim-1", "Nobile Rim 1", -85.4, 37.3),
        Site("haworth", "Haworth", -86.8, 337.2),
    )
    result = {
        "test": "geometry-only relay upper bound on terrain-limited region centers",
        "sites": [analyze_site(site) for site in sites],
    }
    out = ROOT / "analysis" / "moonmesh_relay_gate.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
