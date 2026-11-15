"""Read-only, full-pixel inventory. Rasterio supplies the bundled GDAL drivers.

Statistics are on stored values, excluding GDAL masks and nonfinite values.
Scale/offset are reported separately: a PDS radius offset is NOT a height offset.
Archive members are opened through GDAL's read-only /vsitar/ filesystem.
"""

from collections import Counter
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import re
import subprocess
import warnings

import numpy as np
from pyproj import CRS, Transformer
import rasterio
from rasterio.windows import Window
import tarfile
import yaml

from . import paths
from .downloads import atomic_json, sha256_file

RASTER_SUFFIXES = {".tif", ".tiff", ".lbl", ".img", ".vrt", ".jp2", ".png", ".jpg", ".jpeg"}
TEXT_SUFFIXES = {".json", ".csv", ".txt", ".md", ".yaml", ".yml", ".xml", ".lbl"}


def clean(value):
    """Strict JSON: retain a nonfinite metadata value's identity as a string."""
    if isinstance(value, (np.integer, np.floating)):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def proj4(crs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return CRS(crs).to_proj4()


def same_projection(a, b):
    """Compare projection geometry, keeping datum-name equality separate."""
    return CRS.from_proj4(proj4(a)).equals(CRS.from_proj4(proj4(b)))


def windows(src, size=1024):
    for row in range(0, src.height, size):
        for col in range(0, src.width, size):
            yield Window(col, row, min(size, src.width-col), min(size, src.height-row))


def band_stats(src, band):
    n = nodata = masked_other = nonfinite = 0
    total = 0.0
    lo, hi = math.inf, -math.inf
    low_count = high_count = 0
    nd = src.nodatavals[band-1]
    for window in windows(src):
        raw = src.read(band, window=window)
        mask = src.read_masks(band, window=window) == 0
        nd_mask = (np.isnan(raw) if nd is not None and math.isnan(nd) else raw == nd)
        if nd is None:
            nd_mask = np.zeros(raw.shape, dtype=bool)
        nodata += int(nd_mask.sum())
        masked_other += int((mask & ~nd_mask).sum())
        nonfinite += int((~np.isfinite(raw) & ~nd_mask & ~mask).sum())
        vals = raw[~mask & ~nd_mask & np.isfinite(raw)].astype(np.float64)
        if not vals.size:
            continue
        n += vals.size
        total += float(vals.sum(dtype=np.float64))
        small, large = float(vals.min()), float(vals.max())
        if small < lo:
            lo, low_count = small, 0
        if large > hi:
            hi, high_count = large, 0
        low_count += int((vals == lo).sum())
        high_count += int((vals == hi).sum())
    counts = np.zeros(20, dtype=np.int64)
    edges = np.histogram_bin_edges([lo, hi], bins=20) if n else None
    if n:
        for window in windows(src):
            data = src.read(band, window=window, masked=True)
            vals = data.compressed()
            vals = vals[np.isfinite(vals)].astype(np.float64)
            # An explicit dataset mask can override GDAL's nodata mask.
            # Keep the histogram population identical to the first pass.
            if nd is not None and math.isfinite(nd):
                vals = vals[vals != nd]
            counts += np.histogram(vals, bins=edges)[0]
    return {"valid_count": int(n), "nodata_count": nodata,
            "other_masked_count": masked_other, "nonfinite_non_nodata_count": nonfinite,
            "min": lo if n else None, "max": hi if n else None,
            "mean": total/n if n else None, "minimum_count": low_count, "maximum_count": high_count,
            "histogram": {"counts": counts.tolist(), "edges": edges.tolist() if n else None},
            "statistics_domain": "stored values; GDAL mask/nodata and nonfinite pixels excluded"}


def all_tags(src, band=0):
    return {"default": src.tags(band), **{ns: src.tags(band, ns=ns) for ns in src.tag_namespaces(band)}}


def parse_pds(text):
    entries = []
    for line, content in enumerate(text.splitlines(), 1):
        match = re.match(r"\s*([\w^]+)\s*=\s*(.*?)\s*$", content)
        if match:
            entries.append({"key": match[1], "value": match[2], "line": line})
    fields = {}
    for key in ("SCALING_FACTOR", "OFFSET", "MISSING_CONSTANT", "INVALID_CONSTANT", "UNIT"):
        hits = [item for item in entries if item["key"] == key]
        fields[key] = hits or None
    return {"fields": fields, "assignments": entries,
            "conversion_lines": [{"line": i, "text": s.strip()} for i, s in enumerate(text.splitlines(), 1)
                                 if any(k in s for k in ("HEIGHT =", "PLANETARY_RADIUS =", "reference radius", "MEAN EARTH"))],
            "missing_field_note": "null = NOT FOUND; TODO inspect provider documentation, never assume"}


def polygon_area(p):
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(p, p[1:]+p[:1])))/2 if p else 0.0


