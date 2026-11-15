"""Terrain-only transmitter sampling and buffered spatial split manifests."""
import json
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import distance_transform_edt
from scipy.spatial.distance import cdist

from lunar_core.terrain import polar_stereographic_south
from moonmesh.models import Mission
from .config import load_experiment
from .downloads import atomic_json, sha256_file
from .paths import data_root, repo_root, reports_dir

STRATA = ('ridge_rim_crest', 'shoulder', 'steep_wall', 'floor', 'plain')


def commit():
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_root(), text=True).strip()


def classify(relative, slope, rules):
    """Disjoint rules: steep wall takes precedence over relative elevation."""
    result = np.full(relative.shape, 4, dtype=np.int8)
    result[relative <= rules['floor_max_m']] = 3
    result[relative >= rules['shoulder_min_m']] = 1
    result[relative >= rules['crest_min_m']] = 0
    result[slope >= rules['steep_min_deg']] = 2
    result[~np.isfinite(relative) | ~np.isfinite(slope)] = -1
    return result


def full_disk_valid(valid, pixel_m, radius_m):
    """Conservative cell-area support, including raster exterior and holes."""
    padded = np.pad(valid, 1, constant_values=False)
    distances = distance_transform_edt(padded, sampling=pixel_m)[1:-1, 1:-1]
    return distances > radius_m + pixel_m / np.sqrt(2)


