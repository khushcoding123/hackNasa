"""Resumable simulated Haworth atlas comparison, without per-mast rasters."""
import csv
import json
import math
import subprocess
from pathlib import Path
from time import perf_counter

import numpy as np
import rasterio
from rasterio.windows import from_bounds

from . import paths
from .config import load_experiment, configs_dir
from .downloads import atomic_json, sha256_file
from .los import R, sample, trace_pairs
from .rf import C, fspl_db, knife_edge_loss_db, received_power_dbm, xyz

NAMES = ('best_prx', 'best_id', 'best_los', 'los_count', 'count', 'valid_count')


def select_masts(catalog, bounds, maximum):
    """Projected distance to the closed full raster footprint, including corners."""
    left, bottom, right, top = bounds
    with Path(catalog).open(newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    selected = []
    for row in rows:
        x, y = float(row['x_m']), float(row['y_m'])
        if math.hypot(max(left-x, 0, x-right), max(bottom-y, 0, y-top)) <= maximum:
            selected.append(dict(cand_id=int(row['cand_id']), x_m=x, y_m=y, z_m=float(row['z_m'])))
    selected.sort(key=lambda r: r['cand_id'])
    if len({r['cand_id'] for r in selected}) != len(selected):
        raise ValueError('Duplicate candidate IDs')
    return selected


def empty_aggregates(shape):
    return dict(best_prx=np.full(shape, -np.inf, np.float64),
                best_id=np.full(shape, -1, np.int32), best_los=np.full(shape, 255, np.uint8),
                los_count=np.zeros(shape, np.uint16), count=np.zeros(shape, np.uint16),
                valid_count=np.zeros(shape, np.uint16))


def update(agg, index, prx, los, valid, cand_id, threshold):
    valid = valid & np.isfinite(prx)
    old = agg['best_prx'].ravel()[index]
    ids = agg['best_id'].ravel()[index]
    win = valid & ((prx > old) | ((prx == old) & ((ids < 0) | (cand_id < ids))))
    idx = index[win]
    agg['best_prx'].ravel()[idx] = prx[win]
    agg['best_id'].ravel()[idx] = cand_id
    agg['best_los'].ravel()[idx] = los[win]
    agg['los_count'].ravel()[index] += (valid & (los == 1)).astype(np.uint16)
    agg['count'].ravel()[index] += (valid & (prx >= threshold)).astype(np.uint16)
    agg['valid_count'].ravel()[index] += valid.astype(np.uint16)


def save_checkpoint(path, agg, metadata):
    temporary = path.with_suffix('.tmp')
    with temporary.open('wb') as stream:
        np.savez(stream, **agg, metadata=np.array(json.dumps(metadata, allow_nan=False)))
    temporary.replace(path)


def load_checkpoint(path, identity):
    with np.load(path, allow_pickle=False) as saved:
        meta = json.loads(str(saved['metadata']))
        if meta['identity'] != identity:
            raise ValueError('Checkpoint provenance changed; refusing to mix runs')
        agg = {name: saved[name].copy() for name in NAMES}
    return agg, meta


def confusion(ours, reference, valid, area):
    tp = int(np.count_nonzero(valid & ours & reference))
    tn = int(np.count_nonzero(valid & ~ours & ~reference))
    fp = int(np.count_nonzero(valid & ours & ~reference))
    fn = int(np.count_nonzero(valid & ~ours & reference))
    return dict(tp=tp, tn=tn, fp=fp, fn=fn, valid_pixels=tp+tn+fp+fn,
                agreement=(tp+tn)/(tp+tn+fp+fn) if tp+tn+fp+fn else None,
                iou=tp/(tp+fp+fn) if tp+fp+fn else None,
                false_covered_km2=fp*area, missed_covered_km2=fn*area)


def read_layer(directory, name, window=None, counts=False):
    with rasterio.open(directory/f'Haworth__{name}.tif') as src:
        if counts:
            # Alignment in step 04 masked declared nodata=0, losing valid zero
            # counts. Read the untouched published source when its provenance
            # tag exists, with an exact lattice crop (never infer NaN means 0).
            source = src.tags().get('source')
            if source:
                with rasterio.open(Path(source)) as original:
                    bounds = rasterio.windows.bounds(window, src.transform) if window else src.bounds
                    crop = from_bounds(*bounds, transform=original.transform)
                    if not np.allclose([crop.col_off, crop.row_off, crop.width, crop.height],
                                       np.round([crop.col_off, crop.row_off, crop.width, crop.height]), atol=1e-8):
                        raise ValueError('Published count crop is not on an integer lattice')
                    crop = crop.round_offsets().round_lengths()
                    expected = src.window_transform(window) if window else src.transform
                    if original.crs != src.crs or not original.window_transform(crop).almost_equals(expected):
                        raise ValueError('Published count lattice mismatch')
                    values = original.read(1, window=crop)
                    expected_shape = (int(window.height), int(window.width)) if window else src.shape
                    if values.shape != expected_shape:
                        raise ValueError('Published count crop does not cover the comparison window')
            else:
                values = src.read(1, window=window)
            valid = np.isfinite(values)
        else:
            band = src.read(1, window=window, masked=True)
            values, valid = band.data, ~np.ma.getmaskarray(band) & np.isfinite(band.data)
    return values, valid


def write_rasters(directory, agg, transform, crs, commit):
    for name in NAMES:
        values = agg[name].copy()
        nodata = None
        if name == 'best_prx':
            values = np.where(np.isfinite(values), values, np.nan).astype('float32')
            nodata = np.nan
        elif name == 'best_id':
            nodata = -1
        elif name == 'best_los':
            nodata = 255
        with rasterio.open(directory/f'{name}.tif', 'w', driver='GTiff', count=1,
                           height=values.shape[0], width=values.shape[1], dtype=values.dtype,
                           transform=transform, crs=crs, nodata=nodata,
                           compress='deflate', tiled=True) as dst:
            dst.write(values, 1)
            dst.set_band_description(1, name+' (simulated)')
            dst.update_tags(label_origin='simulated', git_commit=commit)
            dst.set_band_unit(1, 'dBm' if name == 'best_prx' else '1')


def make_report(agg, meta, cfg, directory, transform, crs):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    root = paths.data_root()
    reference = root/cfg['reference_dir']
    shape = agg['best_prx'].shape
    # Published layers were extended onto the master lattice. Crop to their
    # original valid Haworth footprint; never treat the padded exterior as zero.
    with rasterio.open(reference/'Haworth__dem.tif') as src:
        window = from_bounds(*meta['identity']['bounds'], transform=src.transform)
    window = window.round_offsets().round_lengths()
    valid = np.isfinite(agg['best_prx'])
    area = abs(transform.a*transform.e)/1e6
    results = dict(label_origin='simulated', code_commit=meta['code_commit'],
                   processed_masts=len(meta['finished']), selected_masts=len(meta['masts']),
                   grid_pixel_area_km2=area, coverage_threshold_dbm=cfg['comparison_threshold_dbm'],
                   unknown_pixels=int((~valid).sum()),
                   unknown_metadata=dict(vertical_reference=None, lunarrf_exact_los_criterion=None,
                                         lunarrf_tie_rule=None),
                   metadata_todo='TODO: obtain upstream vertical reference, exact LOS criterion and tie rule; see lunarrf_findings.md.')
    ref_los, ref_valid = read_layer(reference, 'los_count', window, counts=True)
    _, terrain_valid = read_layer(reference, 'dem', window)
    joint = valid & terrain_valid & ref_valid
    results['any_los'] = confusion(agg['los_count'] > 0, ref_los > 0, joint, area)
    with rasterio.open(root/'derived/terrain/haworth/hillshade_pgda90.tif') as src:
        shade_window = from_bounds(*meta['identity']['bounds'], transform=src.transform).round_offsets().round_lengths()
        shade = src.read(1, window=shade_window)
    extent = [meta['identity']['bounds'][0]/1000, meta['identity']['bounds'][2]/1000,
              meta['identity']['bounds'][1]/1000, meta['identity']['bounds'][3]/1000]
    def plot_map(values, filename, title, cmap, **kwargs):
        fig, ax = plt.subplots(figsize=(9, 8))
        ax.imshow(shade, cmap='gray', extent=extent)
        im = ax.imshow(values, cmap=cmap, extent=extent, **kwargs)
        fig.colorbar(im, ax=ax)
        ax.set(xlabel='Projected x (km)', ylabel='Projected y (km)', title=title+' (both simulated)')
        fig.tight_layout(); fig.savefig(directory/filename, dpi=160); plt.close(fig)
    for variant, suffix, power in [('norefl', '_norefl', 'best_norefl'), ('reflection', '', 'best_prx')]:
        published, pvalid = read_layer(reference, power+'_1p800GHz', window)
        ids, ivalid = read_layer(reference, 'best_id'+suffix+'_1p800GHz', window)
        los, lvalid = read_layer(reference, 'best_los'+suffix+'_1p800GHz', window)
        counts, cvalid = read_layer(reference, 'count'+suffix+'_1p800GHz', window, counts=True)
        mask = joint & pvalid & ivalid & lvalid & cvalid
        diff = agg['best_prx']-published
        d = diff[mask]
        median = float(np.median(d))
        hist, edges = np.histogram(d, bins=100)
        result = dict(valid_pixels=int(mask.sum()), excluded_pixels=int(mask.size-mask.sum()),
                      best_id_agreement=float(np.mean(agg['best_id'][mask] == ids[mask])),
                      best_los=confusion(agg['best_los'] == 1, los == 1, mask, area),
                      db_difference=dict(median=median, mad=float(np.median(np.abs(d-median))),
                                         p5=float(np.percentile(d, 5)), p95=float(np.percentile(d, 95)),
                                         histogram_counts=hist.tolist(), histogram_edges_db=edges.tolist()),
                      coverage=confusion(agg['best_prx'] >= cfg['comparison_threshold_dbm'],
                                         published >= cfg['comparison_threshold_dbm'], mask, area),
                      count_coverage=confusion(agg['count'] > 0, counts > 0, mask, area),
                      count_difference_mean=float(np.mean(agg['count'][mask].astype(float)-counts[mask])))
        results[variant] = result
        fig, ax = plt.subplots(figsize=(9, 5)); ax.stairs(hist, edges)
        ax.set(xlabel='Our power - LunarRF power (dB)', ylabel='Pixels', title=variant+' power difference (both simulated)')
        fig.tight_layout(); fig.savefig(directory/f'{variant}_db_histogram.png', dpi=160); plt.close(fig)
        plot_map(np.ma.masked_where(~mask, diff), f'{variant}_db_difference.png', variant+' power difference (dB)', 'coolwarm', vmin=-20, vmax=20)
        code = np.zeros(shape, np.uint8)
        our_cov, ref_cov = agg['best_prx'] >= cfg['comparison_threshold_dbm'], published >= cfg['comparison_threshold_dbm']
        code[mask & our_cov & ~ref_cov] = 1
        code[mask & ~our_cov & ref_cov] = 2
        from matplotlib.colors import ListedColormap, BoundaryNorm
        plot_map(np.ma.masked_where(~mask | (code == 0), code), f'{variant}_disagreement.png',
                 variant+' coverage: 1=false covered, 2=missed', ListedColormap(['#ef4444', '#3b82f6']),
                 norm=BoundaryNorm([.5, 1.5, 2.5], 2), alpha=.8)
    atomic_json(directory/'metrics.json', results)
    with (directory/'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream); writer.writerow(['comparison', 'metric', 'value', 'label_origin'])
        for variant in ('norefl', 'reflection'):
            for key, value in results[variant].items():
                if isinstance(value, dict):
                    for name, number in value.items():
                        if not isinstance(number, list): writer.writerow([variant, key+'.'+name, number, 'simulated'])
                else: writer.writerow([variant, key, value, 'simulated'])
        for key, value in results['any_los'].items(): writer.writerow(['shared', 'any_los.'+key, value, 'simulated'])
    warnings = []
    if results['any_los']['iou'] is None or results['any_los']['iou'] < .8:
        warnings.append('Any-LOS IoU below 0.8: '+str(results['any_los']['iou']))
    if abs(results['norefl']['db_difference']['median']) > 6:
        warnings.append('Absolute median no-reflection dB difference above 6 dB')
    ground_diff = np.array([m['ground_minus_catalog_z_m'] for m in meta['masts']])
    lines = ['# Experiment A: Haworth LunarRF comparison (simulated)', '',
             'Both our outputs and LunarRF layers are **simulated**. No machine learning or parameter tuning.', '',
             f"Commit: `{meta['code_commit']}`. Seed: {cfg['seed']}. Processed {len(meta['finished'])}/{len(meta['masts'])} masts.",
             'Provenance, input hashes, finished IDs and mast ground elevations: `run.json`. Atomic aggregate checkpoint: `checkpoint.npz`.', '',
             '## Settings and validity', '',
             'Full base atlas catalog; projected 22,040 m distance to the original Haworth raster footprint selects masts. '
             'The buffered master DEM supplies bilinear ground and all intervening samples. '
             'Only original Haworth receiver pixels are traced, using spherical chord geometry, fast mode, 10 m spacing, '
             '1.8 GHz, 4 m TX and 1.5 m RX. FSPL plus single knife-edge diffraction; no reflection. '
             'Exact sensitivity is used for aggregate count; power coverage comparisons use -94.405 dBm as requested. '
             'Argmax ties use lower cand_id. Counts of zero are valid zero masts; padding outside Haworth is excluded. '
             'The earlier aligned count products masked published zeros to NaN; comparison count values are recovered '
             'from their untouched published source files using verified exact lattice crops. '
             'Per-link invalid terrain/range is excluded; valid_count records valid contributions. '
             'Areas use the 20 m projected grid pixel area (0.0004 km²), rather than a spherical area correction.', '',
             'Radio source: `interim/lunarrf/meta/atlas_v2/bundle/radio_params.json`; threshold confirmed by '
             '`stats_exact/disc_exact.json`. Fixed loss=0 is a documented assumption because no fixed/system loss is listed; '
             'required margin=0 assumes the reported SNR requirement and sensitivity threshold. '
             'LunarRF exact LOS, count definitions, activation details, tie handling and vertical datum remain undocumented (TODO); '
             'interpretations follow `reports/lunarrf_findings.md`.', '',
             f"DEM bilinear ground minus catalog z_m (m): median={np.median(ground_diff):.6f}, min={ground_diff.min():.6f}, max={ground_diff.max():.6f}. See `masts.csv`.", '',
             '## Comparison tables', '',
             'Confusion: TP/TN/FP/FN use LunarRF as reference. Complete metrics and histograms: `metrics.json`, `comparison.csv`.', '',
             '| Comparison | Pixels | Agreement | IoU | TP | TN | FP | FN | False-covered km² | Missed-covered km² |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name, c in [('Any LOS', results['any_los'])]+[(v+' '+k, results[v][k]) for v in ('norefl', 'reflection') for k in ('best_los', 'coverage', 'count_coverage')]:
        lines.append(f"| {name} | {c['valid_pixels']} | {c['agreement']:.6f} | {c['iou']} | {c['tp']} | {c['tn']} | {c['fp']} | {c['fn']} | {c['false_covered_km2']:.4f} | {c['missed_covered_km2']:.4f} |")
    lines += ['', '| Variant | Same best ID fraction | Median dB | MAD dB | p5 dB | p95 dB |', '|---|---:|---:|---:|---:|---:|']
    for variant in ('norefl', 'reflection'):
        result = results[variant]; d = result['db_difference']
        lines.append(f"| {variant} | {result['best_id_agreement']:.6f} | {d['median']:.4f} | {d['mad']:.4f} | {d['p5']:.4f} | {d['p95']:.4f} |")
    lines += ['', '## Maps and histograms', '']
    for variant in ('norefl', 'reflection'):
        for plot in ('db_histogram', 'db_difference', 'disagreement'):
            lines.append(f'![{variant} {plot}]({variant}_{plot}.png)')
    lines += ['', '## Diagnosis', '',
              'A constant dB offset suggests power or gain bookkeeping. Compare median with spread (MAD and p5/p95) '
              'before attributing a discrepancy to a constant budget term. The source power/gain values were used unchanged.', '',
              'Disagreement clustered on crater walls or ridges suggests terrain resolution or diffraction-model differences. '
              'LunarRF names delta-Bullington; ours uses a single knife-edge. '
              'The receiver DEM lattice matches the published terrain; catalog ground differences are separately recorded. '
              'Geometric LOS may differ from the unpublished exact LunarRF LOS criterion.', '',
              'A shifted band suggests registration. Here the original footprint is recovered from the published DEM mask, '
              'and its transform/CRS matches the master DEM at exact integer offsets. '
              'Maps show spatial disagreement for review; causal attribution cannot be established from these metrics alone. '
              'No parameters were tuned to force agreement.', '', '## Warnings', '']
    lines += warnings or ['No threshold warnings.']
    (directory/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return results, warnings


def run(config, max_masts=10, seconds=480, report=False):
    cfg = load_experiment(config); radio = cfg['radio']
    np.random.seed(cfg['seed'])
    root = paths.data_root(); directory = paths.reports_dir()/'exp_a'; directory.mkdir(exist_ok=True)
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=paths.repo_root(), capture_output=True, text=True, check=True).stdout.strip()
    print(f'Commit {commit}; seed {cfg["seed"]}; both chains simulated', flush=True)
    reference = root/cfg['reference_dir']
    with rasterio.open(reference/'Haworth__dem.tif') as src:
        mask = src.read_masks(1) > 0
        rr, cc = np.where(mask)
        window = rasterio.windows.Window(int(cc.min()), int(rr.min()), int(cc.max()-cc.min()+1), int(rr.max()-rr.min()+1))
        bounds = list(rasterio.windows.bounds(window, src.transform))
        transform = src.window_transform(window); crs = src.crs
        shape = (int(window.height), int(window.width))
        if not mask[int(window.row_off):int(window.row_off+window.height), int(window.col_off):int(window.col_off+window.width)].all():
            raise ValueError('Expected rectangular valid published footprint')
    dem_path = root/cfg['dem']; catalog = root/cfg['catalog']
    import moonmesh_train.los as los_module
    import moonmesh_train.rf as rf_module
    source_files = [Path(__file__), Path(los_module.__file__), Path(rf_module.__file__),
                    Path(config) if Path(config).is_absolute() else configs_dir()/config, dem_path, catalog]
    source_files += sorted(reference.glob('*.tif'))
    for name in ('los_count', 'count_1p800GHz', 'count_norefl_1p800GHz'):
        with rasterio.open(reference/f'Haworth__{name}.tif') as src:
            original = src.tags().get('source')
            if original:
                source_files.append(Path(original))
    identity = dict(config=cfg, bounds=bounds, shape=list(shape),
                    hashes={str(p): sha256_file(p) for p in source_files})
    checkpoint = directory/'checkpoint.npz'
    with rasterio.open(dem_path) as src:
        dem = src.read(1, masked=True).filled(np.nan); master = src.transform
        if src.crs != crs or master.a != transform.a or master.e != transform.e:
            raise ValueError('Reference/master lattice mismatch')
        off = (~master)*(transform.c, transform.f)
        if not np.allclose(off, np.round(off), atol=1e-8): raise ValueError('Noninteger reference offset')
        r0, c0 = int(round(off[1])), int(round(off[0]))
    if checkpoint.exists():
        agg, meta = load_checkpoint(checkpoint, identity)
    else:
        masts = select_masts(catalog, bounds, radio['max_range_m'])
        for mast in masts:
            ground = float(sample(dem, mast['x_m'], mast['y_m'], np.array(tuple(master)[:6])))
            if not np.isfinite(ground): raise ValueError('Invalid mast terrain')
            mast.update(dem_ground_m=ground, ground_minus_catalog_z_m=ground-mast['z_m'])
        agg = empty_aggregates(shape)
        meta = dict(identity=identity, code_commit=commit, label_origin='simulated', finished=[], masts=masts,
                    runtime_seconds=0.)
    print(f"Resuming {len(meta['finished'])}/{len(meta['masts'])} masts; footprint {shape}", flush=True)
    start = perf_counter(); processed = 0
    for mast in meta['masts']:
        if mast['cand_id'] in meta['finished']: continue
        if processed >= max_masts or perf_counter()-start >= seconds: break
        t0 = perf_counter(); tx = (mast['x_m'], mast['y_m'])
        pad = radio['max_range_m']*(1+((math.hypot(*tx)+2*radio['max_range_m'])/(2*R))**2)*1.001
        left = max(0, math.floor((tx[0]-pad-transform.c)/transform.a))
        right = min(shape[1], math.ceil((tx[0]+pad-transform.c)/transform.a))
        top = max(0, math.floor((transform.f-tx[1]-pad)/-transform.e))
        bottom = min(shape[0], math.ceil((transform.f-tx[1]+pad)/-transform.e))
        rr, cc = np.mgrid[top:bottom, left:right]
        index = (rr*shape[1]+cc).ravel()
        x, y = transform.c+(cc.ravel()+.5)*transform.a, transform.f+(rr.ravel()+.5)*transform.e
        xy = np.column_stack((x, y))
        trace = trace_pairs(tx, xy, cfg['h_tx_m'], cfg['h_rx_m'], dem, master, crs,
                            radio['max_range_m'], cfg['spacing_m'], C/(radio['frequency_ghz']*1e9), mode='fast')
        b = xyz(x, y, dem[r0+rr.ravel(), c0+cc.ravel()]+cfg['h_rx_m'])
        a = xyz(*tx, mast['dem_ground_m']+cfg['h_tx_m'])
        distance = np.linalg.norm(b-a, axis=-1)
        loss = fspl_db(np.where(trace['valid'], distance, np.nan), radio['frequency_ghz']*1e9)+knife_edge_loss_db(trace['max_knife_edge_nu'])
        prx = received_power_dbm(loss, radio)
        update(agg, index, prx, trace['los'], trace['valid'], mast['cand_id'],
               radio['receiver_sensitivity_dbm']+radio['required_margin_db'])
        meta['finished'].append(mast['cand_id']); processed += 1
        meta['runtime_seconds'] += perf_counter()-t0
        save_checkpoint(checkpoint, agg, meta)
        print(f"Mast {mast['cand_id']}: {len(meta['finished'])}/{len(meta['masts'])}; {perf_counter()-t0:.1f}s; valid links {int(trace['valid'].sum())}", flush=True)
    atomic_json(directory/'run.json', meta)
    with (directory/'masts.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(meta['masts'][0])+['label_origin'])
        writer.writeheader(); writer.writerows(dict(m, label_origin='simulated') for m in meta['masts'])
    complete = len(meta['finished']) == len(meta['masts'])
    if report:
        if not complete: raise ValueError('Comparison requires all selected masts')
        write_rasters(directory, agg, transform, crs, meta['code_commit'])
        metrics, warnings = make_report(agg, meta, cfg, directory, transform, crs)
        print(json.dumps(dict(any_los=metrics['any_los'], warnings=warnings)), flush=True)
    return complete