def clip_rectangle(poly, bounds):
    """Sutherland-Hodgman clipping, including rotated affine footprints."""
    left, bottom, right, top = bounds
    for axis, edge, lower in [(0, left, True), (0, right, False), (1, bottom, True), (1, top, False)]:
        result = []
        if not poly:
            break
        previous = poly[-1]
        for current in poly:
            inside = current[axis] >= edge if lower else current[axis] <= edge
            was_inside = previous[axis] >= edge if lower else previous[axis] <= edge
            if inside != was_inside:
                t = (edge-previous[axis])/(current[axis]-previous[axis])
                result.append(tuple(previous[k]+t*(current[k]-previous[k]) for k in (0, 1)))
            if inside:
                result.append(current)
            previous = current
        poly = result
    return poly


def grid_overlap(src, reference):
    if src.crs is None:
        return {"percent_of_source_footprint": None, "exact_20m_grid": None,
                "reason": "NOT FOUND: raster CRS"}
    a, b = src.transform, reference.transform
    same = same_projection(src.crs, reference.crs)
    strict_crs_equal = CRS(src.crs).equals(CRS(reference.crs))
    offset = [(a.c-b.c)/b.a, (a.f-b.f)/b.e] if same else None
    fraction = [v-round(v) for v in offset] if offset is not None else None
    exact = bool(same and np.allclose([a.a, a.b, a.d, a.e], [20, 0, 0, -20], atol=1e-9, rtol=0)
                 and np.allclose(fraction, 0, atol=1e-7, rtol=0))
    corners = [(0, 0), (src.width, 0), (src.width, src.height), (0, src.height)]
    poly = []
    for start, end in zip(corners, corners[1:]+corners[:1]):
        for t in np.linspace(0, 1, 65, endpoint=False):
            col = float(start[0]+t*(end[0]-start[0]))
            row = float(start[1]+t*(end[1]-start[1]))
            poly.append((a.a*col+a.b*row+a.c, a.d*col+a.e*row+a.f))
    if not same:
        transform = Transformer.from_crs(CRS(src.crs), CRS(reference.crs), always_xy=True)
        poly = [transform.transform(x, y, errcheck=True) for x, y in poly]
    area = polygon_area(poly)
    return {"percent_of_source_footprint": 100*polygon_area(clip_rectangle(poly, list(reference.bounds)))/area,
            "area_method": "planar polygon area in reference CRS; 65 samples per edge if transformed; not valid-data overlap",
            "same_projection_geometry": same,
            "crs_equals_including_datum_names": strict_crs_equal,
            "origin_offset_reference_pixels": offset, "fractional_offset_reference_pixels": fraction,
            "geometrically_aligned_20m_grid": exact,
            "exact_20m_grid": exact and strict_crs_equal}


def haworth_point(src, lon, lat):
    if src.crs is None:
        return {"inside": None, "row": None, "column": None, "reason": "NOT FOUND: CRS"}
    crs = CRS(src.crs)
    x, y = Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True).transform(lon, lat, errcheck=True)
    inv = ~src.transform
    col_f, row_f = inv.a*x+inv.b*y+inv.c, inv.d*x+inv.e*y+inv.f
    row, col = math.floor(row_f), math.floor(col_f)
    return {"latitude": lat, "longitude_east": lon, "x": x, "y": y,
            "inside": 0 <= row < src.height and 0 <= col < src.width,
            "row": row, "column": col, "fractional_row": row_f, "fractional_column": col_f,
            "pixel_convention": "zero-based row,column; floor of inverse affine; outside indices retained"}


def inspect_raster(source, reference, lon, lat):
    with rasterio.open(source["open_path"]) as src:
        crs = CRS(src.crs) if src.crs else None
        radius = crs.ellipsoid.semi_major_metre if crs and crs.ellipsoid.inverse_flattening == 0 else None
        result = {**source, "driver": src.driver, "crs_wkt": crs.to_wkt() if crs else None,
                  "crs_proj4": proj4(crs) if crs else None, "sphere_radius_m": radius,
                  "semi_major_m": crs.ellipsoid.semi_major_metre if crs else None,
                  "semi_minor_m": crs.ellipsoid.semi_minor_metre if crs else None,
                  "bounds": list(src.bounds), "pixel_size": list(src.res), "shape": [src.height, src.width],
                  "transform": list(src.transform)[:6], "count": src.count, "tags": all_tags(src),
                  "overlap": grid_overlap(src, reference), "haworth_point": haworth_point(src, lon, lat),
                  "bands": [], "flags": [],
                  "label_origin": "simulated" if "lunarrf" in source["path"].lower() and "/context/" not in source["path"] else None}
        if crs and radius != 1737400:
            result["flags"].append("CRS sphere radius is not 1737400 m")
        if not crs:
            result["flags"].append("No CRS: non-geospatial image or incomplete georeferencing")
        elif result["overlap"].get("same_projection_geometry") and not result["overlap"].get("crs_equals_including_datum_names"):
            result["flags"].append("Projection geometry matches, but named CRS/datum differs; frame equivalence is not established")
        fraction = result["overlap"].get("fractional_offset_reference_pixels")
        if fraction is not None and not np.allclose(fraction, 0, atol=1e-7, rtol=0):
            result["flags"].append("Fractional pixel origin offset relative to Haworth 20 m grid")
        for band in src.indexes:
            stats = band_stats(src, band)
            entry = {"index": band, "dtype": src.dtypes[band-1], "block_shape": list(src.block_shapes[band-1]),
                     "scale": src.scales[band-1], "offset": src.offsets[band-1], "nodata": src.nodatavals[band-1],
                     "description": src.descriptions[band-1], "unit": src.units[band-1],
                     "mask_flags": [v.name for v in src.mask_flag_enums[band-1]], "tags": all_tags(src, band), **stats}
            result["bands"].append(entry)
            if stats["valid_count"]:
                frac = stats["minimum_count"]/stats["valid_count"]
                hist = stats["histogram"]["counts"]
                if frac >= .01 and stats["min"] != stats["max"]:
                    result["flags"].append(f"Band {band}: exact-minimum spike ({frac:.2%}); possible sentinel or legitimate class")
                if hist[0] >= .1*stats["valid_count"] and hist[0] >= 5*max(1, hist[1]):
                    result["flags"].append(f"Band {band}: isolated first histogram bin; inspect possible floor/sentinel")
                if ("best_prx" in source["path"] or "best_norefl" in source["path"]) and stats["min"] < -200:
                    result["flags"].append(f"Band {band}: very low power ({stats['min']}), possibly an unmasked floor; NOT confirmed sentinel")
            if src.nodatavals[band-1] == 0 and "count" in source["path"]:
                result["flags"].append("Zero is declared nodata in a count layer; zero service and missing data may be conflated")
        if source["path"].lower().endswith(".lbl") and "!" not in source["path"]:
            result["pds_label"] = parse_pds(Path(source["path"]).read_text(encoding="utf-8"))
        return clean(result)


