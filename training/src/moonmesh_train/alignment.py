"""Auditable terrain alignment, physical power averaging and metre-based features.

No source files are modified. Unknown vertical datums stay unknown. Registration
is a diagnostic and never silently shifts a DEM to make the comparison pass.
"""

from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import shutil
import subprocess

import numpy as np
from pyproj import CRS, Transformer
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine, array_bounds
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window
from scipy.signal import fftconvolve
from skimage.registration import phase_cross_correlation
import yaml

from . import paths
from .downloads import atomic_json, sha256_file
from .inventory import same_projection, parse_pds


def dbm_to_mw(values):
    values = np.asarray(values, dtype=np.float64)
    return np.where(np.isfinite(values), np.power(10., values/10.), np.nan)


def mw_to_dbm(values):
    values = np.asarray(values, dtype=np.float64)
    result = np.full(values.shape, np.nan, dtype=np.float64)
    np.log10(values, out=result, where=np.isfinite(values) & (values > 0))
    return result*10.


def mean_power_dbm(values, axis=None):
    """Arithmetic mean of valid *linear power*, never arithmetic mean of dBm."""
    power = dbm_to_mw(values)
    count = np.sum(np.isfinite(power), axis=axis)
    total = np.nansum(power, axis=axis)
    mean = np.full(np.shape(total), np.nan, dtype=np.float64)
    np.divide(total, count, out=mean, where=count > 0)
    return mw_to_dbm(mean)


def disk_kernel(radius_m, pixel_size_m):
    """Unnormalized disk of pixel centers within a projected-metre radius.

    pixel_size_m is (x spacing, y spacing); anisotropic pixels are supported.
    """
    dx, dy = pixel_size_m
    if not all(math.isfinite(v) and v > 0 for v in (radius_m, dx, dy)):
        raise ValueError("Radius and pixel spacing must be positive finite metres")
    nx, ny = math.ceil(radius_m/dx), math.ceil(radius_m/dy)
    x, y = np.meshgrid(np.arange(-nx, nx+1)*dx, np.arange(-ny, ny+1)*dy)
    return (x*x+y*y <= radius_m**2+1e-9).astype(np.float64)


def neighborhood_moments(dem, radius_m, pixel_size_m, std=False):
    """Full-disk support required: no edge extrapolation or nodata filling."""
    dem = np.asarray(dem, dtype=np.float64)
    kernel = disk_kernel(radius_m, pixel_size_m)
    valid = np.isfinite(dem)
    counts = np.rint(fftconvolve(valid.astype(float), kernel, mode="same"))
    full = valid & (counts == kernel.sum())
    # Center values to improve variance stability for large elevation baselines.
    baseline = float(np.mean(dem[valid])) if valid.any() else 0.
    centered = np.where(valid, dem-baseline, 0.)
    mean = fftconvolve(centered, kernel, mode="same")/kernel.sum()
    if std:
        second = fftconvolve(centered**2, kernel, mode="same")/kernel.sum()
        result = np.sqrt(np.maximum(second-mean**2, 0.))
    else:
        result = mean+baseline
    return np.where(full, result, np.nan).astype(np.float32)


def slope_curvature(dem, pixel_size_m):
    """Central differences: slope degrees; planar Laplacian curvature in 1/m."""
    z = np.asarray(dem, dtype=np.float64)
    dx, dy = pixel_size_m
    gy, gx = np.gradient(z, dy, dx)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    curvature = np.full(z.shape, np.nan)
    curvature[1:-1, 1:-1] = ((z[1:-1, :-2]+z[1:-1, 2:]-2*z[1:-1, 1:-1])/dx**2
                            +(z[:-2, 1:-1]+z[2:, 1:-1]-2*z[1:-1, 1:-1])/dy**2)
    slope[~np.isfinite(z)] = np.nan
    slope[[0, -1], :] = np.nan
    slope[:, [0, -1]] = np.nan
    return slope.astype(np.float32), curvature.astype(np.float32)


def hillshade(dem, pixel_size_m, azimuth=315., altitude=45.):
    """Lambertian local surface, illumination azimuth clockwise from north."""
    dx, dy = pixel_size_m
    south_gradient, east_gradient = np.gradient(np.asarray(dem, dtype=np.float32), dy, dx)
    north_gradient = -south_gradient
    az, alt = np.radians([azimuth, altitude])
    light_east, light_north = np.cos(alt)*np.sin(az), np.cos(alt)*np.cos(az)
    shade = (-east_gradient*light_east-north_gradient*light_north+np.sin(alt))/np.sqrt(1+east_gradient**2+north_gradient**2)
    shade = np.clip(shade, 0, 1).astype(np.float32)
    shade[~np.isfinite(dem)] = np.nan
    return shade