def stratified_sample(classes, eligible, transform, count, spacing, seed):
    if count % len(STRATA):
        raise ValueError('Sample count must be divisible by five')
    rng = np.random.default_rng(seed)
    pools = [rng.permutation(np.flatnonzero(eligible & (classes == k))) for k in range(5)]
    offsets = [0] * 5
    selected = []
    width = classes.shape[1]
    for _ in range(count // 5):
        for k in map(int, rng.permutation(5)):
            while offsets[k] < len(pools[k]):
                index = int(pools[k][offsets[k]])
                offsets[k] += 1
                row, col = divmod(index, width)
                x = transform.a * (col + 0.5) + transform.b * (row + 0.5) + transform.c
                y = transform.d * (col + 0.5) + transform.e * (row + 0.5) + transform.f
                if all(np.hypot(x-p['x_m'], y-p['y_m']) >= spacing for p in selected):
                    selected.append(dict(transmitter_id=f'haworth_tx_{len(selected):03d}',
                                         row=row, col=col, x_m=x, y_m=y, stratum=STRATA[k]))
                    break
            else:
                raise ValueError(f'Insufficient separated eligible terrain in {STRATA[k]}')
    return selected


def sample(config_path):
    config_path = Path(config_path)
    cfg = load_experiment(config_path)
    terrain = data_root() / 'derived/terrain/haworth'
    inputs = dict(grid=terrain/'grid.json', dem=terrain/'pgda90_dem_20m.tif',
                  relative=terrain/'features_pgda90/relative_elevation_1000m.tif',
                  slope=terrain/'features_pgda90/slope_deg.tif')
    arrays = {}
    with rasterio.open(inputs['dem']) as src:
        transform, crs, shape = src.transform, src.crs, src.shape
        if transform.b or transform.d or transform.a != -transform.e:
            raise ValueError('Require square north-up grid')
        valid = np.isfinite(src.read(1, masked=True).filled(np.nan))
    grid = json.loads(inputs['grid'].read_text())
    if shape != (grid['height'], grid['width']) or list(transform)[:6] != grid['transform'] or crs != rasterio.crs.CRS.from_wkt(grid['crs_wkt']):
        raise ValueError('DEM differs from master grid')
    for name in ('relative', 'slope'):
        with rasterio.open(inputs[name]) as src:
            if src.shape != shape or src.transform != transform or src.crs != crs:
                raise ValueError(f'{name} differs from master grid')
            arrays[name] = src.read(1, masked=True).filled(np.nan)
    eligible = full_disk_valid(valid, transform.a, cfg['max_range_m'])
    classes = classify(arrays['relative'], arrays['slope'], cfg['terrain_strata'])
    points = stratified_sample(classes, eligible, transform, cfg['pilot_transmitters'], cfg['min_spacing_m'], cfg['seed'])
    site_x, site_y = polar_stereographic_south(cfg['latitude_deg'], cfg['longitude_deg'])
    slope_limit = Mission.model_fields['slope_limit'].default
    for p in points:
        r, c = p['row'], p['col']
        p.update(slope_deg=float(arrays['slope'][r,c]), relative_elevation_1000m_m=float(arrays['relative'][r,c]),
                 deployable=bool(arrays['slope'][r,c] < slope_limit and np.hypot(p['x_m']-site_x,p['y_m']-site_y) <= cfg['deployment_radius_m']))
    result = dict(seed=cfg['seed'], git_commit=commit(), config_sha256=sha256_file(config_path),
                  data_hashes={name:dict(path=str(path),sha256=sha256_file(path)) for name,path in inputs.items()},
                  eligible_pixels=int(eligible.sum()), eligible_per_stratum={s:int(np.sum(eligible & (classes==k))) for k,s in enumerate(STRATA)},
                  deployable_slope_limit_deg=slope_limit, transmitters=points,
                  label_origin='simulated', note='Terrain-only placements for future simulated labels; no coverage layers read.')
    atomic_json(reports_dir()/'splits/transmitters.json', result)
    print(json.dumps({k:v for k,v in result.items() if k in ('git_commit','eligible_pixels','eligible_per_stratum')}), flush=True)
    return result


def spatial_split(points, cfg):
    """Seeded contiguous block slabs; select layout by geometry-only retention.

    Whole occupied blocks are allocated approximately 70/15/15 before buffering.
    Validation and test adjoin at one end to avoid two training buffer boundaries.
    No terrain values or labels enter the layout objective.
    """
    xy = np.array([[p['x_m'],p['y_m']] for p in points])
    blocks = np.floor(xy / cfg['block_size_m']).astype(int)
    unique = np.unique(blocks, axis=0)
    ntrain = round(len(unique)*cfg['split_fractions'][0])
    nval = round(len(unique)*cfg['split_fractions'][1])
    rng = np.random.default_rng(cfg['seed'])
    best = None
    for trial in range(cfg['split_layout_trials']):
        angle = rng.uniform(0, 2*np.pi)
        order = np.argsort(unique @ np.array([np.cos(angle),np.sin(angle)]), kind='stable')
        membership = {tuple(unique[index]):('train' if j<ntrain else 'val' if j<ntrain+nval else 'test') for j,index in enumerate(order)}
        assigned = np.array([membership[tuple(b)] for b in blocks], dtype=object)
        holdout = xy[assigned!='train']
        drop = (assigned=='train') & (cdist(xy,holdout).min(axis=1) < cfg['buffer_m'])
        final = assigned.copy()
        final[drop] = 'dropped'
        counts = Counter(final)
        strata = Counter(p['stratum'] for p,s in zip(points,final) if s!='dropped')
        feasible = min(counts[s] for s in ('train','val','test'))>=10 and min(strata.get(s,0) for s in STRATA)>=15
        score = (feasible, int((~drop).sum()))
        if best is None or score > best[0]:
            best = (score, final.tolist(), membership, trial, angle)
    return best


def footprint_overlap(points, memberships, radius, resolution, bounds):
    """Union overlap estimate on explicitly reported raster cell centers."""
    left,bottom,right,top = bounds
    x = np.arange(left+resolution/2,right,resolution)
    y = np.arange(bottom+resolution/2,top,resolution)
    masks = {s:np.zeros((len(y),len(x)),bool) for s in ('train','test')}
    for p,s in zip(points,memberships):
        if s in masks:
            masks[s] |= (x[None,:]-p['x_m'])**2+(y[:,None]-p['y_m'])**2 <= radius**2
    overlap = int(np.sum(masks['train'] & masks['test']))
    test = int(masks['test'].sum())
    return dict(method='disk unions sampled at cell centers on master extent', resolution_m=resolution,
                overlap_km2=overlap*resolution**2/1e6, test_union_km2=test*resolution**2/1e6,
                fraction_test_union_overlapped=overlap/test if test else None)


def make_splits(config_path):
    config_path = Path(config_path)
    cfg = load_experiment(config_path)
    source_path = reports_dir()/'splits/transmitters.json'
    source = json.loads(source_path.read_text())
    if source['config_sha256'] != sha256_file(config_path):
        raise ValueError('Sampling config changed; rerun sampling')
    for item in source['data_hashes'].values():
        if sha256_file(Path(item['path'])) != item['sha256']:
            raise ValueError('Sampling terrain changed; rerun sampling')
    points = source['transmitters']
    _, memberships, blocks, trial, angle = spatial_split(points,cfg)
    for p,s in zip(points,memberships):
        p['split'] = s
        p['spatial_block_id'] = '_'.join(map(str,np.floor(np.array([p['x_m'],p['y_m']])/cfg['block_size_m']).astype(int)))
    grid = json.loads(Path(source['data_hashes']['grid']['path']).read_text())
    counts = {s:dict(transmitters=sum(v==s for v in memberships),
                     strata={k:sum(p['stratum']==k and p['split']==s for p in points) for k in STRATA},
                     blocks=len({p['spatial_block_id'] for p in points if p['split']==s}),
                     deployable=sum(p['deployable'] and p['split']==s for p in points)) for s in ('train','val','test','dropped')}
    hillshade = data_root()/'derived/terrain/haworth/hillshade_pgda90.tif'
    result = dict(seed=cfg['seed'], git_commit=commit(), config_sha256=sha256_file(config_path),
                  data_hashes=source['data_hashes'], sampling_sha256=sha256_file(source_path),
                  hillshade_sha256=sha256_file(hillshade), label_origin='simulated',
                  splits={s:[p['transmitter_id'] for p in points if p['split']==s] for s in ('train','val','test')},
                  dropped_transmitter_ids=[p['transmitter_id'] for p in points if p['split']=='dropped'],
                  transmitters=points, counts=counts, buffer_m=cfg['buffer_m'], max_range_m=cfg['max_range_m'],
                  block_assignments={f'{b[0]}_{b[1]}':s for b,s in blocks.items()},
                  layout=dict(method='contiguous projected block slabs; geometry-only maximum retention', trials=cfg['split_layout_trials'], selected_trial=trial, angle_radians=angle),
                  grouping='All heights, frequencies, resolutions and augmentations inherit transmitter split.',
                  footprint_overlap=footprint_overlap(points,memberships,cfg['max_range_m'],cfg['footprint_report_resolution_m'],grid['bounds']),
                  cross_site_splits=[dict(train_sites=[site],test_sites=[s for s in ('haworth','malapert','nobile-rim-1') if s!=site], train_transmitter_ids=[],test_transmitter_ids=[],status='planned_until_step_14') for site in ('haworth','malapert','nobile-rim-1')])
    atomic_json(repo_root()/'training/splits.json',result)
    atomic_json(reports_dir()/'splits/counts.json',result)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10,10))
    with rasterio.open(hillshade) as src:
        image = src.read(1,out_shape=(src.height//4,src.width//4),masked=True)
        ax.imshow(image,extent=(src.bounds.left,src.bounds.right,src.bounds.bottom,src.bounds.top),cmap='gray')
    for s,color in [('train','tab:blue'),('val','tab:orange'),('test','tab:red'),('dropped','black')]:
        subset=[p for p in points if p['split']==s]
        ax.scatter([p['x_m'] for p in subset],[p['y_m'] for p in subset],c=color,label=f'{s} ({len(subset)})',s=24,marker='x' if s=='dropped' else 'o')
    ax.set(xlabel='Lunar easting (m)',ylabel='Lunar northing (m)',title='Haworth terrain-only placements for simulated scenarios')
    ax.legend(); fig.tight_layout()
    fig.savefig(reports_dir()/'splits/split_map.png',dpi=160); plt.close(fig)
    print(json.dumps(dict(git_commit=result['git_commit'],counts=counts,footprint_overlap=result['footprint_overlap'])),flush=True)
    return result