def discover(root, manifest_path):
    files, sources, archives = [], [], []
    for part in ("raw", "interim"):
        for p in sorted((root/part).rglob("*")):
            if not p.is_file():
                continue
            files.append({"path": p.as_posix(), "size_bytes": p.stat().st_size})
            suffix = p.suffix.lower()
            if suffix in RASTER_SUFFIXES:
                if suffix == ".img" and (p.with_suffix(".LBL").exists() or p.with_suffix(".lbl").exists()):
                    continue
                sources.append({"path": p.as_posix(), "open_path": p.as_posix(), "location": part})
            if suffix == ".tar":
                with tarfile.open(p, "r") as archive:
                    members = [{"name": m.name, "size_bytes": m.size} for m in archive if m.isfile()]
                archives.append({"path": p.as_posix(), "members": members})
                names = {m["name"].lower() for m in members}
                for member in members:
                    name = member["name"]
                    suffix = Path(name).suffix.lower()
                    if suffix not in RASTER_SUFFIXES or (suffix == ".img" and str(Path(name).with_suffix(".lbl")).replace("\\", "/").lower() in names):
                        continue
                    sources.append({"path": p.as_posix()+"!"+name,
                                    "open_path": "/vsitar/"+p.as_posix()+"/"+name, "location": "archive"})
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"entries": []}
    for entry in manifest["entries"]:
        p = Path(entry["local_path"])
        if p.suffix.lower() == ".lbl" and p.exists() and all(s["path"] != p.as_posix() for s in sources):
            sources.append({"path": p.as_posix(), "open_path": p.as_posix(), "location": "registered_read_only_cache"})
    return files, sources, archives, manifest


def summarize_json(value, depth=0):
    """Bounded structural summaries, never a verbatim 27 MB manifest dump."""
    if isinstance(value, dict):
        if len(value) > 40:
            return {"type": "object", "count": len(value), "first_keys": list(value)[:5],
                    "first_value_schema": summarize_json(next(iter(value.values())), depth+1)}
        return {k: summarize_json(v, depth+1) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) <= 20 and depth < 3:
            return [summarize_json(v, depth+1) for v in value]
        return {"type": "array", "count": len(value), "first": summarize_json(value[0], depth+1) if value else None}
    return clean(value)


def metadata_inventory(root):
    records, documents = [], {}
    for part in ("raw", "interim"):
        for p in sorted((root/part).rglob("*")):
            if not p.is_file() or (p.suffix.lower() not in TEXT_SUFFIXES and p.name != "SHA256SUMS"):
                continue
            text = p.read_text(encoding="utf-8-sig")
            record = {"path": p.as_posix(), "sha256": sha256_file(p), "lines": len(text.splitlines())}
            if p.suffix.lower() == ".json":
                data = json.loads(text)
                documents[p.relative_to(root).as_posix()] = data
                record["summary"] = summarize_json(data)
                if p.name == "manifest.json" and "done" in data:
                    record["summary"] = {k: summarize_json(v) for k, v in data.items() if k != "done"}
                    record["summary"]["done"] = {"count": len(data["done"]),
                        "id_min": min(map(int, data["done"]), default=None), "id_max": max(map(int, data["done"]), default=None),
                        "entry_keys": sorted({k for v in data["done"].values() for k in v}),
                        "mast_height_counts": dict(Counter(str(v.get("mast_height_m")) for v in data["done"].values()))}
                record["nonstandard_nan_tokens"] = len(re.findall(r"\bNaN\b", text))
            elif p.suffix.lower() == ".csv":
                with p.open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    rows = list(reader)
                    record.update(columns=reader.fieldnames, row_count=len(rows))
                documents[p.relative_to(root).as_posix()] = rows
            else:
                record["summary"] = {"line_count": record["lines"]}
            records.append(record)
    return records, documents