def make_grid(lunar, pgda, pixel_m=20):
    """Preserve the LunarRF corner lattice; extend outward by whole pixels."""
    crs = CRS(lunar.crs)
    polar = bool(crs.coordinate_operation and "Polar Stereographic" in crs.coordinate_operation.method_name)
    north_up_20 = np.allclose([lunar.transform.a, lunar.transform.b, lunar.transform.d, lunar.transform.e],
                             [pixel_m, 0, 0, -pixel_m], atol=1e-9, rtol=0)
    if polar and north_up_20:
        chosen, reason = lunar, "LunarRF is polar stereographic at 20 m; preserve its exact pixel-corner lattice and extend to the largest PGDA90 buffered window."
        bounds = (list(pgda.bounds) if same_projection(lunar.crs, pgda.crs) else
                  transform_bounds(pgda.crs, lunar.crs, *pgda.bounds, densify_pts=41))
        a = lunar.transform
        left, bottom, right, top = bounds
        col0, row0 = math.floor((left-a.c)/a.a+1e-8), math.floor((top-a.f)/a.e+1e-8)
        col1, row1 = math.ceil((right-a.c)/a.a-1e-8), math.ceil((bottom-a.f)/a.e-1e-8)
        transform = Affine(a.a, 0, a.c+col0*a.a, 0, a.e, a.f+row0*a.e)
        width, height = col1-col0, row1-row0
    else:
        chosen, reason = pgda, "LunarRF is not a north-up 20 m polar stereographic grid; use the PGDA90 grid unchanged."
        transform, width, height = pgda.transform, pgda.width, pgda.height
    return {"crs_wkt": CRS(chosen.crs).to_wkt(), "transform": list(transform)[:6],
            "width": width, "height": height, "shape": [height, width],
            "bounds": list(array_bounds(height, width, transform)),
            "pixel_size_m": [abs(transform.a), abs(transform.e)], "reason": reason,
            "lattice_source": str(chosen.name), "extent_source": str(pgda.name),
            "pixel_registration": "area; affine origin is upper-left pixel corner; centers at +0.5,+0.5",
            "sphere_radius_m": CRS(chosen.crs).ellipsoid.semi_major_metre,
            "vertical_reference": None, "TODO": "PGDA vertical reference is not explicitly documented in supplied metadata"}


def profile(grid):
    return {"driver": "GTiff", "width": grid["width"], "height": grid["height"], "count": 1,
            "crs": grid["crs_wkt"], "transform": Affine(*grid["transform"]), "dtype": "float32",
            "nodata": np.nan, "compress": "deflate", "predictor": 3, "tiled": True,
            "blockxsize": 256, "blockysize": 256, "BIGTIFF": "IF_SAFER"}


def write_raster(path, array, grid, description, unit=None, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile(grid)) as dst:
        dst.write(np.asarray(array, dtype=np.float32), 1)
        dst.set_band_description(1, description)
        if unit:
            dst.set_band_unit(1, unit)
        dst.update_tags(**{k: str(v) for k, v in tags.items()})


def height_rule(src, source_kind, radius_m):
    """Return value=stored*scale+offset, with source-specific evidence."""
    scale, offset = src.scales[0], src.offsets[0]
    rule = {"source": str(src.name), "gdal_scale": scale, "gdal_offset": offset,
            "output_scale": scale, "output_offset": offset, "vertical_reference_radius_m": None,
            "vertical_reference_status": "NOT FOUND; TODO obtain explicit PGDA vertical reference",
            "rule": "Preserve source elevation values with GDAL scale/offset; no invented vertical datum correction"}
    if source_kind == "pds20":
        text = Path(src.name).read_text(encoding="utf-8")
        label = parse_pds(text)
        factor = label["fields"]["SCALING_FACTOR"]
        reference = label["fields"]["OFFSET"]
        if not factor or not reference or "HEIGHT = (DN * SCALING_FACTOR)" not in text:
            raise ValueError("PDS label does not explicitly establish the height formula")
        label_scale, label_offset = float(factor[0]["value"]), float(reference[0]["value"])
        if not (math.isclose(scale, label_scale) and math.isclose(offset, label_offset)):
            raise ValueError("PDS label scale/offset contradict GDAL")
        rule.update(output_offset=offset-radius_m, vertical_reference_radius_m=radius_m,
                    vertical_reference_status="PDS label explicitly identifies reference sphere",
                    rule=f"GDAL physical radius = DN*{scale}+{offset}; height above {radius_m} = DN*{scale}+{offset-radius_m}. Offset subtraction is algebraically combined before applying to pixels.",
                    label_fields=label["fields"], conversion_lines=label["conversion_lines"])
    else:
        description = " ".join(d or "" for d in src.descriptions).lower()
        if "planetary radius" in description:
            rule.update(output_offset=offset-radius_m, vertical_reference_radius_m=radius_m,
                        vertical_reference_status="Radius product identified by band description",
                        rule="Apply GDAL scale/offset then subtract the requested reference sphere radius")
        rule["evidence"] = {"band_description": src.descriptions[0], "tags": src.tags(),
                            "context": "PGDA elevation products identified by inventory; LunarRF DEM described as PGDA 20 m DEM (m)"}
    return rule


