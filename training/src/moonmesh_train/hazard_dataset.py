"""Build the thermal hazard dataset: Diviner PRP labels joined to LOLA 240 m terrain features.

Labels are REAL (a thermal-model fit to LRO Diviner observations, LRO-L-DLRE-5-PRP-V2.0), not simulated. They are a
model product, not a raw measurement; see ``ora/hazard.py`` and ``docs/HAZARD_RESPONSE.md``.

Inputs never include anything from the PRP file except the position of each facet (used to look up terrain
features). The facet altitude column (``tri_calt``, from the Kaguya DEM) is kept only as a registration check.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import yaml
from scipy.ndimage import distance_transform_edt

from lunar_core.terrain import polar_stereographic_south
from ora import hazard

from . import paths
from .config import configs_dir

SPLITS = {"train": 0, "val": 1, "test": 2, "buffer": 3}
PRP_COLUMNS = ("tri_clon", "tri_clat", "tri_calt", "temp_avg", "temp_max", "ice_depth")   # XML field order 10..15


def raw_prp_dir(config, create=True):
    """<data root>/raw/<prp_dir>. Not a RAW_SOURCES member in paths.py, so resolved here under the same data root."""
    path = paths.data_root() / "raw" / config["source"]["prp_dir"]
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def load_config(path=None):
    path = Path(path) if path else configs_dir() / "hazard_thermal.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) | {"_path": str(path)}


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.repo_root(), text=True, capture_output=True,
                              timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def sha256(path, chunk=1 << 22):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while block := file.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def read_prp(path, expected_bytes=None):
    """Parse DLRE_PRP_SOUTH.TAB. Column positions come from the first data line (comma separated, fixed width)."""
    path = Path(path)
    size = path.stat().st_size
    if expected_bytes is not None and size != expected_bytes:
        raise ValueError(f"{path.name} is {size:,} bytes, the PDS server reports {expected_bytes:,} (truncated download?)")
    raw = np.fromfile(path, dtype=np.uint8)
    if raw.size % 210:
        raise ValueError("PRP records are 210 bytes; the file size is not a multiple of 210")
    records = raw.reshape(-1, 210)
    header = bytes(records[0]).decode("ascii")
    if "tri_clat" not in header or "ice_depth" not in header:
        raise ValueError("unexpected PRP header: " + header[:120])
    table = records[1:]
    spans, position = [], 0
    for field in bytes(table[0]).rstrip(b"\r\n").split(b","):
        spans.append((position, position + len(field)))
        position += len(field) + 1
    if len(spans) != 15:
        raise ValueError(f"expected 15 PRP columns, found {len(spans)}")
    out = {}
    for name, (start, stop) in zip(PRP_COLUMNS, spans[9:]):
        out[name] = table[:, start:stop].copy().view(f"S{stop - start}").ravel().astype(np.float64)
    return out


def tiled_features(heights, x, y, strip_rows=420):
    """Features for many points without holding a full-cap feature stack: horizontal strips with context margins."""
    row, col = hazard.xy_to_rc(x, y)
    out = np.full((len(x), len(hazard.FEATURES)), np.nan, dtype=np.float32)
    x_left, y_top = hazard.grid_axes()
    margin = hazard.MARGIN_CELLS
    inside = np.isfinite(row) & (row > 1) & (col > 1) & (row < heights.shape[0] - 2) & (col < heights.shape[1] - 2)
    first, last = int(np.floor(row[inside].min())), int(np.ceil(row[inside].max()))
    c0 = max(0, int(np.floor(col[inside].min())) - margin)
    c1 = min(heights.shape[1], int(np.ceil(col[inside].max())) + margin + 1)
    for start in range(first, last + 1, strip_rows):
        stop = min(start + strip_rows, last + 1)
        r0, r1 = max(0, start - margin), min(heights.shape[0], stop + margin)
        select = inside & (row >= start) & (row < stop)
        if not select.any():
            continue
        x0, y0 = x_left + (c0 + 0.5) * hazard.PIXEL_M, y_top - (r0 + 0.5) * hazard.PIXEL_M
        maps = hazard.feature_maps(heights[r0:r1, c0:c1], x0, y0)
        out[select] = hazard.sample_maps(maps, x[select], y[select], x_left + c0 * hazard.PIXEL_M, y_top - r0 * hazard.PIXEL_M)
    return out


def assign_blocks(x, y, config, seed):
    """Return (split code per point, block id per point). Whole blocks are held out; a buffer ring is excluded from training."""
    split = config["split"]
    block = split["block_km"] * 1000.0
    bx, by = np.floor(x / block).astype(int), np.floor(y / block).astype(int)
    key = (bx - bx.min()) * 10_000 + (by - by.min())
    unique, inverse, counts = np.unique(key, return_inverse=True, return_counts=True)
    usable = np.flatnonzero(counts >= split["min_points_per_block"])
    rng = np.random.default_rng(seed)
    order = rng.permutation(usable)
    n = len(order)
    n_test, n_val = round(split["fractions"]["test"] * n), round(split["fractions"]["val"] * n)
    block_split = np.full(len(unique), SPLITS["buffer"], dtype=np.int8)      # blocks too small to use stay excluded
    block_split[order[:n_test]] = SPLITS["test"]
    block_split[order[n_test:n_test + n_val]] = SPLITS["val"]
    block_split[order[n_test + n_val:]] = SPLITS["train"]
    point_split = block_split[inverse].copy()
    # Drop training points within buffer_km of any held-out point: terrain features and the thermal field are spatially smooth.
    km = 1000.0
    edge = 320 * km
    size = int(2 * edge / km)
    held = np.zeros((size, size), dtype=bool)
    ix, iy = ((x + edge) / km).astype(int).clip(0, size - 1), ((y + edge) / km).astype(int).clip(0, size - 1)
    heldout_points = np.isin(point_split, (SPLITS["val"], SPLITS["test"]))
    held[ix[heldout_points], iy[heldout_points]] = True
    distance = distance_transform_edt(~held)
    near = (point_split == SPLITS["train"]) & (distance[ix, iy] < split["buffer_km"])
    point_split[near] = SPLITS["buffer"]
    return point_split, inverse.astype(np.int32)


def build(config, out_path=None, prp_path=None, dem_path=None):
    seed = config["seed"]
    root = raw_prp_dir(config, create=False)
    prp_path = Path(prp_path) if prp_path else root / "dlre_prp_south.tab"
    from moonmesh.data import DATA
    dem_path = Path(dem_path) if dem_path else DATA / "terrain" / config["source"]["dem"]
    for item in (prp_path, dem_path):
        if not item.is_file():
            raise FileNotFoundError(f"{item} is missing; run training/scripts/18_download_diviner_prp.py (PRP) or python -m moonmesh.data (DEM)")
    prp = read_prp(prp_path, config["source"].get("prp_bytes"))
    lat, lon = prp["tri_clat"], prp["tri_clon"] % 360
    keep = (lat <= config["domain"]["max_lat_deg"]) & np.isfinite(prp["temp_avg"]) & np.isfinite(prp["temp_max"])
    x, y = polar_stereographic_south(lat[keep], lon[keep])
    heights = hazard.load_heights(dem_path)
    features = tiled_features(heights, x, y)
    usable = np.isfinite(features).all(axis=1)
    kept = np.flatnonzero(keep)[usable]
    x, y, features = x[usable], y[usable], features[usable]
    split, block = assign_blocks(x, y, config, seed)
    ice = prp["ice_depth"][kept]
    # Registration check (not a model input): LOLA elevation at the facet centre against the PRP's Kaguya altitude.
    lola_elevation = features[:, hazard.FEATURES.index("elevation_m")]
    kaguya = prp["tri_calt"][kept] * 1000.0
    offset = lola_elevation - kaguya
    cold_trap = (ice == config["labels"]["cold_trap_ice_depth_m"]).astype(np.int8)
    out_path = Path(out_path) if out_path else paths.derived_dir("datasets") / config["output"]["dataset"]
    np.savez_compressed(out_path, X=features, temp_avg_k=prp["temp_avg"][kept].astype(np.float32),
                        temp_max_k=prp["temp_max"][kept].astype(np.float32), ice_depth_m=ice.astype(np.float32),
                        cold_trap=cold_trap, lat=lat[kept].astype(np.float32), lon=lon[kept].astype(np.float32),
                        x=x.astype(np.float32), y=y.astype(np.float32), block=block, split=split,
                        features=np.array(hazard.FEATURES))
    counts = {name: int((split == code).sum()) for name, code in SPLITS.items()}
    manifest = dict(
        generated=datetime.now(UTC).isoformat(timespec="seconds"), git_commit=git_commit(), seed=seed, label_kind="real (Diviner-derived model product, not simulated)",
        sources=dict(prp=dict(file=prp_path.name, bytes=prp_path.stat().st_size, sha256=sha256(prp_path), url=config["source"]["prp_south_url"],
                              dataset_id="LRO-L-DLRE-5-PRP-V2.0", producer="LRO Diviner team, UCLA (Paige et al.)", licence="NASA PDS public data; no restrictions stated in the label or catalog (TODO: confirm citation wording with the PDS node)"),
                     dem=dict(file=dem_path.name, bytes=dem_path.stat().st_size, sha256=sha256(dem_path), note="NASA LOLA LDEM_75S_240M, read from the app cache")),
        rows=dict(prp_total=int(len(lat)), in_domain=int(keep.sum()), with_finite_features=int(usable.sum()), **counts),
        cold_trap_prevalence={name: float(cold_trap[split == code].mean()) if counts[name] else None for name, code in SPLITS.items()},
        registration_check=dict(lola_minus_prp_altitude_m=dict(median=float(np.median(offset)), p05=float(np.percentile(offset, 5)),
                                                                p95=float(np.percentile(offset, 95)), correlation=float(np.corrcoef(lola_elevation, kaguya)[0, 1]))),
        features=list(hazard.FEATURES), targets=list(hazard.TARGETS) + ["cold_trap (ice_depth == 0)"],
        split=config["split"], config=config["_path"],
    )
    out_path.with_suffix(".json").write_text(json.dumps(manifest, indent=1))
    return out_path, manifest
