"""Deterministic profile selection, figures and gates for simulated RF labels."""
import json
from pathlib import Path

import numpy as np
import rasterio

from . import paths
from .los import R, sample, trace_pairs
from .rf import C, FLOAT_BANDS, xyz
from .downloads import atomic_json


def make_report(target, out=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    target = Path(target)
    meta = json.loads(target.with_suffix('.json').read_text())
    out = Path(out) if out else paths.reports_dir()/'rf'
    profiles = out/'profiles'
    profiles.mkdir(parents=True, exist_ok=True)
    with rasterio.open(target) as src:
        arrays = dict(zip(src.descriptions, src.read()))
        tr, crs = src.transform, src.crs
        bounds = src.bounds
        window = rasterio.windows.Window(*meta['window'])
    valid = arrays['valid_mask'].astype(bool)
    los = arrays['los']
    margin, free = arrays['link_margin_db'], arrays['free_space_margin_db']
    covered = arrays['rf_covered'].astype(bool)
    dead = valid & (margin < meta['threshold_db'])
    terrain = valid & (los == 0)
    caused = arrays['terrain_caused_rf_outage'].astype(bool)
    master = rasterio.Affine(*meta['master_transform'])
    lattice = (tr.a == master.a == 20 and tr.e == master.e == -20
               and tr.b == tr.d == 0
               and abs((tr.c-master.c)/20-round((tr.c-master.c)/20)) < 1e-9
               and abs((tr.f-master.f)/20-round((tr.f-master.f)/20)) < 1e-9)
    gates = [dict(name='research valid pixels', passed=int(valid.sum()) == 503434, value=int(valid.sum())),
             dict(name='research LOS-clear pixels', passed=int((valid & (los == 1)).sum()) == 70612,
                  value=int((valid & (los == 1)).sum())),
             dict(name='link margin bounded by free space', passed=bool(np.all(margin[valid] <= free[valid]+1e-6)), value=float(np.max(margin[valid]-free[valid]))),
             dict(name='covered/dead disjoint', passed=not bool(np.any(covered & dead)), value=int((covered & dead).sum())),
             dict(name='terrain-caused outage subset', passed=not bool(np.any(caused & ~dead)), value=int((caused & ~dead).sum())),
             dict(name='invalid continuous values NaN', passed=all(np.isnan(arrays[k][~valid]).all() for k in FLOAT_BANDS), value=int((~valid).sum())),
             dict(name='GeoTIFF at most 60 MB', passed=target.stat().st_size <= 60000000, value=target.stat().st_size),
             dict(name='master lattice alignment', passed=bool(lattice), value=list(tr)[:6])]
    with rasterio.open(meta['dem_path']) as src:
        dem = src.read(1, masked=True).filled(np.nan)
        demtr = src.transform
    hillshade_path = paths.data_root()/'derived/terrain/haworth/hillshade_pgda90.tif'
    with rasterio.open(hillshade_path) as src:
        shade = src.read(1, window=window, masked=True)
    extent = np.array([bounds.left, bounds.right, bounds.bottom, bounds.top])/1000
    tx = np.array(meta['tx_xy_m'])
    config = meta['scenario']
    radio = meta['radio_profile']
    wavelength = C/(radio['frequency_ghz']*1e9)
    outputs = [str(target), str(target.with_suffix('.json')), meta['class_geotiff']]
    for kind in ('link_margin', 'classes'):
        fig, ax = plt.subplots(figsize=(9, 8), layout='constrained')
        ax.imshow(shade, extent=extent, cmap='gray')
        if kind == 'link_margin':
            image = ax.imshow(np.ma.masked_invalid(margin), extent=extent, cmap='viridis', alpha=.75)
            fig.colorbar(image, ax=ax, label='Link margin (dB)')
        else:
            # The masks overlap physically. Display RF failures last, and report all
            # independent areas separately; terrain category here means covered/blocked.
            classes = np.full(valid.shape, 3, dtype='uint8')
            classes[covered] = 0
            classes[terrain] = 1
            classes[dead] = 2
            colors = ['#2ab07f', '#e6ae36', '#d1495b', '#9a9da1']
            ax.imshow(classes, extent=extent, cmap=ListedColormap(colors), vmin=0, vmax=3, alpha=.65)
            ax.legend(handles=[Patch(color=c, label=t) for c, t in zip(colors,
                ['Covered / clear', 'Terrain blocked, RF covered', 'RF dead zone (takes priority)', 'Unknown'])], fontsize=8)
        ax.plot(*(tx/1000), '*', ms=12, color='yellow', markeredgecolor='black')
        ax.set(xlabel='Easting (km)', ylabel='Northing (km)', title=f'Haworth simulated {kind.replace("_", " ")}\nTX 5 m / RX 2 m; 2.4 GHz; 8 km range')
        destination = out/(kind+'.png')
        fig.savefig(destination, dpi=150)
        plt.close(fig)
        outputs.append(str(destination))

    clearance = arrays['min_clearance_m']
    # Choose clear highest-clearance, blocked deepest-clearance, boundary nearest zero.
    # Deterministic row-major argmin/argmax breaks ties; no random selection or tuning.
    selections = [('clear', valid & (los == 1), -clearance),
                  ('ridge_blocked', terrain, clearance),
                  ('near_boundary', valid & (np.abs(clearance) < 2), np.abs(clearance))]
    selected = []
    tr_values = np.asarray(tuple(demtr)[:6])
    ground = sample(dem, *tx, tr_values)
    a = xyz(*tx, ground+config['h_tx_m'])
    for name, mask, score in selections:
        if not mask.any():
            raise ValueError(f'No path available for {name}')
        row, col = np.unravel_index(np.argmin(np.where(mask, score, np.inf)), valid.shape)
        rx = np.array(tr*(col+.5, row+.5))
        z = sample(dem, *rx, tr_values)
        b = xyz(*rx, z+config['h_rx_m'])
        length = np.linalg.norm(b-a)
        f = np.linspace(0, 1, max(3, int(np.ceil(length/10))+1))
        points = a+(b-a)*f[:, None]
        radius = np.linalg.norm(points, axis=1)
        x, y = 2*R*points[:, 1]/(radius-points[:, 2]), 2*R*points[:, 0]/(radius-points[:, 2])
        terrain_z = np.array([sample(dem, px, py, tr_values) for px, py in zip(x, y)])
        distance = length*f
        chord_height = radius-R
        fresnel = np.sqrt(wavelength*distance*(length-distance)/length)
        diagnostic = trace_pairs(tx, [rx], config['h_tx_m'], config['h_rx_m'], dem, demtr,
                                 crs, radio['max_range_m'], 10, wavelength, mode='fast')
        obs = float(diagnostic['obstruction_distance_m'][0])
        knife = float(diagnostic['knife_edge_distance_m'][0])
        fig, ax = plt.subplots(figsize=(10, 4.5), layout='constrained')
        ax.plot(distance, terrain_z, color='#654d3a', label='Terrain')
        ax.plot(distance, chord_height, color='#2467a5', label='3D chord (radial elevation)')
        ax.fill_between(distance, chord_height-fresnel, chord_height+fresnel, color='#2467a5', alpha=.18, label='First Fresnel zone')
        ax.scatter([obs], [np.interp(obs, distance, terrain_z)], color='red', zorder=5, label='Minimum clearance obstruction')
        ax.axvline(knife, color='purple', ls=':', label='Maximum knife-edge nu')
        ax.set(xlabel='Distance along 3D chord (m)', ylabel='Radial elevation above sphere (m)',
               title=f'Simulated {name.replace("_", " ")}: min clearance {clearance[row, col]:.3f} m; margin {margin[row, col]:.2f} dB')
        ax.legend(fontsize=8)
        destination = profiles/(name+'.png')
        fig.savefig(destination, dpi=150)
        plt.close(fig)
        outputs.append(str(destination))
        selected.append(dict(kind=name, row=int(row), column=int(col), rx_xy_m=rx.tolist(),
                             min_clearance_m=float(clearance[row, col]), chord_length_m=float(length),
                             obstruction_distance_m=obs, max_nu_distance_m=knife))
    gates.append(dict(name='three profiles and two maps exist', passed=all(Path(p).is_file() for p in outputs[-5:]), value=outputs[-5:]))
    # Areas are projected pixel areas, matching the 20 m lattice (no invented geodesic correction).
    area = abs(tr.a*tr.e)/1e6
    counts = dict(valid=int(valid.sum()), los_clear=int((valid & (los == 1)).sum()),
                  rf_covered=int(covered.sum()), terrain_dead_zone=int(terrain.sum()),
                  rf_dead_zone=int(dead.sum()), terrain_caused_rf_outage=int(caused.sum()),
                  unknown=int((~valid).sum()))
    result = dict(label_origin='simulated', gates=gates, counts=counts,
                  areas_km2={k: v*area for k, v in counts.items()}, profiles=selected,
                  outputs=outputs, runtime_seconds=meta['runtime_seconds'], code_commit=meta['code_commit'])
    atomic_json(out/'report.json', result)
    lines = ['# Haworth RF labels (simulated)', '',
             f'Code commit `{meta["code_commit"]}`; seed {meta["seed"]}.',
             'Model: FSPL plus P.526 single knife edge; maximum nu from the complete 10 m fast LOS trace. No refraction or multi-edge propagation.',
             f'Labeling runtime (including cold JIT, DEM read, hash and GeoTIFF writing): {meta["runtime_seconds"]:.3f} s.',
             f'Complete GeoTIFF size: {target.stat().st_size:,} bytes; native uint8 companion: {Path(meta["class_geotiff"]).stat().st_size:,} bytes.',
             '', '| Layer | Pixels | Projected area (km²) |', '|---|---:|---:|']
    lines += [f'| {k} | {v:,} | {v*area:.6f} |' for k, v in counts.items()]
    lines += ['', 'Areas use 400 m² per master-grid pixel. Unknown includes the bounding-window corners outside the spherical 8 km disk; it is not a negative label.',
              'Independent terrain and RF masks can overlap. For the exclusive class visualization, RF dead zone takes priority, followed by terrain blocked but RF covered, covered/clear, and unknown.',
              '', meta['storage_note'],
              'Invalid continuous bands are NaN; invalid classes are 0, LOS is 255, and quality_flags follows the LOS tracer codes. valid_mask distinguishes unknown from negative classes.',
              'The sidecar preserves the profile, threshold, source DEM/hash, window transform, sampling and code commit. Full-trace diagnostics are labels only and must never enter model inputs.',
              'PGDA vertical reference remains null/TODO in grid.json; no new datum is assumed.',
              '', 'Path selection: highest minimum-clearance clear path, deepest blocked path, and smallest absolute clearance below 2 m; row-major ties. Fresnel envelope uses radius sqrt(lambda*d1*d2/D). The radial profile shows the actual straight XYZ chord, not a flat-Earth approximation.',
              '', '![Link margin](link_margin.png)', '![Classes](classes.png)']
    lines += [f'![{name}](profiles/{name}.png)' for name, _, _ in selections]
    (out/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return result
