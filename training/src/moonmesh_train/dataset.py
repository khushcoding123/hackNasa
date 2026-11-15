"""Resumable simulated dataset, with terrain-only inference features."""
import hashlib
import json
import math
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import rasterio
import yaml
from numba import njit, set_num_threads

from . import paths, rf
from .config import configs_dir, load_experiment, numba_threads
from .downloads import atomic_json, sha256_file
from .los import R, sample, unit
from .sampling import commit

TERRAIN = ('slope_deg', 'relative_elevation_200m', 'relative_elevation_1000m', 'roughness_100m')
FEATURES = ['distance_m', 'sin_bearing', 'cos_bearing', 'h_tx', 'h_rx', 'freq_ghz',
            'tx_ground_m', 'rx_ground_m', 'ground_difference_m'] + [
                f'{end}_{name}' for end in ('tx', 'rx') for name in TERRAIN] + [
                'tx_horizon_angle_deg', 'coarse_max_angle_deg'] + [f'profile_{i:02d}_relative_m' for i in range(16)]


def check_features(names):
    forbidden = ('best_', 'count', 'obstruction_', 'min_clearance', 'los', 'margin',
                 'covered', 'quality', 'valid', 'split', 'weight')
    if any(n not in FEATURES or n.endswith('_id') or any(x in n for x in forbidden) for n in names):
        raise ValueError('Forbidden or unregistered model feature')


def config_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@njit(cache=True, nogil=True)
def coarse_profiles(tx, receivers, ht, hr, dem, tr, points):
    """Interior XYZ chord samples: terrain minus straight antenna line, metres."""
    n = len(receivers)
    heights = np.empty((n, points))
    angles = np.empty(n)
    u = unit(tx[0], tx[1])
    ground = sample(dem, tx[0], tx[1], tr)
    a = u * (R + ground + ht)
    for i in range(n):
        x, y = receivers[i]
        v = unit(x, y)
        z = sample(dem, x, y, tr)
        b = v * (R + z + hr)
        maximum = -math.inf
        for j in range(points):
            f = (j+1)/(points+1)
            p = a + (b-a)*f
            radius = np.linalg.norm(p)
            px, py = 2*R*p[1]/(radius-p[2]), 2*R*p[0]/(radius-p[2])
            elevation = sample(dem, px, py, tr)
            heights[i,j] = R + elevation - radius
            surface = p/radius*(R+elevation)
            delta = surface-a
            vertical = np.dot(delta, u)
            horizontal = np.linalg.norm(delta-vertical*u)
            angle = math.atan2(vertical, horizontal)*180/math.pi
            maximum = max(maximum, angle)
        angles[i] = maximum
    return heights, angles


def sample_receivers(arrays, cfg, rng, required):
    valid = arrays['valid_mask'].astype(bool)
    boundary = valid & ((np.abs(arrays['min_clearance_m']) < cfg['clearance_boundary_m']) |
                        (np.abs(arrays['link_margin_db']-required) < cfg['margin_boundary_db']))
    population = np.flatnonzero(valid)
    edge = np.flatnonzero(boundary)
    uniform = rng.choice(population, min(cfg['uniform_receivers'], len(population)), replace=False)
    extra = rng.choice(edge, min(cfg['boundary_receivers'], len(edge)), replace=False)
    indices = np.concatenate((uniform, extra))
    # Inverse expected multiplicity across the two independent sampling draws.
    pu = len(uniform)/len(population) if len(population) else 0
    pb = len(extra)/len(edge) if len(edge) else 0
    weights = 1/(pu + boundary.ravel()[indices]*pb)
    return indices, np.array(['uniform']*len(uniform)+['boundary']*len(extra)), weights, boundary


