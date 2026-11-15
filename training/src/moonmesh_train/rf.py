"""Simulated FSPL plus ITU-R P.526 single-edge surface-link labels."""
import math
from pathlib import Path
import subprocess
from time import perf_counter

import numpy as np
import rasterio
from rasterio.windows import Window
from pyproj import CRS, Transformer

from . import paths
from .config import load_experiment
from .downloads import atomic_json, sha256_file
from .los import R, QUALITY, sample, trace_pairs

C = 299792458.
BYTE_BANDS = ('los', 'valid_mask', 'rf_covered', 'terrain_caused_rf_outage', 'quality_flags')
FLOAT_BANDS = ('min_clearance_m', 'obstruction_x_m', 'obstruction_y_m',
               'obstruction_distance_m', 'path_loss_db', 'free_space_margin_db',
               'received_power_dbm', 'link_margin_db')


def fspl_db(d_m, f_hz):
    """Free-space loss; d_m is the antenna-to-antenna 3D chord length."""
    d = np.asarray(d_m, dtype=float)
    if np.any(d <= 0) or not np.isfinite(f_hz) or f_hz <= 0:
        raise ValueError('FSPL requires positive distance and frequency')
    return 20*np.log10(d) + 20*np.log10(f_hz) - 147.55


def knife_edge_loss_db(nu):
    nu = np.asarray(nu, dtype=float)
    x = nu - .1
    # arcsinh is algebraically identical and stable for large negative nu.
    return np.where(nu > -.78, 6.9 + 20/np.log(10)*np.arcsinh(x),
                    np.where(np.isnan(nu), np.nan, 0.))


def received_power_dbm(path_loss_db, radio):
    return (radio['tx_power_dbm'] + radio['tx_gain_dbi'] + radio['rx_gain_dbi']
            - radio['fixed_loss_db'] - np.asarray(path_loss_db))


def classify(valid, los, margin, free_space_margin, required_margin):
    valid = np.asarray(valid, dtype=bool)
    dead = valid & (margin < required_margin)
    return dict(rf_covered=valid & (margin >= required_margin),
                terrain_dead_zone=valid & (los == 0), rf_dead_zone=dead,
                terrain_caused_rf_outage=dead & (free_space_margin >= required_margin),
                unknown=~valid)


def xyz(x, y, elevation):
    rho = np.hypot(x, y)
    lat = 2*np.arctan(rho/(2*R))-np.pi/2
    lon = np.arctan2(x, y)
    radius = R + elevation
    return np.stack((radius*np.cos(lat)*np.cos(lon),
                     radius*np.cos(lat)*np.sin(lon), radius*np.sin(lat)), axis=-1)


def label_arrays(tx_xy, h_tx, h_rx, dem, transform, crs, radio):
    """Trace a conservative range window, retaining the full DEM for sampling."""
    pad = radio['max_range_m']*(1+((math.hypot(*tx_xy)+2*radio['max_range_m'])/(2*R))**2)*1.001
    c0 = max(0, math.floor((tx_xy[0]-pad-transform.c)/transform.a))
    c1 = min(dem.shape[1], math.ceil((tx_xy[0]+pad-transform.c)/transform.a))
    r0 = max(0, math.floor((transform.f-tx_xy[1]-pad)/-transform.e))
    r1 = min(dem.shape[0], math.ceil((transform.f-tx_xy[1]+pad)/-transform.e))
    if c1 <= c0 or r1 <= r0:
        raise ValueError('Scenario does not overlap the DEM')
    window = Window(c0, r0, c1-c0, r1-r0)
    tr = rasterio.windows.transform(window, transform)
    rr, cc = np.mgrid[r0:r1, c0:c1]
    x, y = transform.c+(cc+.5)*transform.a, transform.f+(rr+.5)*transform.e
    trace = trace_pairs(tx_xy, np.column_stack((x.ravel(), y.ravel())), h_tx, h_rx,
                        dem, transform, crs, radio['max_range_m'], 10,
                        C/(radio['frequency_ghz']*1e9), mode='fast')
    trace = {k: v.reshape(rr.shape) for k, v in trace.items()}
    ground = sample(dem, *tx_xy, np.asarray(tuple(transform)[:6]))
    a = xyz(*tx_xy, ground+h_tx)
    # Receiver centers coincide with DEM samples (avoid interpolating nodata neighbors here).
    b = xyz(x, y, dem[r0:r1, c0:c1]+h_rx)
    distance = np.linalg.norm(b-a, axis=-1)
    # Coincident phase centers have undefined FSPL. Mask that receiver rather
    # than rejecting an otherwise valid map when TX and RX heights match.
    valid = trace['valid'] & np.isfinite(distance) & (distance > 0)
    free_loss = fspl_db(np.where(valid, distance, np.nan), radio['frequency_ghz']*1e9)
    nu = trace['max_knife_edge_nu']
    # A coincident phase center has no meaningful FSPL; refuse rather than invent a floor.
    loss = free_loss + knife_edge_loss_db(nu)
    free_margin = received_power_dbm(free_loss, radio)-radio['receiver_sensitivity_dbm']
    prx = received_power_dbm(loss, radio)
    margin = prx-radio['receiver_sensitivity_dbm']
    out = {k: trace[k] for k in FLOAT_BANDS[:4]}
    out.update(path_loss_db=loss, free_space_margin_db=free_margin,
               received_power_dbm=prx, link_margin_db=margin)
    out = {k: np.where(valid, v, np.nan).astype('float32') for k, v in out.items()}
    classes = classify(valid, trace['los'], out['link_margin_db'],
                       out['free_space_margin_db'], radio['required_margin_db'])
    out.update({k: v.astype('uint8') for k, v in classes.items()})
    out.update(los=trace['los'], valid_mask=valid.astype('uint8'), quality_flags=trace['quality_flag'])
    return out, tr, window


