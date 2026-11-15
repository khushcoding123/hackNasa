"""Prompt 12: reproduce published lunar surface-coverage studies with MOONMESH's deterministic viewshed.

Simulated. Every result is simulator-to-simulator consistency between our LOS tracer on LOLA PDS terrain and the
published study's own tool, never validation against measurements. Study values and their page numbers live in
training/configs/benchmarks.yaml; anything a study does not state is reported as NOT STATED, and every value we had
to choose to run a case is listed as an assumption next to its result.
"""
from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from pyproj import CRS
from rasterio.transform import Affine

from .config import configs_dir
from .los import PROJECTION, R, viewshed
from .paths import repo_root, reports_dir
from .rf import label_arrays

VISIBLE = 1   # los code for a clear line of sight (0 = blocked, 255 = not traced / unknown)


def load_config(name: str = "benchmarks.yaml") -> dict[str, Any]:
    with (configs_dir() / name).open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root(), capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# ---------------------------------------------------------------- terrain
def polar_xy(lat: float, lon: float) -> tuple[float, float]:
    """South polar stereographic x, y (m), the same construction as lunar_core.terrain and the LOLA polar DEMs."""
    rho = 2 * R * math.tan(math.radians(90 + lat) / 2)
    return rho * math.sin(math.radians(lon)), rho * math.cos(math.radians(lon))