def warp_dem(source, grid, resampling, source_kind, radius_m):
    data = np.full(grid["shape"], np.nan, dtype=np.float32)
    with rasterio.open(source) as src:
        rule = height_rule(src, source_kind, radius_m)
        target = Affine(*grid["transform"])
        same = same_projection(src.crs, grid["crs_wkt"])
        ratio_x, ratio_y = target.a/src.transform.a, target.e/src.transform.e
        col, row = (src.transform.c-target.c)/target.a, (src.transform.f-target.f)/target.e
        if same and src.transform == target and list(src.shape) == grid["shape"]:
            # GDAL's nominal identity bilinear warp can perturb an exact zero.
            data = src.read(1, masked=True).astype(np.float32).filled(np.nan)
            rule["implementation"] = "exact identity copy (requested bilinear has no resampling to perform)"
        elif (resampling == Resampling.average and same and src.transform.b == src.transform.d == 0
              and ratio_x >= 1 and ratio_y >= 1
              and np.allclose([ratio_x, ratio_y, row, col], np.round([ratio_x, ratio_y, row, col]), atol=1e-7, rtol=0)
              and src.height % round(ratio_y) == 0 and src.width % round(ratio_x) == 0):
            # Exact aligned area average avoids GDAL edge slivers outside the source footprint.
            nx, ny, row, col = round(ratio_x), round(ratio_y), round(row), round(col)
            raw = src.read(1, masked=True).astype(np.float64).filled(np.nan)
            block = raw.reshape(src.height//ny, ny, src.width//nx, nx)
            count = np.isfinite(block).sum(axis=(1, 3))
            sums = np.nansum(block, axis=(1, 3))
            averaged = np.full(count.shape, np.nan)
            np.divide(sums, count, out=averaged, where=count > 0)
            r0, c0 = max(0, row), max(0, col)
            r1, c1 = min(grid["height"], row+averaged.shape[0]), min(grid["width"], col+averaged.shape[1])
            if r0 < r1 and c0 < c1:
                data[r0:r1, c0:c1] = averaged[r0-row:r1-row, c0-col:c1-col]
            rule["implementation"] = f"exact {ny}x{nx} source-pixel arithmetic mean on aligned grid; no extrapolation beyond footprint"
        else:
            reproject(rasterio.band(src, 1), data, src_transform=src.transform, src_crs=src.crs,
                      src_nodata=src.nodata, dst_transform=target, dst_crs=grid["crs_wkt"],
                      dst_nodata=np.nan, resampling=resampling, num_threads=2)
            rule["implementation"] = "GDAL reproject"
        data *= rule["output_scale"]
        data += rule["output_offset"]
        rule["resampling"] = resampling.name
        rule["horizontal_frame_note"] = ("Same projection geometry; no explicit source-to-target lunar frame transformation available"
                                         if same_projection(src.crs, grid["crs_wkt"]) else "GDAL CRS reprojection")
    return data, rule


def place_lunar(source, grid):
    """Copy exact lattice subsets with NaN padding; warp only a different lattice."""
    target = Affine(*grid["transform"])
    destination = np.full(grid["shape"], np.nan, dtype=np.float32)
    with rasterio.open(source) as src:
        a = src.transform
        col, row = (a.c-target.c)/target.a, (a.f-target.f)/target.e
        exact = (same_projection(src.crs, grid["crs_wkt"])
                 and np.allclose([a.a, a.b, a.d, a.e], [target.a, target.b, target.d, target.e], atol=1e-8, rtol=0)
                 and np.allclose([col, row], np.round([col, row]), atol=1e-7, rtol=0))
        values = src.read(1, masked=True).astype(np.float64).filled(np.nan)
        if exact:
            row, col = round(row), round(col)
            r0, c0, r1, c1 = max(0, row), max(0, col), min(grid["height"], row+src.height), min(grid["width"], col+src.width)
            if r0 < r1 and c0 < c1:
                destination[r0:r1, c0:c1] = values[r0-row:r1-row, c0-col:c1-col]
            return destination, "exact lattice copy with NaN padding; no resampling"
        power = "best_prx" in source.name or "best_norefl" in source.name
        if power:
            values = dbm_to_mw(values)
            method = Resampling.average if abs(target.a*target.e) > abs(a.a*a.e) else Resampling.bilinear
        else:
            method = Resampling.bilinear if source.name.endswith("__dem.tif") else Resampling.nearest
        reproject(values, destination, src_transform=a, src_crs=src.crs, src_nodata=np.nan,
                  dst_transform=target, dst_crs=grid["crs_wkt"], dst_nodata=np.nan, resampling=method)
        if power:
            destination = mw_to_dbm(destination).astype(np.float32)
        return destination, f"{method.name} in {'linear mW then converted to dBm' if power else 'source values'}"


def difference_stats(values):
    finite = np.asarray(values)[np.isfinite(values)].astype(np.float64)
    if not finite.size:
        return {"count": 0, "median": None, "mad": None, "p5": None, "p95": None}
    median = np.median(finite)
    return {"count": int(finite.size), "median": float(median), "mad": float(np.median(np.abs(finite-median))),
            "p5": float(np.percentile(finite, 5)), "p95": float(np.percentile(finite, 95)),
            "min": float(finite.min()), "max": float(finite.max()), "mean": float(finite.mean()),
            "rmse": float(np.sqrt(np.mean(finite**2)))}


def registration(reference_shade, moving_shade, pixel_size_m, upsample_factor=10, limit_px=.5):
    """Common complete rectangle; no masks, since skimage masks disable upsampling."""
    valid = np.isfinite(reference_shade) & np.isfinite(moving_shade)
    rr, cc = np.where(valid)
    if not rr.size:
        raise ValueError("Registration has no joint valid terrain")
    r0, r1, c0, c1 = int(rr.min())+1, int(rr.max()), int(cc.min())+1, int(cc.max())
    a, b = reference_shade[r0:r1, c0:c1], moving_shade[r0:r1, c0:c1]
    if min(a.shape) < 16 or not (np.isfinite(a).all() and np.isfinite(b).all()):
        raise ValueError("Registration requires a complete joint-valid rectangle; refusing masked mode that loses subpixel precision")
    if np.std(a) < 1e-8 or np.std(b) < 1e-8:
        raise ValueError("Registration is indeterminate on featureless hillshade")
    taper = (np.hanning(a.shape[0])[:, None]*np.hanning(a.shape[1])[None, :]).astype(np.float32)
    aa, bb = (a-np.mean(a))*taper, (b-np.mean(b))*taper
    shift, error, phase = phase_cross_correlation(aa, bb, upsample_factor=upsample_factor, normalization="phase")
    correlation = float(np.sum(aa.astype(np.float64)*bb)/np.sqrt(np.sum(aa.astype(np.float64)**2)*np.sum(bb.astype(np.float64)**2)))
    dy, dx = map(float, shift)
    norm = math.hypot(dx, dy)
    return {"shift_to_apply_row_col_px": [dy, dx], "shift_to_apply_easting_northing_m": [dx*pixel_size_m[0], -dy*pixel_size_m[1]],
            "shift_norm_px": norm, "shift_norm_m": math.hypot(dx*pixel_size_m[0], dy*pixel_size_m[1]),
            "status": "PASS" if norm < limit_px else "FAIL", "failure_threshold_px": limit_px,
            "threshold_rule": "Euclidean shift magnitude >= threshold fails (also catches any axis >= threshold)",
            "upsample_factor": upsample_factor, "normalization": "phase", "error": float(error), "phase_difference": float(phase),
            "unshifted_tapered_hillshade_correlation": correlation,
            "error_note": "Skimage phase-normalized error can be near 1 even for identical rasters; it is not a confidence score",
            "window_row_col_height_width": [r0, c0, r1-r0, c1-c0],
            "method": "same-illumination hillshade; joint finite rectangle cropped by one pixel, mean removal and Hann taper; unmasked subpixel phase_cross_correlation; no shift applied"}


def derive_features(dem_path, destination, grid, config, commit):
    destination.mkdir(parents=True, exist_ok=True)
    dx, dy = grid["pixel_size_m"]
    radii = config["relative_elevation_radii_m"]
    roughness = config["roughness_radius_m"]
    roughness_name = f"roughness_{roughness}m"
    specs = {"slope_deg": "degree", "curvature_per_m": "1/m", roughness_name: "m", "valid_mask": "1"}
    specs.update({f"relative_elevation_{r}m": "m" for r in radii})
    halo = max(1, math.ceil(max(*radii, roughness)/min(dx, dy)))
    tile = config["processing_tile_pixels"]
    with ExitStack() as stack:
        src = stack.enter_context(rasterio.open(dem_path))
        outputs = {name: stack.enter_context(rasterio.open(destination/f"{name}.tif", "w", **profile(grid))) for name in specs}
        for name, dst in outputs.items():
            dst.set_band_description(1, name)
            dst.set_band_unit(1, specs[name])
            dst.update_tags(source=str(dem_path), git_commit=commit, support="full finite disk required; no extrapolation",
                            neighborhood_distance="projected metres", curvature_definition="five-point planar Laplacian", label_origin="not_simulated_terrain")
        for row in range(0, src.height, tile):
            print(f"Derived features rows {row}/{src.height}", flush=True)
            for col in range(0, src.width, tile):
                h, w = min(tile, src.height-row), min(tile, src.width-col)
                z = src.read(1, window=Window(col-halo, row-halo, w+2*halo, h+2*halo), boundless=True, fill_value=np.nan)
                slope, curvature = slope_curvature(z, (dx, dy))
                layers = {"slope_deg": slope, "curvature_per_m": curvature,
                          roughness_name: neighborhood_moments(z, roughness, (dx, dy), std=True),
                          "valid_mask": np.isfinite(z).astype(np.float32)}
                for r in radii:
                    layers[f"relative_elevation_{r}m"] = z-neighborhood_moments(z, r, (dx, dy))
                for name, values in layers.items():
                    outputs[name].write(values[halo:halo+h, halo:halo+w], 1, window=Window(col, row, w, h))
    return {name: (destination/f"{name}.tif").as_posix() for name in specs}


def quicklooks(out, grid, shades, differences, power, site):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    crs = CRS(grid["crs_wkt"])
    x, y = Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True).transform(site["longitude_deg"], site["latitude_deg"])
    left, bottom, right, top = grid["bounds"]
    extent = [left/1000, right/1000, bottom/1000, top/1000]
    # Plot display samples only; all numeric diagnostics use full-resolution data.
    stride = max(1, math.ceil(max(grid["shape"])/1600))
    files = []

    def decorate(ax):
        ax.plot(x/1000, y/1000, marker="+", color="red", markersize=10, markeredgewidth=1.5)
        ax.set_xlabel("Easting (km)")
        ax.set_ylabel("Northing (km)")
        ax.set_facecolor("#dddddd")

    for name, shade in shades.items():
        fig, ax = plt.subplots(figsize=(8, 8), layout="constrained")
        ax.imshow(shade[::stride, ::stride], extent=extent, cmap="gray", vmin=0, vmax=1, interpolation="nearest")
        ax.set_title(f"Haworth — {name} hillshade\n20 m master grid; red + = research point")
        decorate(ax)
        p = out/f"hillshade_{name}.png"
        fig.savefig(p, dpi=150)
        plt.close(fig)
        files.append(p.as_posix())
    for name, diff in differences.items():
        limit = float(np.nanpercentile(np.abs(diff), 98)) or 1.
        fig, ax = plt.subplots(figsize=(8, 8), layout="constrained")
        im = ax.imshow(diff[::stride, ::stride], extent=extent, cmap="RdBu_r", vmin=-limit, vmax=limit, interpolation="nearest")
        ax.set_title(f"Haworth — {name}\nSource elevations; vertical datum unresolved")
        decorate(ax)
        fig.colorbar(im, ax=ax, label="Elevation difference (m); colors clipped at |difference| p98", shrink=.8)
        p = out/f"difference_{name}.png"
        fig.savefig(p, dpi=150)
        plt.close(fig)
        files.append(p.as_posix())
    fig, ax = plt.subplots(figsize=(8, 8), layout="constrained")
    ax.imshow(shades["pgda90"][::stride, ::stride], extent=extent, cmap="gray", vmin=0, vmax=1, interpolation="nearest")
    finite = power[np.isfinite(power)]
    lo, hi = np.percentile(finite, [2, 98])
    im = ax.imshow(power[::stride, ::stride], extent=extent, cmap="viridis", vmin=lo, vmax=hi, alpha=.72, interpolation="nearest")
    ax.set_title("Haworth — LunarRF 1.8 GHz best power (simulated)\nOver PGDA 90 hillshade; no RF resampling on matching lattice")
    decorate(ax)
    fig.colorbar(im, ax=ax, label="Received power (dBm); colors clipped at p2/p98", shrink=.8)
    p = out/"lunarrf_best_prx_over_hillshade.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    files.append(p.as_posix())
    return files