class Builder:
    def __init__(self, config, splits):
        setup_start = perf_counter()
        self.cfg = yaml.safe_load(Path(config).read_text())
        self.exp = load_experiment(self.cfg['experiment_config'])
        self.splits = json.loads(Path(splits).read_text())
        self.root = paths.data_root()
        self.out = self.root/self.cfg['output']
        self.out.mkdir(parents=True, exist_ok=True)
        self.code_commit = commit()
        self.source = self.root/self.cfg['dem']
        self.hashes = {'dem': sha256_file(self.source), 'splits': sha256_file(Path(splits)),
                       'implementation': {n: sha256_file(Path(__file__).with_name(n)) for n in ('dataset.py', 'rf.py', 'los.py')}}
        with rasterio.open(self.source) as src:
            self.dem = src.read(1, masked=True).filled(np.nan)
            self.tr, self.crs = src.transform, src.crs
            source_path = src.tags().get('source')
        catalog_path = paths.repo_root()/'training/data_manifest.json'
        catalog = json.loads(catalog_path.read_text())
        self.source_ids = [e['source_id'] for e in catalog['entries']
                           if source_path and Path(e.get('local_path','')).resolve()==Path(source_path).resolve()] or None
        self.hashes['source_catalog'] = sha256_file(catalog_path)
        self.terrain = {}
        for name in TERRAIN:
            source = self.root/self.cfg['features_dir']/(name+'.tif')
            self.hashes[name] = sha256_file(source)
            with rasterio.open(source) as src:
                if src.transform != self.tr or src.crs != self.crs or src.shape != self.dem.shape:
                    raise ValueError('Feature lattice differs from DEM')
                self.terrain[name] = src.read(1, masked=True).filled(np.nan)
        self.grid = json.loads((self.source.parent/'grid.json').read_text())
        self.hashes['grid'] = sha256_file(self.source.parent/'grid.json')
        self.plan = []
        points = {p['transmitter_id']: p for p in self.splits['transmitters']}
        for split, ids in self.splits['splits'].items():
            for tid in ids:
                for height in self.exp['tx_heights_m']:
                    self.plan.append(dict(points[tid], split=split, height=height,
                                          scenario_id=f'{tid}_tx{height}_rx{self.exp["rx_height_m"]}'))
        check_features(FEATURES)
        self.setup_seconds = perf_counter()-setup_start
        print(f'Git commit {self.code_commit}; simulated scenarios {len(self.plan)}; seed {self.cfg["seed"]}', flush=True)

    def signature(self, p):
        return config_hash(dict(config=self.cfg, experiment=self.exp, hashes=self.hashes, placement=p))

    def completed(self, p):
        folder = self.out/'scenarios'/p['scenario_id']
        sidecar = folder/'dataset.json'
        if not sidecar.is_file():
            return None
        saved = json.loads(sidecar.read_text())
        if saved['config_hash'] == self.signature(p) and all((folder/f).is_file() for f in
                ('labels.tif', 'labels.json', 'labels_classes.tif', 'receivers.parquet')):
            return saved
        return None

    def build(self, p):
        saved = self.completed(p)
        if saved:
            return saved
        set_num_threads(numba_threads(getattr(self, 'thread_limit', self.cfg['numba_threads'])))
        # Conservative per-scenario headroom, checked before each write batch.
        if shutil.disk_usage(self.root.anchor).free < 11*1024**3:
            raise OSError('Insufficient disk: less than 10 GB would remain')
        start = perf_counter()
        folder = self.out/'scenarios'/p['scenario_id']
        folder.mkdir(parents=True, exist_ok=True)
        radio = self.exp['radio']
        tx = np.array([p['x_m'], p['y_m']])
        ht, hr = p['height'], self.exp['rx_height_m']
        t = perf_counter()
        arrays, tr, window = rf.label_arrays(tx, ht, hr, self.dem, self.tr, self.crs, radio)
        label_seconds = perf_counter()-t
        meta = dict(label_origin='simulated', code_commit=self.code_commit, radio_profile=radio,
                    radio_profile_name=self.exp['radio_profile'], propagation_model='fspl+p526_single_edge (simulated)',
                    dem_path=str(self.source), dem_sha256=self.hashes['dem'], sample_spacing_m=10,
                    tracer_mode='fast', threshold_db=radio['required_margin_db'], tx_xy_m=tx.tolist(),
                    h_tx_m=ht, h_rx_m=hr, config_hash=self.signature(p),
                    master_transform=list(self.tr)[:6], window=[window.col_off, window.row_off, window.width, window.height])
        rf.write_scenario(folder/'labels.tif', arrays, tr, self.crs, meta)
        rng = np.random.default_rng(int(config_hash([self.cfg['seed'], p['scenario_id']])[:16], 16))
        idx, kinds, weights, boundary = sample_receivers(arrays, self.cfg, rng, radio['required_margin_db'])
        rr, cc = np.unravel_index(idx, arrays['los'].shape)
        x, y = tr.c+(cc+.5)*tr.a, tr.f+(rr+.5)*tr.e
        rows = rr+int(window.row_off)
        cols = cc+int(window.col_off)
        xy = np.column_stack((x,y))
        frame = pd.DataFrame(dict(scenario_id=p['scenario_id'], receiver_x_m=x, receiver_y_m=y,
                                   sample_kind=kinds, sample_weight=weights, label_origin='simulated',
                                   terrain_valid=True, label_valid=True))
        timings = {}
        t = perf_counter()
        ground = float(self.dem[p['row'],p['col']])
        rx_ground = self.dem[rows,cols]
        a = rf.xyz(*tx, ground+ht)
        b = rf.xyz(x,y,rx_ground+hr)
        frame['distance_m'] = np.linalg.norm(b-a,axis=1)
        # True local tangent bearing, clockwise from north.
        u = unit(*tx)
        lon = math.atan2(tx[0],tx[1])
        east = np.array([-math.sin(lon),math.cos(lon),0.])
        north = np.cross(u,east)
        bearing = np.arctan2((b-a)@east,(b-a)@north)
        frame['bearing_deg'] = np.degrees(bearing)%360
        frame['sin_bearing'], frame['cos_bearing'] = np.sin(bearing), np.cos(bearing)
        frame['h_tx'], frame['h_rx'], frame['freq_ghz'] = ht, hr, radio['frequency_ghz']
        frame['tx_ground_m'], frame['rx_ground_m'] = ground, rx_ground
        frame['ground_difference_m'] = rx_ground-ground
        timings['endpoint_geometry_seconds'] = perf_counter()-t
        t = perf_counter()
        for name, layer in self.terrain.items():
            frame['tx_'+name] = layer[p['row'],p['col']]
            frame['rx_'+name] = layer[rows,cols]
        timings['terrain_lookup_seconds'] = perf_counter()-t
        t = perf_counter()
        # Terrain-only horizon cache shared by all receivers; one per TX/height.
        bins = self.cfg['horizon_bins']
        azimuth = np.arange(bins)*2*np.pi/bins
        # Projected rays, binned clockwise from grid north (recorded in manifest).
        end = tx + radio['max_range_m']*np.column_stack((np.sin(azimuth),np.cos(azimuth)))
        _, horizon = coarse_profiles(tx,end,ht,0.,self.dem,np.array(tuple(self.tr)[:6]),
                                     math.ceil(radio['max_range_m']/self.cfg['horizon_spacing_m'])-1)
        np.savez_compressed(folder/'horizon.npz', angle_deg=horizon, grid_bearing_deg=np.degrees(azimuth), label_origin='simulated')
        grid_bearing = np.arctan2(x-tx[0],y-tx[1])%(2*np.pi)
        frame['tx_horizon_angle_deg'] = horizon[np.floor(grid_bearing*bins/(2*np.pi)+.5).astype(int)%bins]
        timings['horizon_precompute_and_lookup_seconds'] = perf_counter()-t
        t = perf_counter()
        profile, angle = coarse_profiles(tx,xy,ht,hr,self.dem,np.array(tuple(self.tr)[:6]),16)
        frame['coarse_max_angle_deg'] = angle
        for i in range(16):
            frame[f'profile_{i:02d}_relative_m'] = profile[:,i]
        timings['coarse_profile_seconds'] = perf_counter()-t
        for name in rf.FLOAT_BANDS+('rf_covered','terrain_caused_rf_outage','quality_flags'):
            frame[name] = arrays[name].ravel()[idx]
        frame['geometric_los'] = arrays['los'].ravel()[idx]
        for name in FEATURES:
            frame[name] = frame[name].astype('float32')
        temporary = folder/'receivers.parquet.tmp'
        frame.to_parquet(temporary,index=False,compression='zstd')
        temporary.replace(folder/'receivers.parquet')
        valid = arrays['valid_mask'].astype(bool)
        counts = dict(valid_px=int(valid.sum()), blocked_px=int(np.sum(valid & (arrays['los']==0))),
                      clear_px=int(np.sum(valid & (arrays['los']==1))), boundary_px=int(boundary.sum()),
                      unknown_px=int((~valid).sum()), partial_unknown_px=int(np.sum(arrays['quality_flags']==2)),
                      out_of_range_px=int(np.sum(arrays['quality_flags']==3)), uniform_n=int(np.sum(kinds=='uniform')),
                      uniform_covered=int(frame.loc[frame.sample_kind=='uniform','rf_covered'].sum()))
        result = dict(placement=p, config_hash=self.signature(p), counts=counts, feature_timings=timings,
                      label_seconds=label_seconds, total_seconds=perf_counter()-start, label_origin='simulated',
                      bytes=sum(f.stat().st_size for f in folder.iterdir() if f.is_file()))
        atomic_json(folder/'dataset.json', result)
        print(f'Built {p["scenario_id"]}: {result["total_seconds"]:.2f}s, {len(frame)} receivers', flush=True)
        return result

    def estimate(self):
        saved = [self.completed(p) for p in self.plan[:5]]
        if not all(saved):
            return
        estimate = dict(measured_scenarios=5, planned=len(self.plan),
                        projected_seconds=sum(s['total_seconds'] for s in saved)/5*len(self.plan),
                        projected_bytes=sum(s['bytes'] for s in saved)/5*len(self.plan),
                        free_bytes=shutil.disk_usage(self.root.anchor).free, label_origin='simulated')
        atomic_json(paths.reports_dir()/'dataset/estimate.json',estimate)
        print(json.dumps(estimate),flush=True)
        if estimate['projected_bytes'] > 40*1024**3:
            raise OSError('Projected dataset exceeds 40 GB; storage reduction required before continuing')
        if estimate['free_bytes']-estimate['projected_bytes'] < 10*1024**3:
            raise OSError('Projected dataset would leave less than 10 GB free')

    def finalize(self):
        saved = [self.completed(p) for p in self.plan]
        if not all(saved):
            print(f'Checkpoint: {sum(s is not None for s in saved)}/{len(saved)} complete',flush=True)
            return None
        records = []
        radio = self.exp['radio']
        for p in self.plan:
            records.append(dict(scenario_id=p['scenario_id'], parent_scene_id=p['spatial_block_id'], site_id='haworth',
                spatial_block_id=p['spatial_block_id'], transmitter_id=p['transmitter_id'],
                source_product_ids=self.source_ids, terrain_grid_id=self.hashes['grid'],
                generator_version=config_hash(self.hashes['implementation']), code_commit=self.code_commit,
                transmitter_x_m=p['x_m'], transmitter_y_m=p['y_m'], tx_height_agl_m=p['height'],
                rx_height_agl_m=self.exp['rx_height_m'], frequency_ghz=radio['frequency_ghz'],
                tx_power_w=10**((radio['tx_power_dbm']-30)/10),
                **{n:radio[n] for n in ('tx_gain_dbi','rx_gain_dbi','fixed_loss_db','receiver_sensitivity_dbm','required_margin_db','max_range_m')},
                split=p['split'],stratum=p['stratum'],deployable=p['deployable'],label_origin='simulated',
                config_hash=self.signature(p), label_path=str(self.out/'scenarios'/p['scenario_id']/'labels.tif')))
        pd.DataFrame(records).to_parquet(self.out/'scenarios.parquet',index=False)
        counts = {}
        sampled_classes = {}
        for split in ('train','val','test'):
            subset = [s for s in saved if s['placement']['split']==split]
            temporary = self.out/f'receivers_{split}.parquet.tmp'
            with pq.ParquetWriter(temporary,pq.read_schema(self.out/'scenarios'/subset[0]['placement']['scenario_id']/'receivers.parquet'),compression='zstd') as writer:
                sampled_classes[split] = {0:0,1:0}
                for s in subset:
                    table = pq.read_table(self.out/'scenarios'/s['placement']['scenario_id']/'receivers.parquet')
                    check_features([n for n in table.column_names if n in FEATURES])
                    if not set(FEATURES).issubset(table.column_names):
                        raise ValueError('Missing model features')
                    los = table['geometric_los'].to_numpy()
                    for label in (0,1):
                        sampled_classes[split][label] += int(np.sum(los==label))
                    if not table['label_valid'].to_numpy().all():
                        raise ValueError('Invalid targets in receiver table')
                    writer.write_table(table)
            temporary.replace(self.out/f'receivers_{split}.parquet')
            counts[split] = {k:sum(s['counts'][k] for s in subset) for k in subset[0]['counts']}
            counts[split].update(unique_transmitters=len({s['placement']['transmitter_id'] for s in subset}),
                                 blocks=len({s['placement']['spatial_block_id'] for s in subset}))
            counts[split]['covered_prevalence'] = counts[split]['uniform_covered']/counts[split]['uniform_n']
        timings = {k:sum(s['feature_timings'][k] for s in saved) for k in saved[0]['feature_timings']}
        manifest = dict(feature_columns=FEATURES, feature_timings_seconds=timings, hashes=self.hashes,
                        config=self.cfg, experiment=self.exp, code_commit=self.code_commit, seed=self.cfg['seed'],
                        label_origin='simulated', counts=counts, scenarios=len(records),
                        sampling='15000 uniform + up to 5000 boundary, independently without replacement; overlap retained as distinct draws',
                        weight_definition='1 / (n_uniform/N_valid + is_boundary*n_boundary/N_boundary); inverse expected multiplicity',
                        horizon='360 grid-north bins, nearest bin; 20 m projected radial spacing; spherical terrain angle from antenna local tangent',
                        profile='16 interior XYZ chord fractions (j+1)/17; terrain minus chord radial elevation in metres',
                        source_product_ids=self.source_ids, input_read_hash_setup_seconds=self.setup_seconds,
                        TODO='Vertical datum remains unresolved in the existing grid metadata.')
        atomic_json(self.out/'manifest.json',manifest)
        report = paths.reports_dir()/'dataset/report.md'
        report.parent.mkdir(parents=True,exist_ok=True)
        lines = ['# Haworth pilot dataset (simulated)', '', f'Code commit `{self.code_commit}`; seed {self.cfg["seed"]}; {len(records)} scenarios.', '',
                 '| Split | Transmitters | Blocks | Valid px | Blocked px | Boundary px | Unknown px | Uniform covered prevalence |',
                 '|---|---:|---:|---:|---:|---:|---:|---:|']
        for split,c in counts.items():
            lines.append(f'| {split} | '+ ' | '.join(str(c[k]) for k in ('unique_transmitters','blocks','valid_px','blocked_px','boundary_px','unknown_px','covered_prevalence'))+' |')
        lines += ['', 'Counts sum scenario windows, including repeated receivers across heights and transmitters. Unknown includes outside-range window corners; partial and outside-range counts are separate in manifest.json. Invalid receivers never enter target tables.', '',
                  'Sampling: '+manifest['sampling']+'. Uniform draws alone determine prevalence. Boundary means |clearance| < 5 m OR |margin-required| < 3 dB. '+manifest['weight_definition']+'. Use uniform rows for validation metrics.', '',
                  'All targets are simulated FSPL + P.526 single-edge labels using the unchanged step 06 labeler and fast 10 m LOS tracing. Full float and native-class GeoTIFFs preserve reviewable targets; no storage reduction needed if estimate is below 40 GB.', '',
                  'Feature list, hashes, exact geometry and inference feature timings are in manifest.json. Terrain lookup timings exclude initial whole-grid reads (recorded separately in build log); upstream terrain derivative preparation must also be charged when not cached. Roughness uses the existing 100 m population standard deviation. Horizon precomputation is measured per TX/height and saved for reuse; coarse profiles use only 16 samples, never full-trace outputs.', '',
                  'Schema reference: the step calls it section 6; the actual schema is in spec Step 6. IDs and diagnostics are metadata/targets, excluded by the explicit feature allowlist. No test tuning performed.', '',
                  f'Source product IDs from the inspected DEM source and training catalog: {self.source_ids}. TODO: Vertical datum unresolved in existing grid metadata. Source paths and SHA256 remain recorded.', '', 'Feature compute totals (seconds):', '', '```json',json.dumps(timings,indent=2),'```']
        report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
        gates = [dict(name='Every planned scenario built',passed=len(records)==len(self.plan),value=len(records)),
                 dict(name='Three receiver tables and safe feature allowlist',passed=all((self.out/f'receivers_{s}.parquet').is_file() for s in counts),value=len(FEATURES)),
                 dict(name='Report with split counts and prevalence',passed=report.is_file(),value=counts),
                 dict(name='Clear and blocked receivers in each split',passed=all(c[0]>0 and c[1]>0 for c in sampled_classes.values()),value=sampled_classes)]
        atomic_json(paths.reports_dir()/'dataset/gates.json',gates)
        return gates