def compare_dem(a_path, b_path):
    with rasterio.open(a_path) as a, rasterio.open(b_path) as b:
        check = grid_overlap(b, a)
        if not check["exact_20m_grid"]:
            return {"lunarrf": a_path.as_posix(), "pgda90": b_path.as_posix(), "stats": None,
                    "TODO": "No exact grid match: do not interpolate in inventory", "grid": check}
        left, bottom = max(a.bounds.left, b.bounds.left), max(a.bounds.bottom, b.bounds.bottom)
        right, top = min(a.bounds.right, b.bounds.right), min(a.bounds.top, b.bounds.top)
        if left >= right or bottom >= top:
            return {"lunarrf": a_path.as_posix(), "pgda90": b_path.as_posix(), "stats": None, "TODO": "No overlap"}
        wa = a.window(left, bottom, right, top).round_offsets().round_lengths()
        wb = b.window(left, bottom, right, top).round_offsets().round_lengths()
        av, bv = a.read(1, window=wa, masked=True).astype(np.float64), b.read(1, window=wb, masked=True).astype(np.float64)
        av = av*a.scales[0]+a.offsets[0]
        bv = bv*b.scales[0]+b.offsets[0]
        diff = (av-bv).compressed()
        diff = diff[np.isfinite(diff)]
        stats = {"count": int(diff.size)}
        if diff.size:
            median = float(np.median(diff))
            stats.update(min=float(diff.min()), max=float(diff.max()), mean=float(diff.mean()),
                         median=median, std=float(diff.std()), rmse=float(np.sqrt(np.mean(diff**2))),
                         mad=float(np.median(np.abs(diff-median))), p05=float(np.quantile(diff, .05)),
                         p95=float(np.quantile(diff, .95)), nonzero_count=int(np.count_nonzero(diff)))
        return {"lunarrf": a_path.as_posix(), "pgda90": b_path.as_posix(),
                "difference": "LunarRF minus PGDA90; scale/offset applied; exact pixel centers, no resampling",
                "stats": stats, "overlap_bounds": [left, bottom, right, top], "grid": check,
                "buffer_m_observed": [a.bounds.left-b.bounds.left, a.bounds.bottom-b.bounds.bottom,
                                      b.bounds.right-a.bounds.right, b.bounds.top-a.bounds.top]}


def candidate_inventory(root, documents, reference):
    base = "interim/lunarrf/meta/"
    manifest = documents.get(base+"atlas_v2/manifest.json", {})
    radio = documents.get(base+"atlas_v2/bundle/radio_params.json", {})
    done = set(manifest.get("done", {}))
    radius = radio.get("max_radius_m")
    result = []
    for name, rows in documents.items():
        if not name.endswith(".csv") or not Path(name).name.startswith("candidates"):
            continue
        entry = {"path": (root/name).as_posix(), "rows": len(rows), "columns": list(rows[0]) if rows else [],
                 "sha256": sha256_file(root/name)}
        ids = {r["cand_id"] for r in rows if "cand_id" in r}
        entry.update(unique_ids=len(ids), matches_manifest_done_ids=ids == done,
                     missing_from_manifest=len(ids-done), manifest_ids_missing=len(done-ids))
        for field in ("done", "mast_height_m", "freeze_flag", "cls"):
            if rows and field in rows[0]:
                entry[field+"_counts"] = dict(Counter(r[field] for r in rows))
        if rows and radius is not None and all(k in rows[0] for k in ("x_m", "y_m")):
            x = np.array([float(r["x_m"]) for r in rows])
            y = np.array([float(r["y_m"]) for r in rows])
            left, bottom, right, top = reference.bounds
            dx, dy = np.maximum(np.maximum(left-x, x-right), 0), np.maximum(np.maximum(bottom-y, y-top), 0)
            near = dx**2+dy**2 <= radius**2
            entry.update(max_radius_m=radius, within_range_of_window=int(near.sum()),
                         within_range_ids=[r["cand_id"] for r, keep in zip(rows, near) if keep],
                         within_range_completed=sum(bool(keep) and r["cand_id"] in done for r, keep in zip(rows, near)),
                         range_method="Euclidean projected x_m/y_m distance to closed Haworth raster rectangle, not its center; spatial eligibility only")
            if "lon_e" in rows[0] and "lat" in rows[0]:
                crs = CRS(reference.crs)
                xx, yy = Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True).transform(
                    [float(r["lon_e"]) for r in rows], [float(r["lat"]) for r in rows])
                entry["xy_vs_lonlat_max_discrepancy_m"] = float(np.max(np.hypot(x-xx, y-yy)))
        result.append(entry)
    return result


def evidence(value, source, key, status="observed"):
    return {"value": clean(value), "source": source, "key": key, "status": status,
            "TODO": "NOT FOUND; obtain authoritative metadata" if value is None else None}