def choose_sources(inventory):
    local = [r for r in inventory["rasters"] if r["location"] != "archive"]
    pgda = sorted([r for r in local if "/interim/pgda90/" in r["path"]], key=lambda r: math.prod(r["shape"]))
    if not pgda:
        raise ValueError("Inventory contains no PGDA90 window")
    pds = [r for r in local if r["driver"] == "PDS" and np.allclose(r["pixel_size"], [20, 20])]
    pgda78 = [r for r in local if "/pgda78/haworth/" in r["path"] and r["pixel_size"] == [5., 5.]
              and ("surf" in Path(r["path"]).stem.lower() or "dem" in Path(r["path"]).stem.lower())]
    lunar = [r for r in local if "/lunarrf/Haworth/" in r["path"]]
    if len(pds) != 1 or len(pgda78) != 1:
        raise ValueError("Need one registered PDS20 and one Haworth PGDA78 DEM; inspect inventory")
    return {"pgda90": Path(pgda[-1]["path"]), "pgda90_windows": pgda,
            "pds20": Path(pds[0]["path"]), "pgda78": Path(pgda78[0]["path"]),
            "lunar": [Path(r["path"]) for r in lunar],
            "lunar_dem": next(Path(r["path"]) for r in lunar if r["path"].endswith("Haworth__dem.tif")),
            "lunar_power": next(Path(r["path"]) for r in lunar if r["path"].endswith("Haworth__best_prx_1p800GHz.tif"))}