def band_unit(name):
    return 'm' if name.endswith('_m') else 'dBm' if name == 'received_power_dbm' else 'dB' if name.endswith('_db') else '1'


def write_scenario(path, arrays, transform, crs, metadata):
    """Write complete float32 GeoTIFF and matching native uint8 class companion.

    GDAL GeoTIFF cannot mix sample dtypes within one multiband dataset.
    The sidecar declares this storage choice and the companion's shared transform.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    companion = path.with_name(path.stem+'_classes.tif')
    for target, names, dtype, predictor in ((path, BYTE_BANDS+FLOAT_BANDS, 'float32', 3),
                                           (companion, BYTE_BANDS, 'uint8', 2)):
        temporary = target.with_name(target.name+'.tmp')
        with rasterio.open(temporary, 'w', driver='GTiff', height=arrays['los'].shape[0],
                           width=arrays['los'].shape[1], count=len(names), dtype=dtype,
                           crs=crs, transform=transform, compress='deflate', predictor=predictor,
                           tiled=True) as dst:
            for i, name in enumerate(names, 1):
                dst.write(arrays[name].astype(dtype), i)
                dst.set_band_description(i, name)
                dst.set_band_unit(i, band_unit(name))
                dst.update_tags(i, units=band_unit(name), logical_dtype='uint8' if name in BYTE_BANDS else 'float32')
            dst.update_tags(label_origin='simulated', propagation_model='fspl+p526_single_edge (simulated)',
                            quality_codes=str(QUALITY), git_commit=metadata.get('code_commit', 'unknown'))
        temporary.replace(target)
    metadata = dict(metadata, label_origin='simulated', window_transform=list(transform)[:6],
                    shape=list(arrays['los'].shape), class_geotiff=str(companion),
                    storage_note='Complete GeoTIFF float32; class companion uint8 because GeoTIFF requires uniform band dtype.',
                    bands={k: dict(units=band_unit(k), dtype='uint8' if k in BYTE_BANDS else 'float32') for k in BYTE_BANDS+FLOAT_BANDS})
    atomic_json(path.with_suffix('.json'), metadata)
    return companion


def label_scenario(config, output=None):
    """Load a scenario YAML, generate simulated labels and provenance, return paths."""
    cfg = load_experiment(config)
    radio = cfg['radio']
    source = paths.data_root()/cfg['dem']
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=paths.repo_root(),
                            capture_output=True, text=True, check=True).stdout.strip()
    print(f'Git commit {commit}; seed {cfg.get("seed", 42)}; simulated RF labels', flush=True)
    start = perf_counter()
    with rasterio.open(source) as src:
        dem = src.read(1, masked=True).filled(np.nan)
        crs, master = src.crs, src.transform
    if 'tx_x_m' in cfg and 'tx_y_m' in cfg:
        tx = np.array([cfg['tx_x_m'], cfg['tx_y_m']], dtype=float)
    else:
        tx = np.array(Transformer.from_crs(CRS(crs).geodetic_crs, crs, always_xy=True).transform(
            cfg['longitude_deg'], cfg['latitude_deg']))
    arrays, transform, window = label_arrays(tx, cfg['h_tx_m'], cfg['h_rx_m'], dem, master, crs, radio)
    target = Path(output) if output else paths.derived_dir('labels')/cfg['scenario']/(cfg['scenario']+'.tif')
    metadata = dict(radio_profile=radio, radio_profile_name=cfg['radio_profile'], scenario=cfg,
                    propagation_model='fspl+p526_single_edge (simulated)', dem_path=str(source),
                    dem_sha256=sha256_file(source), sample_spacing_m=10, tracer_mode='fast',
                    code_commit=commit, implementation_sha256={name: sha256_file(Path(__file__).with_name(name))
                                                              for name in ('rf.py', 'los.py')},
                    seed=cfg.get('seed', 42), threshold_db=radio['required_margin_db'],
                    tx_xy_m=tx.tolist(), master_transform=list(master)[:6],
                    window=[window.col_off, window.row_off, window.width, window.height])
    companion = write_scenario(target, arrays, transform, crs, metadata)
    metadata['runtime_seconds'] = perf_counter()-start
    import json
    saved = json.loads(target.with_suffix('.json').read_text())
    saved.update(runtime_seconds=metadata['runtime_seconds'])
    atomic_json(target.with_suffix('.json'), saved)
    return target, companion