_PROCESS_BUILDER = None


def _initialize_worker(config, splits, threads):
    global _PROCESS_BUILDER
    _PROCESS_BUILDER = Builder(config, splits)
    _PROCESS_BUILDER.thread_limit = threads


def _build_worker(placement):
    return _PROCESS_BUILDER.build(placement)


def run(config, splits, workers=1, max_scenarios=None):
    if workers < 1:
        raise ValueError('workers must be positive')
    start = perf_counter()
    builder = Builder(config,splits)
    threads = min(builder.cfg['numba_threads'], max(1, (os.cpu_count() or 1)//workers))
    builder.thread_limit = threads
    print(f'Workers {workers}; Numba threads per worker {threads}',flush=True)
    print(f'Input read/hash/setup seconds: {perf_counter()-start:.3f}',flush=True)
    pending = [p for p in builder.plan if not builder.completed(p)]
    if max_scenarios is not None:
        pending = pending[:max_scenarios]
    first = [p for p in pending if p in builder.plan[:5]]
    for p in first:
        builder.build(p)
    builder.estimate()
    remaining = [p for p in pending if p not in first]
    if workers == 1:
        for p in remaining:
            builder.build(p)
    elif remaining:
        with ProcessPoolExecutor(max_workers=workers,initializer=_initialize_worker,
                                 initargs=(config,splits,threads)) as pool:
            list(pool.map(_build_worker,remaining))
    return builder.finalize()