def align_quality_layers(inventory, sources, grid, terrain, commit):
    """Recognize only metadata-named quality semantics; report unknowns explicitly."""
    dem_paths = {str(sources[k]).replace("\\", "/") for k in ("pgda90", "pgda78")}
    dem_paths.update(r["path"] for r in sources["pgda90_windows"])
    outputs, unknown = [], []
    for r in inventory["rasters"]:
        if r["location"] == "archive" or r["path"] in dem_paths or not any(f"/{s}/" in r["path"] for s in ("pgda90", "pgda78")):
            continue
        description = (Path(r["path"]).stem+" "+" ".join(b.get("description") or "" for b in r["bands"])).lower()
        if any(word in description for word in ("count", "shots", "observations")):
            method = Resampling.nearest
        elif any(word in description for word in ("uncertainty", "error", "effective_resolution", "resolution", "slope")):
            method = Resampling.bilinear
        else:
            unknown.append({"path": r["path"], "semantics": None, "TODO": "Unrecognized PGDA raster; inspect metadata before choosing resampling"})
            continue
        source = Path(r["path"])
        target = terrain/"quality"/source.name
        values = np.full(grid["shape"], np.nan, dtype=np.float32)
        with rasterio.open(source) as src:
            reproject(rasterio.band(src, 1), values, src_transform=src.transform, src_crs=src.crs,
                      src_nodata=src.nodata, dst_transform=Affine(*grid["transform"]), dst_crs=grid["crs_wkt"],
                      dst_nodata=np.nan, resampling=method)
            values = values*src.scales[0]+src.offsets[0]
            write_raster(target, values, grid, src.descriptions[0] or source.stem, src.units[0],
                         source=source, git_commit=commit, resampling=method.name)
        outputs.append({"path": target.as_posix(), "source": source.as_posix(), "source_sha256": sha256_file(source),
                        "resampling": method.name, "evidence": description})
    return {"status": "aligned" if outputs else "NOT FOUND in local inventory", "paths": outputs,
            "unclassified": unknown, "TODO": None if outputs else "No identified PGDA observation count, uncertainty or effective-resolution raster is available; none fabricated"}


