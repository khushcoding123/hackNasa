"""App surface coverage, using the evaluated simulated deterministic pipeline.

Threshold-independent bounded memory cache; native-resolution GeoTIFF export.
No model artifacts are searched for or loaded unless approved by evaluation.
"""
from collections import OrderedDict
from functools import lru_cache
import hashlib
import io
import json
import logging
from pathlib import Path
import subprocess
from threading import RLock
from typing import Literal
import zipfile

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from numba import set_num_threads
from pydantic import BaseModel, Field, model_validator
from fastapi import APIRouter, HTTPException

from moonmesh.data import SITES
from lunar_core.terrain import polar_stereographic_south, polar_stereographic_south_inverse
from .config import configs_dir, _load_mapping, load_radio_profile, numba_threads
from .downloads import sha256_file
from .paths import data_root
from .sampling import commit
from . import rf
from .los import R, sample

router = APIRouter(prefix='/api/coverage', tags=['surface coverage'])
LOCK = RLock()
CACHE = OrderedDict()
PROPAGATION = 'fspl+p526_single_edge (simulated)'


class CoverageRequest(BaseModel):
    site: str = 'haworth'
    latitude: float | None = Field(None, ge=-90, le=-85, allow_inf_nan=False)
    longitude: float | None = Field(None, ge=-180, le=360, allow_inf_nan=False)
    h_tx: float = Field(5, ge=0, le=100, allow_inf_nan=False)
    h_rx: float = Field(2, ge=0, le=100, allow_inf_nan=False)
    radio_profile: str = 'moonmesh_2p4_ref'
    threshold: float | None = Field(None, allow_inf_nan=False)
    range_m: float | None = Field(None, gt=0, le=22040, allow_inf_nan=False)
    predictor: Literal['deterministic', 'model'] | None = None
    display_size: int = Field(160, ge=16, le=256)

    @model_validator(mode='after')
    def paired_position(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError('Supply both transmitter coordinates')
        return self


class InspectRequest(CoverageRequest):
    rx_x_m: float = Field(allow_inf_nan=False)
    rx_y_m: float = Field(allow_inf_nan=False)


def code_version():
    """Git commit for provenance, or None (null) when it cannot be read.

    The app must still work from a GitHub "Download ZIP" copy or on a computer without git on PATH;
    training scripts keep using the strict sampling.commit().
    """
    try:
        return commit()
    except (OSError, subprocess.SubprocessError):
        return None


def settings():
    return _load_mapping(configs_dir()/'surface_coverage.yaml')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


@lru_cache(maxsize=32)
def file_hash(path, size, modified):
    return sha256_file(Path(path))


def version(path):
    stat = path.stat()
    return file_hash(str(path), stat.st_size, stat.st_mtime_ns)


def cache_key(*, terrain_version, grid, tx, heights, profile, propagation_model=PROPAGATION,
              implementation=None):
    """Threshold and display choices deliberately do not enter the physical key."""
    radio = {k: v for k, v in profile.items() if k != 'required_margin_db'}
    return digest(dict(terrain=terrain_version, grid=grid, tx=tx, heights=heights,
                       profile_hash=digest(radio), propagation_model=propagation_model,
                       implementation=implementation))


def full_range_on_terrain(src, tx, radius_m):
    """True where the whole range around the transmitter lies on prepared terrain.

    This is the rule that placed every training, validation and test tower (sampling.full_disk_valid):
    no missing-terrain cell, and no raster edge, within radius_m plus half a pixel diagonal of the
    transmitter's cell. Those towers span the prepared Haworth terrain, not only the area around the
    research point, so this, not the lander deployment radius, is where the learned model was evaluated.
    """
    pixel = abs(src.transform.a)
    reach = radius_m + pixel/np.sqrt(2)
    row, col = src.index(*tx)
    # Outside the raster counts as missing terrain, starting one cell beyond each edge.
    if min(row+1, src.height-row, col+1, src.width-col)*pixel <= reach:
        return False
    n = int(reach//pixel)
    block = src.read(1, window=rasterio.windows.Window(col-n, row-n, 2*n+1, 2*n+1), masked=True).filled(np.nan)
    rr, cc = np.ogrid[-n:n+1, -n:n+1]
    return bool(np.isfinite(block[np.hypot(rr, cc)*pixel <= reach]).all())


def prepare(body):
    cfg = settings()
    try:
        radio = load_radio_profile(body.radio_profile)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    threshold = radio['required_margin_db'] if body.threshold is None else body.threshold
    if body.range_m is not None:
        radio['max_range_m'] = body.range_m
    pilot = _load_mapping(configs_dir()/cfg['experiment_config'])
    reasons = []
    in_domain = (body.site == pilot['site'] and min(pilot['tx_heights_m']) <= body.h_tx <= max(pilot['tx_heights_m'])
                 and body.h_rx == pilot['rx_height_m'] and radio['max_range_m'] <= pilot['max_range_m']
                 and body.radio_profile == pilot['radio_profile'])
    if not in_domain:
        reasons.append('Outside evaluated site, antenna heights, radio profile or range; deterministic fallback.')
    decision_path = data_root()/cfg['decision']
    decision = json.loads(decision_path.read_text()) if decision_path.exists() else {'ship': 'deterministic'}
    requested = body.predictor or ('model' if decision.get('ship') != 'deterministic' else 'deterministic')
    # The final decision in this release rejects the learned model. Never expose
    # an unapproved surrogate through an explicit client predictor override.
    if requested == 'model':
        reasons.append('Learned predictor unavailable: final decision ships deterministic.' if decision.get('ship') == 'deterministic'
                       else 'Approved learned predictor adapter unavailable; deterministic fallback.')
    meta = dict(label_origin='simulated', predictor='deterministic', requested_predictor=requested,
                propagation_model=PROPAGATION, model_version='deterministic-fast-10m',
                in_evaluated_domain=in_domain, fallback_reasons=reasons, threshold_db=threshold,
                h_tx_m=body.h_tx, h_rx_m=body.h_rx, radio_profile=body.radio_profile, radio=radio,
                evaluation_decision=decision, seed=cfg['seed'])
    if body.site not in SITES:
        raise HTTPException(422, 'Unknown site')
    site_cfg = cfg['sites'].get(body.site)
    if site_cfg is None:
        return None, dict(meta, state='missing_data', message='No integrated terrain for this site. Select Haworth.')
    dem_path, slope_path, grid_path = [data_root()/site_cfg[k] for k in ('dem', 'slope', 'grid')]
    if not all(p.exists() for p in (dem_path, slope_path, grid_path)):
        return None, dict(meta, state='missing_data', message=f'Prepared DEM, slope or grid is missing under {data_root()}. '
                          'Unpack shared-data (see shared-data/README.md) and point MOONMESH_DATA_ROOT at that folder.')
    lat = SITES[body.site][1] if body.latitude is None else body.latitude
    lon = SITES[body.site][2] if body.longitude is None else body.longitude
    tx = list(map(float, polar_stereographic_south(lat, lon)))
    with rasterio.open(dem_path) as src:
        grid = dict(transform=list(src.transform)[:6], shape=[src.height, src.width], crs=src.crs.to_wkt())
        if not (src.bounds.left <= tx[0] <= src.bounds.right and src.bounds.bottom <= tx[1] <= src.bounds.top):
            return None, dict(meta, state='out_of_domain', message='Transmitter is outside prepared terrain.')
        if not full_range_on_terrain(src, tx, pilot['max_range_m']):
            meta['in_evaluated_domain'] = False
            reasons.append(f'Part of the {pilot["max_range_m"]/1000:g} km range around this transmitter is off the prepared '
                           'terrain, where no training or test tower was placed; deterministic fallback.')
    implementations = {name: version(Path(__file__).with_name(name)) for name in ('coverage.py', 'rf.py', 'los.py')}
    hashes = {str(p): version(p) for p in (dem_path, slope_path, grid_path)}
    key = cache_key(terrain_version=hashes, grid=grid, tx=tx, heights=[body.h_tx, body.h_rx],
                    profile=radio, implementation=implementations)
    meta.update(cache_key=key, tx_xy_m=tx, tx_latitude=lat, tx_longitude=lon,
                terrain_source=site_cfg['source'], resolution_m=abs(grid['transform'][0]),
                sources=hashes, implementation_sha256=implementations,
                vertical_reference=json.loads(grid_path.read_text()).get('vertical_reference'),
                assumptions=['Simulated straight spherical chord LOS, strict bilinear validity, 10 m samples.',
                             'FSPL plus ITU P.526 single-edge diffraction; no measured lunar RF validation.',
                             'Unknown cells are excluded from covered and dead classes.',
                             'TODO: PGDA vertical reference is undocumented in supplied metadata.'])
    return (cfg, radio, tx, dem_path, slope_path), meta


def calculate(body):
    context, meta = prepare(body)
    if context is None:
        return None, meta
    cfg, radio, tx, dem_path, slope_path = context
    with LOCK:
        key = meta['cache_key']
        hit = key in CACHE
        if hit:
            entry = CACHE.pop(key)
        else:
            set_num_threads(numba_threads(cfg['numba_threads']))
            with rasterio.open(dem_path) as src:
                dem = src.read(1, masked=True).filled(np.nan)
                arrays, tr, window = rf.label_arrays(tx, body.h_tx, body.h_rx, dem, src.transform, src.crs, radio)
                elevation = src.read(1, window=window, masked=True).filled(np.nan)
                crs = src.crs
            with rasterio.open(slope_path) as src:
                slope = src.read(1, window=window, masked=True).filled(np.nan)
            arrays.update(elevation=elevation, slope=slope)
            # Coincident phase centers or numerical RF failures are unknown.
            arrays['valid_mask'] &= np.isfinite(arrays['link_margin_db']).astype('uint8')
            entry = dict(arrays=arrays, transform=tr, crs=crs)
        CACHE[key] = entry
        while len(CACHE) > cfg['cache_entries']:
            CACHE.popitem(last=False)
    meta.update(cache_hit=hit, code_version=code_version())
    logging.getLogger(__name__).info('Surface coverage simulated; commit %s; key %s', meta['code_version'], key)
    valid = entry['arrays']['valid_mask'].astype(bool)
    covered = valid & (entry['arrays']['link_margin_db'] >= meta['threshold_db'])
    meta['state'] = ('no_valid_pixels' if not valid.any() else 'no_link' if not covered.any()
                     else 'out_of_domain' if not meta['in_evaluated_domain'] else 'ready')
    meta.update(valid_pixels=int(valid.sum()), covered_pixels=int(covered.sum()))
    return entry, meta


def layers(entry, threshold):
    a = entry['arrays']
    valid = a['valid_mask'].astype(bool)
    return dict(elevation=a['elevation'], slope=a['slope'], los=np.where(valid, a['los'], 255).astype('uint8'),
                margin=np.where(valid, a['link_margin_db'], np.nan), valid=valid.astype('uint8'),
                **{'class': np.where(valid, np.where(a['link_margin_db'] >= threshold, 1, 0), 255).astype('uint8')})


def json_array(a):
    return np.where(np.isfinite(a), a, None).tolist()


def coverage(body):
    entry, meta = calculate(body)
    if entry is None:
        return dict(metadata=meta, layers={})
    a = layers(entry, meta['threshold_db'])
    height, width = a['valid'].shape
    stride = max(1, int(np.ceil(max(height, width)/body.display_size)))
    tr = entry['transform']
    # Display cells are center samples of the native lattice, never aggregates.
    display_tr = tr @ rasterio.Affine.translation((1-stride)/2, (1-stride)/2) @ rasterio.Affine.scale(stride)
    meta.update(native_shape=[height, width], display_stride=stride,
                display_transform=list(display_tr)[:6], crs=entry['crs'].to_wkt(),
                units=dict(elevation='m', slope='degrees', los='1', margin='dB', valid='1', **{'class': '1'}),
                classes={'covered': 1, 'below_margin': 0, 'unknown': 255})
    return dict(metadata=meta, layers={k: json_array(v[::stride, ::stride]) for k, v in a.items()})


@router.post('')
def route(body: CoverageRequest):
    try:
        return coverage(body)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(422, str(error)) from error


@router.post('/export')
def export_route(body: CoverageRequest):
    from fastapi.responses import Response
    entry, meta = calculate(body)
    if entry is None:
        raise HTTPException(409, meta)
    payload = io.BytesIO()
    out = layers(entry, meta['threshold_db'])
    meta.update(masks={'valid': '1=known, 0=unknown; includes profile completeness and range',
                       'class': '1=covered, 0=below margin, 255=unknown', 'los': '1=clear, 0=occluded, 255=unknown'},
                exports={}, crs=entry['crs'].to_wkt(), transform=list(entry['transform'])[:6])
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, a in out.items():
            unit = 'm' if name == 'elevation' else 'degrees' if name == 'slope' else 'dB' if name == 'margin' else '1'
            nodata = 255 if name in ('los', 'class') else None if name == 'valid' else float('nan')
            with MemoryFile() as memory:
                with memory.open(driver='GTiff', height=a.shape[0], width=a.shape[1], count=1,
                                 dtype=a.dtype, crs=entry['crs'], transform=entry['transform'], nodata=nodata,
                                 compress='deflate') as dst:
                    dst.write(a, 1)
                    dst.set_band_description(1, name)
                    dst.update_tags(label_origin='simulated', units=unit, code_version=meta['code_version'] or 'null')
                content = memory.read()
            filename = name+'.tif'
            archive.writestr(filename, content)
            meta['exports'][filename] = dict(sha256=hashlib.sha256(content).hexdigest(), units=unit,
                                             nodata='NaN' if isinstance(nodata, float) else nodata)
        archive.writestr('scenario.json', json.dumps(meta, indent=2, allow_nan=False))
    return Response(payload.getvalue(), media_type='application/zip',
                    headers={'Content-Disposition': 'attachment; filename="surface-coverage.zip"'})


@router.post('/inspect')
def inspect_route(body: InspectRequest):
    context, meta = prepare(body)
    if context is None:
        return dict(metadata=meta, profile=[])
    cfg, radio, tx, dem_path, _ = context
    with rasterio.open(dem_path) as src:
        dem = src.read(1, masked=True).filled(np.nan)
        tr, crs = src.transform, src.crs
    rx = [body.rx_x_m, body.rx_y_m]
    set_num_threads(numba_threads(cfg['numba_threads']))
    trace = rf.trace_pairs(tx, [rx], body.h_tx, body.h_rx, dem, tr, crs,
                           radio['max_range_m'], 10, rf.C/(radio['frequency_ghz']*1e9), mode='fast')
    valid = bool(trace['valid'][0])
    lat, lon = polar_stereographic_south_inverse(*rx)
    result = dict(metadata=meta, latitude=float(lat), longitude=float(lon), profile=[],
                  state='unknown', margin_db=None, los=None)
    if not valid:
        return result
    t = np.asarray(tuple(tr)[:6])
    a = rf.xyz(*tx, sample(dem, *tx, t)+body.h_tx)
    b = rf.xyz(*rx, sample(dem, *rx, t)+body.h_rx)
    distance = float(np.linalg.norm(b-a))
    if distance <= 0:
        return result
    loss = rf.fspl_db(distance, radio['frequency_ghz']*1e9)+rf.knife_edge_loss_db(trace['max_knife_edge_nu'][0])
    margin = float(rf.received_power_dbm(loss, radio)-radio['receiver_sensitivity_dbm'])
    if not np.isfinite(margin):
        return result
    f = np.linspace(0, 1, max(2, int(np.ceil(distance/10))+1))
    p = a+(b-a)*f[:, None]
    radii = np.linalg.norm(p, axis=1)
    x, y = 2*R*p[:, 1]/(radii-p[:, 2]), 2*R*p[:, 0]/(radii-p[:, 2])
    terrain = np.array([sample(dem, px, py, t) for px, py in zip(x, y)])
    result.update(state='covered' if margin >= meta['threshold_db'] else 'below_margin',
                  margin_db=margin, los=int(trace['los'][0]),
                  min_clearance_m=float(trace['min_clearance_m'][0]),
                  obstruction_distance_m=float(trace['obstruction_distance_m'][0]),
                  profile=[dict(distance_m=float(d), terrain_m=float(z) if np.isfinite(z) else None,
                                ray_m=float(ray)) for d, z, ray in zip(f*distance, terrain, radii-R)])
    return result