def build_findings(root, docs, rasters, comparisons, catalogs):
    base = "interim/lunarrf/meta/"
    radio_path = base+"atlas_v2/bundle/radio_params.json"
    radio = docs.get(radio_path, {})
    fields = {}
    for key in ("tx_power_dbm", "tx_gain_dbi", "rx_gain_dbi", "rx_sensitivity_dbm", "mast_height_m",
                "rx_height_m", "max_radius_m", "diffraction", "los_model", "eps_r", "loss_tangent",
                "rms_height_m", "two_ray_layer", "avg_rx_height_pm_m", "avg_site_height_pm_m", "avg_samples", "n_az"):
        fields[key] = evidence(radio.get(key), radio_path, "$.'"+key+"'")
    for key in ("fixed_loss_db", "vertical_reference", "aggregation_tie_rule", "aggregation_exact_code",
                "best_los_exact_definition", "los_count_exact_definition", "count_exact_definition", "norefl_exact_definition"):
        fields[key] = evidence(None, "all locally supplied metadata (see inventory.json metadata)", key)
    stats_path = base+"atlas_v2/bundle/stats_exact/disc_exact.json"
    stats = docs.get(stats_path, {})
    fields["coverage_threshold_dbm"] = evidence(stats.get("threshold_dbm"), stats_path, "$.threshold_dbm")
    fields["datalink_threshold_dbm"] = evidence(stats.get("datalink_threshold_dbm"), stats_path, "$.datalink_threshold_dbm")
    manifest = docs.get(base+"atlas_v2/manifest.json", {})
    done = manifest.get("done", {})
    manifest_summary = {"run_hash": manifest.get("run_hash"), "engine_version": manifest.get("engine_version"),
                        "completed_candidates": len(done), "shards": len(manifest.get("shards", [])),
                        "shard_candidate_total": sum(s.get("candidates", 0) for s in manifest.get("shards", [])),
                        "mast_height_counts": dict(Counter(str(v.get("mast_height_m")) for v in done.values())),
                        "dem_products": sorted({p["dem_product"] for s in manifest.get("shards", [])
                                                for p in s.get("provenance", []) if "dem_product" in p})}
    freeze_path = root/base/"docs/candidate_freeze.md"
    freeze_text = freeze_path.read_text(encoding="utf-8") if freeze_path.exists() else ""
    expected = re.search(r"sha256\(candidates.csv\) = `([a-f0-9]{64})`", freeze_text)
    expected_hash = expected[1] if expected else None
    base_catalog = next((c for c in catalogs if c["path"].endswith("/atlas_base/candidates.csv")), {})
    catalog_hash_check = {"expected": expected_hash, "observed": base_catalog.get("sha256"),
                          "matches": expected_hash == base_catalog.get("sha256") if expected_hash else None,
                          "source": base+"docs/candidate_freeze.md:3"}
    return {"label_origin": "simulated", "fields": fields, "radio_budget": radio.get("budget"),
            "manifest_summary": manifest_summary, "base_catalog_hash_check": catalog_hash_check,
            "dem_comparisons": comparisons, "candidate_catalogs": catalogs}