def area_scale(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """True surface area of one projected square metre: 1/k² with stereographic scale k = 1 + ρ²/4R²."""
    k = 1 + (x * x + y * y) / (4 * R * R)
    return 1 / (k * k)


def load_window(lat: float, lon: float, half_m: float, product: str = "LDEM_85S_20M"):
    """A north-up DEM window (metres, NaN for nodata) centred on a point, read from the app's LOLA cache (read-only)."""
    from moonmesh import data as moon_data   # imported here so tests that do not need terrain stay light
    window = moon_data.crop(("benchmark", lat, lon), product, half_m)
    px = float(window["pixel_size_m"])
    transform = Affine(px, 0, float(window["x_left"]), 0, -px, float(window["y_top"]))
    return np.ascontiguousarray(window["heights"], dtype=np.float64), transform


def decimate(dem: np.ndarray, transform: Affine, factor: int):
    """Block-mean a DEM by an integer factor (NaN-aware: a block with any nodata stays nodata)."""
    if factor <= 1:
        return dem, transform
    rows, cols = (dem.shape[0] // factor) * factor, (dem.shape[1] // factor) * factor
    blocks = dem[:rows, :cols].reshape(rows // factor, factor, cols // factor, factor)
    out = blocks.mean(axis=(1, 3))
    return np.ascontiguousarray(out), Affine(transform.a * factor, 0, transform.c, 0, transform.e * factor, transform.f)


def centres(dem: np.ndarray, transform: Affine):
    rows, cols = np.mgrid[0:dem.shape[0], 0:dem.shape[1]]
    return transform.c + (cols + .5) * transform.a, transform.f + (rows + .5) * transform.e


def run_viewshed(tx_xy, h_tx, h_rx, dem, transform, max_range_m, spacing_fraction=.5):
    spacing = transform.a * spacing_fraction
    return viewshed(tx_xy, h_tx, h_rx, dem, transform, CRS.from_proj4(PROJECTION), max_range_m, spacing, mode="fast")


# ---------------------------------------------------------------- cases
def case_disk(case: dict, terrain: dict) -> dict:
    """Visible area from a tower within a radius (Tall Lunar Towers, JPL, NASA/Nokia)."""
    lat, lon, radius = case["site"]["lat"], case["site"]["lon"], case["radius_m"]
    dem, transform = load_window(lat, lon, radius * 1.05, terrain["product"])
    tx = polar_xy(lat, lon)
    x, y = centres(dem, transform)
    cell = transform.a * -transform.e * area_scale(x, y)
    pairs = case.get("pairs_m") or [[h, case["receiver_height_m"]] for h in case["tower_heights_m"]]
    rows = []
    for h_tx, h_rx in pairs:
        started = time.perf_counter()
        v = run_viewshed(tx, h_tx, h_rx, dem, transform, radius, terrain["spacing_fraction"])
        traced = v["quality_flag"] != 3
        valid = traced & v["valid"]
        visible = valid & (v["los"] == VISIBLE)
        row = dict(tower_m=h_tx, receiver_m=h_rx, visible_km2=float(cell[visible].sum() / 1e6),
                   disk_valid_km2=float(cell[valid].sum() / 1e6), disk_km2=float(cell[traced].sum() / 1e6),
                   coverage_pct=float(100 * cell[visible].sum() / max(cell[valid].sum(), 1e-9)),
                   unknown_pct=float(100 * (1 - cell[valid].sum() / max(cell[traced].sum(), 1e-9))),
                   seconds=round(time.perf_counter() - started, 1))
        row["directional"] = directional_reach(tx, x, y, visible, traced, radius)
        for ghz in case.get("path_loss_ghz", []):
            row.setdefault("path_loss_db", {})[f"{ghz:g} GHz"] = path_loss_summary(tx, h_tx, h_rx, dem, transform, radius, ghz)
        if case.get("local_search"):
            row["local_search"] = local_search(case, terrain, tx, h_tx, h_rx, dem, transform, cell)
        rows.append(row)
    return dict(id=case["id"], study=case["study"], site=case["site"], radius_m=radius, terrain_px_m=transform.a, rows=rows,
                assumed=case.get("assumed", []))


def local_search(case, terrain, centre, h_tx, h_rx, dem, transform, cell):
    """Best tower within `radius_m` of the assumed site: shows how much the unstated exact location matters.
    Search on the decimated DEM, then re-run the best few at full resolution (whose area is what is reported)."""
    spec, radius = case["local_search"], case["radius_m"]
    coarse_dem, coarse_tr = decimate(dem, transform, terrain["search_decimation"])
    cx, cy = centres(coarse_dem, coarse_tr)
    coarse_cell = coarse_tr.a * -coarse_tr.e * area_scale(cx, cy)
    offsets = np.arange(-spec["radius_m"], spec["radius_m"] + 1, spec["step_m"])
    towers = [(centre[0] + ox, centre[1] + oy) for oy in offsets for ox in offsets if math.hypot(ox, oy) <= spec["radius_m"]]

    def area(grid_dem, grid_tr, grid_cell, tx):
        v = run_viewshed(tx, h_tx, h_rx, grid_dem, grid_tr, radius, terrain["spacing_fraction"])
        return float(grid_cell[v["valid"] & (v["los"] == VISIBLE) & (v["quality_flag"] != 3)].sum() / 1e6)

    scores = [area(coarse_dem, coarse_tr, coarse_cell, tx) for tx in towers]
    order = np.argsort(scores)[::-1][:spec["refine_top"]]
    refined = [(area(dem, transform, cell, towers[i]), int(i)) for i in order]
    best_km2, best = max(refined)
    return dict(best_visible_km2=best_km2, best_offset_m=math.dist(towers[best], centre), towers=len(towers),
                search_radius_m=spec["radius_m"], step_m=spec["step_m"], search_px_m=coarse_tr.a, refined_px_m=transform.a)


def directional_reach(tx, x, y, visible, traced, radius, bins=72):
    """Farthest visible terrain in each 5° direction: how far coverage reaches toward each bearing."""
    dx, dy = x - tx[0], y - tx[1]
    distance, bearing = np.hypot(dx, dy), (np.degrees(np.arctan2(dx, dy)) + 360) % 360
    index = (bearing // (360 / bins)).astype(int)
    reach = np.zeros(bins)
    np.maximum.at(reach, index[visible], distance[visible])
    return dict(bins=bins, median_reach_km=float(np.median(reach) / 1000),
                directions_reaching_90pct_radius=int((reach >= .9 * radius).sum()),
                directions_limited_to_1p5_km=int((reach <= 1500).sum()))


def path_loss_summary(tx, h_tx, h_rx, dem, transform, radius, ghz):
    """FSPL + single knife-edge path loss percentiles inside the disk, split by LOS. Only the loss is reported:
    the receiver sensitivity is not used (the margin fields of label_arrays are discarded)."""
    radio = dict(frequency_ghz=ghz, tx_power_dbm=0., tx_gain_dbi=0., rx_gain_dbi=0., fixed_loss_db=0.,
                 receiver_sensitivity_dbm=0., required_margin_db=0., max_range_m=radius)
    out, _, _ = label_arrays(tx, h_tx, h_rx, dem, transform, CRS.from_proj4(PROJECTION), radio)
    loss, los, valid = out["path_loss_db"], out["los"], out["valid_mask"].astype(bool)
    summary = {}
    for name, mask in (("los", valid & (los == VISIBLE)), ("nlos", valid & (los == 0))):
        values = loss[mask & np.isfinite(loss)]
        summary[name] = dict(n=int(values.size), **({f"p{p}": round(float(np.percentile(values, p)), 1) for p in (10, 50, 90)} if values.size else {}))
    return summary


def case_grid(case: dict, terrain: dict, log=print) -> dict:
    """NASA Glenn Malapert trade: best of a 1 km tower grid over a 20 × 20 km region, coverage as a share of the region."""
    lat, lon, half = case["site"]["lat"], case["site"]["lon"], case["region_half_m"]
    dem, transform = load_window(lat, lon, half, terrain["product"])
    cx, cy = polar_xy(lat, lon)
    full = dict(dem=dem, transform=transform)
    coarse_dem, coarse_tr = decimate(dem, transform, terrain["search_decimation"])
    coarse = dict(dem=coarse_dem, transform=coarse_tr)

    def region(grid):
        x, y = centres(grid["dem"], grid["transform"])
        inside = (np.abs(x - cx) <= half) & (np.abs(y - cy) <= half) & np.isfinite(grid["dem"])
        return x, y, inside, grid["transform"].a * -grid["transform"].e * area_scale(x, y)

    def coverage(grid, tx, h_tx):
        x, y, inside, cell = region(grid)
        v = run_viewshed(tx, h_tx, case["receiver_height_m"], grid["dem"], grid["transform"], 2 * math.sqrt(2) * half + 100, terrain["spacing_fraction"])
        visible = inside & v["valid"] & (v["los"] == VISIBLE)
        return float(100 * cell[visible].sum() / cell[inside].sum())

    x, y, inside, _ = region(full)
    peak = np.nanargmax(np.where(inside, full["dem"], -np.inf))
    habitat = (float(x.flat[peak]), float(y.flat[peak]))
    step = case["grid_spacing_m"]
    offsets = np.arange(-half + step / 2, half, step)
    towers = [(cx + ox, cy + oy) for oy in offsets for ox in offsets]
    results = {}
    for h_tx in case["tower_heights_m"]:
        started = time.perf_counter()
        scores = []
        for i, tx in enumerate(towers):
            scores.append(coverage(coarse, tx, h_tx))
            if (i + 1) % 50 == 0:
                log(f"  {case['id']} {h_tx} m: {i + 1}/{len(towers)} towers, best so far {max(scores):.1f}%")
        order = np.argsort(scores)[::-1][:case["refine_top"]]
        refined = [(float(coverage(full, towers[i], h_tx)), int(i)) for i in order]
        best_pct, best_i = max(refined)
        tx = towers[best_i]
        results[str(h_tx)] = dict(best_pct=best_pct, best_tower_xy=tx, best_tower_from_habitat_km=math.dist(tx, habitat) / 1000,
                                  search_px_m=coarse_tr.a, refined_px_m=transform.a, refined=[{"tower": i, "pct": p} for p, i in refined],
                                  grid_median_pct=float(np.median(scores)), grid_max_pct_search=float(max(scores)), towers=len(towers),
                                  seconds=round(time.perf_counter() - started, 1))
    return dict(id=case["id"], study=case["study"], site=case["site"], region_km=2 * half / 1000, habitat_xy=habitat,
                results=results, assumed=case.get("assumed", []))


# ---------------------------------------------------------------- reporting
def configs_markdown(config: dict) -> str:
    lines = ["# Published study configurations (Prompt 12)", "",
             "Every value as the study states it, with its PDF page. NOT STATED means the study does not give it. "
             "Source: training/configs/benchmarks.yaml.", ""]
    for key, study in config["studies"].items():
        lines += [f"## {study['title']}", "", f"File: `{study['file']}`", "", "| Value | As stated | Page |", "| --- | --- | --- |"]
        lines += [f"| {name} | {item['value']} | {item['page'] if item['page'] is not None else '—'} |" for name, item in study["values"].items()]
        lines.append("")
    return "\n".join(lines)


def pct_diff(ours: float, theirs: float) -> float:
    return 100 * (ours - theirs) / theirs


REASONS = ("terrain source and resolution (LOLA PDS 20 m here; theirs not stated or 5 m), exact site coordinates and "
           "region placement (not stated, assumed here), area method (projected-scale-corrected pixel area of clear LOS "
           "cells; unknown cells excluded) and antenna heights above a bilinear ground model")


def report_markdown(results: dict, config: dict) -> str:
    out = ["# Published benchmarks: simulator-to-simulator consistency (simulated)", "",
           f"Git commit `{results['git_commit']}`. Our deterministic straight-chord LOS tracer on LOLA PDS terrain, "
           "compared with each study's own tool. **Not validation against measurements.**", ""]
    cases = {c["id"]: c for c in config["cases"]}
    for res in results["cases"]:
        case = cases[res["id"]]
        out += [f"## {res['id']} ({config['studies'][res['study']]['title']})", ""]
        if "rows" in res:
            out += ["| Tower | Receiver | Our visible area | Our coverage of valid disk | Theirs | Difference |", "| --- | --- | --- | --- | --- | --- |"]
            published = case.get("published_km2", {})
            for row in res["rows"]:
                theirs = published.get(row["tower_m"])
                out.append(f"| {row['tower_m']} m | {row['receiver_m']} m | {row['visible_km2']:.1f} km² | {row['coverage_pct']:.1f}% "
                           f"(unknown {row['unknown_pct']:.1f}%) | {f'{theirs} km²' if theirs else 'NOT STATED'} | "
                           f"{f'{pct_diff(row['visible_km2'], theirs):+.0f}%' if theirs else '—'} |")
            if len(res["rows"]) == 2 and published:
                a, b = res["rows"]
                out.append(f"\nGain from the taller tower: ours {100 * (b['visible_km2'] / a['visible_km2'] - 1):.0f}%, theirs "
                           f"{100 * (published[b['tower_m']] / published[a['tower_m']] - 1):.0f}%.")
            for row in res["rows"]:
                s = row.get("local_search")
                if s:
                    theirs = published.get(row["tower_m"])
                    out.append(f"\nBest {row['tower_m']} m tower within {s['search_radius_m'] / 1000:g} km of the assumed site "
                               f"({s['towers']} sites, {s['step_m']:g} m grid on a {s['search_px_m']:g} m DEM, best re-run at {s['refined_px_m']:g} m): "
                               f"{s['best_visible_km2']:.1f} km², {s['best_offset_m']:.0f} m from it"
                               + (f"; {pct_diff(s['best_visible_km2'], theirs):+.0f}% vs their {theirs} km²." if theirs else "."))
            for row in res["rows"]:
                d = row["directional"]
                out.append(f"\n{row['tower_m']} m / {row['receiver_m']} m: median reach {d['median_reach_km']:.1f} km over {d['bins']} directions; "
                           f"{d['directions_reaching_90pct_radius']} reach ≥90% of the radius, {d['directions_limited_to_1p5_km']} stop within 1.5 km.")
                for band, summary in row.get("path_loss_db", {}).items():
                    parts = [f"{k.upper()} p10/p50/p90 {s.get('p10', '—')}/{s.get('p50', '—')}/{s.get('p90', '—')} dB (n={s['n']})" for k, s in summary.items()]
                    out.append(f"Path loss {band} (FSPL + knife-edge, 0 dBi antennas): " + "; ".join(parts) + ". Theirs: NOT STATED as numbers.")
        else:
            out += ["| Tower | Our best tower | Their best tower | Difference | Our best from habitat | Theirs |", "| --- | --- | --- | --- | --- | --- |"]
            for h, r in res["results"].items():
                theirs = case["published_pct"][int(h)]
                out.append(f"| {h} m | {r['best_pct']:.1f}% | {theirs}% | {pct_diff(r['best_pct'], theirs):+.0f}% | {r['best_tower_from_habitat_km']:.1f} km | 8.3 km |")
            out.append(f"\nSearch on a {res['results'][next(iter(res['results']))]['search_px_m']:.0f} m DEM over {res['results'][next(iter(res['results']))]['towers']} towers; "
                       f"the top {case['refine_top']} re-run at 20 m.")
        out += ["", "Assumptions: " + " ".join(res["assumed"]), "", f"Likely reasons for differences: {REASONS}.", ""]
    return "\n".join(out)


def run(config: dict, only: list[str] | None = None, out_dir: Path | None = None, log=print) -> dict:
    out_dir = out_dir or reports_dir() / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "configs.md").write_text(configs_markdown(config), encoding="utf-8")
    results = dict(simulated=True, kind="simulator-to-simulator consistency, not validation", git_commit=git_commit(),
                   terrain=config["terrain"], cases=[])
    for case in config["cases"]:
        if only and case["id"] not in only:
            continue
        log(f"case {case['id']}")
        results["cases"].append(case_grid(case, config["terrain"], log) if "grid_spacing_m" in case else case_disk(case, config["terrain"]))
        (out_dir / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    (out_dir / "report.md").write_text(report_markdown(results, config), encoding="utf-8")
    return results