def write_summary(report, directory):
    atomic_json(directory/"report.json", report)
    lines = ["# Haworth alignment", "", f"Status: **{report['status']}**. Commit `{report['git_commit']}`; seed {report['seed']}.",
             "LunarRF RF/LOS labels are simulated. Terrain and diagnostics are not measured RF validation.", "",
             report["grid"]["reason"], f"Grid: {report['grid']['shape']} rows × columns, {report['grid']['pixel_size_m']} m pixels; bounds {report['grid']['bounds']}.",
             "The largest PGDA90 window (25 km buffer in this inventory) is selected; both available window paths and extents are retained in grid.json.", "",
             "## Height and primary DEM choices", ""]
    for name, rule in report["height_rules"].items():
        lines += [f"- {name}: {rule['rule']} Source: `{rule['source']}`. Vertical reference: {rule['vertical_reference_status']}. Processing: {rule['implementation']}."]
    lines += ["", report["primary_dems"]["reason"],
              "PGDA's explicit vertical reference remains NOT FOUND. No 1,737,400 m offset was guessed from its horizontal CRS. Cross-source differences are provisional elevation comparisons, not proof of vertical/frame equivalence. PDS uses a documented lunar frame; no unverified frame transform was applied.",
              "", "## Differences (PGDA90 minus comparison; metres)", "", "| Comparison | Valid pixels | Median | MAD | p5 | p95 |", "|---|---:|---:|---:|---:|---:|"]
    for name, s in report["differences"].items():
        lines.append(f"| {name} | {s['count']} | {s['median']:.6f} | {s['mad']:.6f} | {s['p5']:.6f} | {s['p95']:.6f} |")
    lines += ["", "## Registration", "", "Shifts are corrections that would align each moving hillshade to PGDA90; no correction was applied. Negative row shift is northward. Full-resolution common valid terrain is used with upsample_factor=10. Euclidean shift magnitude ≥0.5 pixels fails; errors/indeterminate registration also fail.",
              "", "| DEM | Row, column shift (px) | Easting, northing shift (m) | Magnitude (px) | Result |", "|---|---|---|---:|---|"]
    for name, r in report["registration"].items():
        lines.append(f"| {name} | {r.get('shift_to_apply_row_col_px')} | {r.get('shift_to_apply_easting_northing_m')} | {r.get('shift_norm_px')} | {r['status']} |")
    lines += ["", "## Features and masks", "",
              "Relative elevation = center elevation minus full-disk mean at 200/1000 m radius. Roughness = full-disk population standard deviation at 100 m radius. Distances are projected metres. Incomplete disks at nodata/edges yield NaN; no reflection padding or extrapolation. Slope is central-difference gradient angle in degrees; curvature is the five-point planar Laplacian (1/m), with 20 m neighbor spacing. Valid mask is 1 for finite DEM and 0 otherwise; each feature also has its own NaN support mask.",
              "All aligned rasters use float32 and NaN nodata. The native PGDA78 copy retains its original 5 m grid. LunarRF layers on the master lattice are copied/padded, not interpolated. Declared zero nodata in counts remains NaN; no count is used as a terrain feature. If RF reprojection is needed, interpolation/averaging operates in mW.",
              "", f"PGDA quality layers: {report['quality_layers']['status']}. {report['quality_layers'].get('TODO', '')}",
              "", "## Quick looks", ""]
    for p in report["quicklooks"]:
        lines.append(f"- [{Path(p).name}]({Path(p).name})")
    if report["status"] != "PASS":
        lines += ["", "**FAILED REGISTRATION — do not treat these products as registration-approved inputs.**", report.get("error", "")]
    lines += ["", "TODO: visually check a rim high point, small crater and ridge crest in QGIS before labeling. The PNG review and phase correlation do not establish the undocumented vertical datum."]
    (directory/"report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def run_alignment(config_path, site_name="haworth"):
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    site = yaml.safe_load((config_path.parent/config["site_config"]).read_text(encoding="utf-8"))
    if site["site"] != site_name:
        raise ValueError("Site config does not match requested site")
    random.seed(config["seed"])
    root = paths.data_root()
    terrain, reports = paths.derived_dir("terrain")/site_name, paths.reports_dir()/"align"
    terrain.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    inventory = json.loads((paths.reports_dir()/"inventory.json").read_text(encoding="utf-8"))
    sources = choose_sources(inventory)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.repo_root(), text=True, capture_output=True, timeout=10).stdout.strip() or None
    print(f"Git commit: {commit}; seed: {config['seed']}; data root: {root}; LunarRF simulated", flush=True)
    with rasterio.Env(GDAL_PAM_ENABLED="NO"), rasterio.open(sources["lunar_power"]) as lunar, rasterio.open(sources["pgda90"]) as pgda:
        grid = make_grid(lunar, pgda, config["master_pixel_m"])
    grid.update(git_commit=commit, seed=config["seed"], available_pgda90_windows=[{"path": r["path"], "bounds": r["bounds"], "shape": r["shape"]} for r in sources["pgda90_windows"]])
    atomic_json(terrain/"grid.json", grid)
    report = {"status": "RUNNING", "git_commit": commit, "seed": config["seed"], "started_utc": datetime.now(timezone.utc).isoformat(),
              "data_root": root.as_posix(), "config": config, "config_sha256": sha256_file(config_path), "grid": grid,
              "inventory_sha256": sha256_file(paths.reports_dir()/"inventory.json"),
              "findings_sha256": sha256_file(paths.reports_dir()/"lunarrf_findings.md"),
              "height_rules": {}, "sources": {}, "outputs": {}, "differences": {}, "registration": {}, "quicklooks": [],
              "quality_layers": {"status": "NOT FOUND in local inventory", "paths": [], "TODO": "No PGDA observation count, uncertainty or effective-resolution raster is currently available; not downloaded or fabricated"}}
    arrays, shades = {}, {}
    with rasterio.Env(GDAL_PAM_ENABLED="NO"):
        # Mark the run incomplete before writes, so a failed rerun cannot leave a stale PASS.
        atomic_json(reports/"report.json", report)
        (reports/"report.md").write_text("# Haworth alignment\n\nRUNNING / NOT APPROVED: see align_run.log for progress.\n", encoding="utf-8")
        atomic_json(terrain/"primary_dems.json", {"registration_status": "RUNNING", "ready": False})
        report["quality_layers"] = align_quality_layers(inventory, sources, grid, terrain, commit)
        for key, resampling in [("pgda90", Resampling.bilinear), ("pds20", Resampling.bilinear), ("pgda78", Resampling.average)]:
            print(f"Aligning {key}: {sources[key]}", flush=True)
            values, rule = warp_dem(sources[key], grid, resampling, key, config["reference_radius_m"])
            arrays[key] = values
            output = terrain/f"{key}_dem_20m.tif"
            write_raster(output, values, grid, f"{key} elevation on master grid", "m", source=sources[key],
                         git_commit=commit, height_rule=rule["rule"], vertical_reference=rule["vertical_reference_status"])
            report["height_rules"][key] = rule
            report["sources"][key] = {"path": sources[key].as_posix(), "sha256": sha256_file(sources[key])}
            acquisition = inventory.get("acquisition_manifest", {}).get("entries", [])
            report["sources"][key]["source_id"] = next((e["source_id"] for e in acquisition if Path(e["local_path"]) == sources[key]), None)
            if key == "pds20":
                with rasterio.open(sources[key]) as pds:
                    report["sources"][key]["dataset_files"] = [{"path": f, "sha256": sha256_file(Path(f))} for f in pds.files]
            report["outputs"][key] = output.as_posix()
        native = terrain/"pgda78_native_5m.tif"
        shutil.copyfile(sources["pgda78"], native)
        report["outputs"]["pgda78_native"] = native.as_posix()
        power = None
        for source in sources["lunar"]:
            print(f"Placing LunarRF {source.name}", flush=True)
            values, method = place_lunar(source, grid)
            output = terrain/"lunarrf"/source.name
            is_dem = source == sources["lunar_dem"]
            write_raster(output, values, grid, source.stem, "m" if is_dem else None,
                         source=source, git_commit=commit, method=method,
                         label_origin="terrain_context" if "context" in source.parts else "simulated",
                         allowed_as_model_input="true" if is_dem else "false")
            report["outputs"][source.stem] = {"path": output.as_posix(), "method": method, "source_sha256": sha256_file(source)}
            if is_dem:
                arrays["lunarrf_dem"] = values
            if source == sources["lunar_power"]:
                power = values
        differences = {"pgda90_minus_pds20": arrays["pgda90"]-arrays["pds20"],
                       "pgda90_minus_pgda78": arrays["pgda90"]-arrays["pgda78"]}
        for name, diff in differences.items():
            p = terrain/f"{name}.tif"
            write_raster(p, diff, grid, name, "m", git_commit=commit, vertical_reference="unresolved; provisional source-elevation comparison")
            report["differences"][name] = {**difference_stats(diff), "path": p.as_posix()}
        identical = difference_stats(arrays["pgda90"]-arrays["lunarrf_dem"])
        same = (identical["count"] == int(np.isfinite(arrays["lunarrf_dem"]).sum()) > 0
                and identical["min"] == identical["max"] == 0)
        report["primary_dems"] = {"experiment_b": report["outputs"]["pgda90"],
                                  "experiment_a": report["outputs"]["pgda90"] if same else report["outputs"][sources["lunar_dem"].stem]["path"],
                                  "lunarrf_minus_pgda90_stats": identical,
                                  "reason": ("Both experiments use PGDA90: LunarRF manifest identifies LDEM_80S_20MPP_ADJ and its Haworth DEM is exactly equal on all joint pixels."
                                             if same else "Experiment B uses PGDA90; Experiment A retains LunarRF's supplied DEM because the joint pixels differ.")}
        for name, dem in arrays.items():
            print(f"Hillshade and registration: {name}", flush=True)
            shades[name] = hillshade(dem, grid["pixel_size_m"], config["hillshade_azimuth_deg"], config["hillshade_altitude_deg"])
            write_raster(terrain/f"hillshade_{name}.tif", shades[name], grid, f"{name} hillshade", "1", git_commit=commit)
            try:
                report["registration"][name] = registration(shades["pgda90"], shades[name], grid["pixel_size_m"],
                                                           config["registration_upsample_factor"], config["registration_fail_shift_px"])
            except ValueError as exc:
                report["registration"][name] = {"status": "FAIL", "error": str(exc)}
        print("Writing quick looks", flush=True)
        report["quicklooks"] = quicklooks(reports, grid, shades, differences, power, site)
        # Release full-size diagnostic arrays before neighborhood processing.
        del arrays, shades, differences, power, values, diff, dem
        print("Deriving primary terrain features", flush=True)
        features = {}
        for experiment in ("experiment_b", "experiment_a"):
            dem_path = report["primary_dems"][experiment]
            if dem_path not in features:
                folder = terrain/("features_pgda90" if experiment == "experiment_b" else "features_lunarrf")
                features[dem_path] = derive_features(Path(dem_path), folder, grid, config, commit)
            report["primary_dems"][experiment+"_features"] = features[dem_path]
    failures = [name for name, result in report["registration"].items() if result["status"] != "PASS"]
    report["status"] = "FAIL" if failures else "PASS"
    report["ready_for_registration_checked_use"] = not failures
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    atomic_json(terrain/"primary_dems.json", {**report["primary_dems"], "registration_status": report["status"]})
    write_summary(report, reports)
    if failures:
        print(f"ERROR: REGISTRATION FAILED for {failures}; shift >= 0.5 px or indeterminate. See {reports/'report.md'}. No automatic correction applied.", flush=True)
        return 2
    print(f"Alignment PASS. Grid: {terrain/'grid.json'}; report: {reports/'report.md'}", flush=True)
    return 0