def write_findings(report, out):
    findings = report["lunarrf_findings"]
    root = Path(report["data_root"])
    prefix = "interim/lunarrf/meta/"
    def cite(rel, key):
        return f"`{rel}` `{key}`"
    radio = cite(prefix+"atlas_v2/bundle/radio_params.json", "$")
    manifest = cite(prefix+"atlas_v2/manifest.json", "$")
    lines = ["# LunarRF findings — simulated", "", f"Data root: `{root}`. Relative source paths below resolve under this root. Commit: `{report['git_commit']}`.",
             "All LunarRF RF/LOS products and validation results are **simulated**. No config values were changed.", "",
             "## Radio, threshold and propagation", "", "| Item | Value | Exact source and key |", "|---|---|---|"]
    for key, item in findings["fields"].items():
        value = json.dumps(item["value"]) if item["value"] is not None else "NOT FOUND — TODO"
        lines.append(f"| {key} | {value} | {cite(item['source'], item['key'])} |")
    budget = findings["radio_budget"] or {}
    lines += ["", f"Budget metadata: {radio} `$.budget` = `{json.dumps(budget)}`.",
              "The exact coverage threshold above agrees with `atlas_v2/bundle/store.json: $.sensitivity_dbm` and the radio receiver sensitivity. The rounded budget threshold is separate from the +10 dB datalink threshold. No fixed cable/system loss or extra required margin is explicitly stated; do not substitute the app defaults.",
              "LOS and diffraction are both named `delta_bullington` in the supplied metadata. Reflection is `height_averaged` two-ray with the listed dielectric, roughness and height averaging settings. Exact LOS/Fresnel criterion, polarization for the atlas, reflection gating, averaging arithmetic and diffraction implementation details: **NOT FOUND**. Validation settings describe separate experiments, not necessarily atlas settings.",
              "", "## Power units, nodata and histogram floors", "",
              f"Power is inferred to be **dBm** from {cite(prefix+'atlas_v2/bundle/stats_exact/disc_exact.json', '$.threshold_dbm; $.bands[*].map.median_prx_dbm')} and {radio} `$.rx_sensitivity_dbm`. Explicit GeoTIFF power unit metadata is **NOT FOUND** (band unit is null).", "",
              "All statistics below exclude declared nodata, GDAL masks and nonfinite values. Full 20-bin histograms and all tags are in `inventory.json: rasters[*].bands[*]`. A floor retained by GDAL is not automatically a confirmed sentinel.", "",
              "| Haworth layer | Declared nodata | Nodata pixels | Valid min / max / mean | Exact minimum count |", "|---|---:|---:|---|---:|"]
    local = [r for r in report["rasters"] if r["location"] == "interim" and "/Haworth/" in r["path"]]
    for r in local:
        b = r["bands"][0]
        lines.append(f"| `{Path(r['path']).name}` | {b['nodata']} | {b['nodata_count']} | {b['min']} / {b['max']} / {b['mean']} | {b['minimum_count']} |")
    lines += ["", "Sources: the named GeoTIFFs under `interim/lunarrf/Haworth/atlas_v2/bundle/regions/` (DEM/PSR under `context/regions/`), GDAL band 1 nodata and computed full-pixel statistics. Count zero is declared nodata: this conflates absent service with invalid data; retain a separate validity policy before training. Very low valid power values must not be removed merely because they look like a floor.",
              "", "## Terrain and vertical reference", "",
              f"Atlas DEM provenance: {manifest} `$.shards[*].provenance[*].dem_product`; Haworth terrain band 1 description is `PGDA 20 m DEM (m)` in `interim/lunarrf/Haworth/atlas_v2/bundle/context/regions/Haworth__dem.tif`. The manifest identifies `LDEM_80S_20MPP_ADJ`. Local PGDA windows retain `NC_GLOBAL#history` describing conversion of `ldem_80s_final_adj_20mpp_surf.grd` by 1000.",
              "An explicit vertical datum/reference for the atlas/PGDA GeoTIFFs is **NOT FOUND**. The 1,737,400 m horizontal CRS sphere alone does not establish a vertical reference. The PDS labels independently state heights relative to 1,737.4 km and DE421 mean Earth/polar-axis coordinates; that statement is not transferred to PGDA without evidence.", "",
              "Difference is LunarRF minus PGDA on identical pixel centers, GDAL scale/offset applied, joint valid mask, no interpolation. Units are metres from the LunarRF band description; PGDA unit metadata is not explicit.", "",
              "| PGDA window | Buffer on four sides (m) | Compared pixels | Min / max / mean (m) | Median / MAD / RMSE (m) |", "|---|---|---:|---|---|"]
    for c in findings["dem_comparisons"]:
        s = c.get("stats") or {}
        lines.append(f"| `{c['pgda90']}` | {c.get('buffer_m_observed')} | {s.get('count')} | {s.get('min')} / {s.get('max')} / {s.get('mean')} | {s.get('median')} / {s.get('mad')} / {s.get('rmse')} |")
    lines += ["", "## Mast catalog, active set and aggregation", "",
              f"The atlas-v2 input catalog is `interim/lunarrf/meta/data/atlas_base/candidates.csv`: {cite(prefix+'figures/validation/sensitivity_dem-product.json', '$.config; $.settings.candidates')} links `configs/run_atlas_v2.yaml` to that original path. {cite(prefix+'docs/candidate_freeze.md', 'lines 1–3')} names the base catalog and its SHA256. The exported v2 result table is `interim/lunarrf/meta/data/atlas_v2/bundle/candidates_coverage.csv`, with `done` and `mast_height_m` columns. The freeze and side-run catalogs are not interchangeable with the base catalog.", "",
              "| Catalog | Rows | Unique IDs | IDs equal manifest done set | Within max range of window | Completed within range |", "|---|---:|---:|---|---:|---:|"]
    for c in findings["candidate_catalogs"]:
        lines.append(f"| `{c['path']}` | {c['rows']} | {c['unique_ids']} | {c['matches_manifest_done_ids']} | {c.get('within_range_of_window')} | {c.get('within_range_completed')} |")
    for c in findings["candidate_catalogs"]:
        lines += ["", f"`{c['path']}` line 1 columns: `{', '.join(c['columns'])}`. SHA256: `{c['sha256']}`. x/y versus lon/lat reprojection maximum discrepancy: {c.get('xy_vs_lonlat_max_discrepancy_m')} m."]
    lines += ["", f"Active-set evidence: {manifest} `$.done` contains completed candidate IDs; `$.shards[*].candidates` gives shard counts. IDs are checked against each catalog above. The eligible mast count uses the published max radius and Euclidean projected distance from each `x_m,y_m` to the **full Haworth raster footprint**, including corner distances, not distance to the central point. It is a spatial eligibility count, not proof that every mast serves a pixel. ID lists and method are in `inventory.json: lunarrf_findings.candidate_catalogs`.",
              "The expected aggregation is maximum received power across active masts and its argmax candidate ID; **explicit aggregation algorithm and tie handling: NOT FOUND** in supplied metadata. Manifest completion and catalog matches support using the full completed set, but a distinct activation/filter policy is **NOT FOUND**. Do not infer the active set only from IDs that win in this window.",
              "", "## LOS, counts and no-reflection layers", "",
              f"{cite(prefix+'atlas_v2/bundle/stats_exact/disc_exact.json', '$.los_source; $.bands[*].map.coverable_best_server_nlos_share; $.bands[*].map.los_any_pct; $.bands[*].map.mean_count_covered; $.bands[*].map.redundant2_pct')} supports the following interpretations, but does not contain formal layer definitions:",
              "- `best_los`: inferred LOS flag for the power-selected best server, not any-server LOS.",
              "- `los_count`: inferred number of LOS candidates, independent of band; it need not equal coverage count.",
              "- `count`: inferred number of candidates meeting the band-specific threshold.",
              "- `best_norefl`, `best_id_norefl`, `best_los_norefl`, `count_norefl`: inferred separately aggregated no-reflection power, its selected ID/LOS, and threshold count; diffraction remains a separate model setting. Exact disabling/gating and tie rules: **NOT FOUND**.",
              f"Evidence for two variants: {cite(prefix+'atlas_v2/manifest.json', '$.done[*].area_km2; $.done[*].area_norefl_km2; $.done[*].los_metrics')}, {cite(prefix+'figures/validation/pilot_diff_T06_07.json', '$.a_layer; $.bands[*].identity_layers')}, and the GeoTIFF band descriptions. These interpretations are explicitly inferential; confirm implementation before an exact simulator reproduction.",
              "", "## Manifest and validation audit", "",
              "The 27 MB manifest was parsed with Python and structurally summarized; it is never printed whole. `inventory.json: metadata` records its hash, run hash, engine version, completed-ID count/range, mast-height distribution, shard provenance and timestamps. Nonstandard NaN tokens (not valid strict JSON) are counted and represented as strings in output metadata.",
              f"Manifest summary (`atlas_v2/manifest.json`: `$.run_hash`, `$.engine_version`, `$.done`, `$.shards`): `{json.dumps(findings['manifest_summary'])}`.",
              f"Base catalog hash verification against `interim/lunarrf/meta/docs/candidate_freeze.md:3`: `{json.dumps(findings['base_catalog_hash_check'])}`. The document also gives a 'frozen-table hash'; its hashing/serialization definition is NOT FOUND, so it is not treated as a file-SHA256 guarantee.",
              "Every extracted JSON/CSV/text metadata file, including all `figures/validation/**/*.json` and `stats_exact/*`, was read and is summarized in `inventory.json: metadata`. Their distinct run settings are preserved. Validation is simulator-to-simulator evidence, not measured lunar RF validation.",
              "", "## Remaining evidence gaps", "",
              "TODO: obtain explicit atlas units/vertical reference, fixed system losses, aggregation/LOS/count definitions and tie rules, and confirm very low power-floor semantics. No unknown was filled with a config default. All unknown machine-readable findings are null with TODOs."]
    lines += ["", "## Measured power-floor diagnostics", ""]
    power = next((r for r in local if r["path"].endswith("Haworth__best_prx_1p800GHz.tif")), None)
    if power:
        b = power["bands"][0]
        lines += [f"`{power['path']}`, band 1: minimum {b['min']} occurs {b['minimum_count']} time(s); "
                  f"20-bin counts = `{b['histogram']['counts']}`. "
                  "The count at the exact minimum is an observed diagnostic, not an undocumented sentinel declaration."]
    for r in report["rasters"]:
        if any("very low power" in f for f in r["flags"]):
            b = r["bands"][0]
            lines += ["", f"`{r['path']}`, band 1 minimum {b['min']} occurs {b['minimum_count']} time(s), "
                      f"declared nodata {b['nodata']}. A single extreme value is not a repeated sentinel spike; "
                      "semantics remain unconfirmed."]
    (out/"lunarrf_findings.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def write_reports(report, out):
    atomic_json(out/"inventory.json", clean(report))
    write_findings(report, out)
    overlap = ["# Raster overlap and Haworth point", "", "Overlap is percent of the source footprint, not percent of the reference or valid pixels. Exact 20 m grid requires full CRS equality and geometric alignment; projection-only alignment is separately retained in JSON. Pixel indices are zero-based. Null means unavailable, never zero overlap.", "",
               "| Raster | Overlap % | Exact 20 m grid | Origin offset (reference pixels) | Haworth inside | Row, column |", "|---|---:|---|---|---|---|"]
    for r in report["rasters"]:
        o, p = r["overlap"], r["haworth_point"]
        percent = o["percent_of_source_footprint"]
        overlap.append(f"| `{r['path']}` | {percent:.8f}" if percent is not None else f"| `{r['path']}` | null")
        overlap[-1] += f" | {o['exact_20m_grid']} | {o.get('origin_offset_reference_pixels')} | {p['inside']} | {p['row']}, {p['column']} |"
    (out/"inventory_overlap.md").write_text("\n".join(overlap)+"\n", encoding="utf-8")
    local = [r for r in report["rasters"] if r["location"] != "archive" and r["driver"] != "PNG"]
    # Keep the requested overview to one page; the full table retains every file.
    grouped = []
    for r in local:
        signature = (r["crs_wkt"], tuple(r["transform"]), tuple(r["shape"]))
        group = next((g for g in grouped if g[0] == signature), None)
        if group is None:
            grouped.append((signature, [r]))
        else:
            group[1].append(r)
    lines = ["# Data inventory", "", f"Root: `{report['data_root']}`. Commit `{report['git_commit']}`; seed {report['seed']}. LunarRF labels are **simulated**. Configs unchanged.",
             f"Scanned {len(report['files'])} files, {len(report['archives'])} archives and {len(report['rasters'])} raster datasets (including archive members, figures and registered read-only PDS labels); {len(report['errors'])} errors. Every pixel was read; histograms use 20 bins. Metadata files read: {len(report['metadata'])}.", "",
             "| Local raster | Shape (rows × columns) | Pixel m | Overlap % | 20 m aligned | Haworth pixel (row,col) |", "|---|---|---|---:|---|---|"]
    for _, group in grouped:
        r = group[0]
        o, p = r["overlap"], r["haworth_point"]
        percent = o["percent_of_source_footprint"]
        name = Path(r["path"]).name.replace("Haworth__", "")
        if len(group) > 1:
            name = f"Haworth layers ({len(group)} rasters; see full table)"
        lines.append(f"| {name} | {' × '.join(map(str,r['shape']))} | {r['pixel_size'][0]} | {percent:.3f} | {o['exact_20m_grid']} | {p['row']},{p['column']} ({p['inside']}) |" if percent is not None else f"| {name} | {r['shape']} | — | — | — | — |")
    lines += ["", "Both PGDA 90 windows are reported separately above; their observed buffers and exact-pixel DEM differences are in [LunarRF findings](lunarrf_findings.md). Full archive/local overlap and point table: [inventory_overlap.md](inventory_overlap.md). Bounds, CRS/radius, dtype, block shape, scale/offset, nodata counts, all tags and histograms: [inventory.json](inventory.json).", "",
              "Flags are diagnostic heuristics, not metadata: exact minima occupying ≥1% or a first bin occupying ≥10% and ≥5× its neighbor are inspected for spikes; power below −200 is flagged for review. Categorical spikes may be legitimate. Counts declare zero nodata. PDS GDAL offset produces planetary radius; the label's height formula omits that offset.",
              f"CRS radius flags: {sum('CRS sphere radius' in f for r in report['rasters'] for f in r['flags'])}; fractional-grid flags: {sum('Fractional pixel' in f for r in report['rasters'] for f in r['flags'])}; raster datasets with any flag: {sum(bool(r['flags']) for r in report['rasters'])}.",
              "PDS uses a differently named CRS/datum: its projection and pixel lattice align geometrically, but strict CRS equality is false. Exact-grid results require both geometry and CRS equality; this does not prove frame equivalence.",
              "Scope: archive duplicates are retained with their own paths; PNG figures have no geographic overlap. Source hashes/licenses/acquisition metadata from training/data_manifest.json are preserved in JSON; unverified license/vertical-reference values remain unverified. Future model inputs must exclude RF/LOS/count/ID outputs."]
    if report["errors"]:
        lines.append("ERRORS: "+json.dumps(report["errors"]))
    (out/"inventory.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def run_inventory(config_path):
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    random.seed(config["seed"])
    root, out = paths.data_root(), paths.reports_dir()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.repo_root(), capture_output=True, text=True, timeout=10)
    report = {"schema_version": 1, "data_root": root.as_posix(), "started_utc": datetime.now(timezone.utc).isoformat(),
              "git_commit": commit.stdout.strip() if commit.returncode == 0 else None,
              "config": config_path.resolve().as_posix(), "config_sha256": sha256_file(config_path),
              "seed": config["seed"], "label_origin": "simulated", "rasters": [], "errors": [],
              "versions": {"rasterio": rasterio.__version__, "gdal": rasterio.__gdal_version__}}
    print(f"Git commit: {report['git_commit']}; seed: {report['seed']}; data root: {root}; LunarRF simulated", flush=True)
    files, sources, archives, acquisition = discover(root, paths.repo_root()/"training/data_manifest.json")
    report.update(files=files, archives=archives, acquisition_manifest=acquisition)
    reference_path = root/"interim/lunarrf/Haworth/atlas_v2/bundle/regions/Haworth__best_prx_1p800GHz.tif"
    report["reference_raster"] = reference_path.as_posix()
    metadata, documents = metadata_inventory(root)
    report["metadata"] = metadata
    # PAM disabled explicitly: no .aux.xml sidecars may be created in raw/cache.
    with rasterio.Env(GDAL_PAM_ENABLED="NO"), rasterio.open(reference_path) as reference:
        for i, source in enumerate(sources, 1):
            print(f"[{i}/{len(sources)}] {source['path']}", flush=True)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
                    report["rasters"].append(inspect_raster(source, reference, config["longitude_deg"], config["latitude_deg"]))
            except Exception as exc:
                report["errors"].append({"path": source["path"], "error": str(exc)})
                print(f"ERROR: {exc}", flush=True)
        comparisons = [compare_dem(root/"interim/lunarrf/Haworth/atlas_v2/bundle/context/regions/Haworth__dem.tif", p)
                       for p in sorted((root/"interim/pgda90").glob("*.tif"))]
        catalogs = candidate_inventory(root, documents, reference)
    report["lunarrf_findings"] = build_findings(root, documents, report["rasters"], comparisons, catalogs)
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    report["status"] = "complete" if not report["errors"] else "partial_with_errors"
    write_reports(report, out)
    print(f"Reports: {out}; {len(report['rasters'])} rasters; {len(report['errors'])} errors", flush=True)
    return int(bool(report["errors"]))
