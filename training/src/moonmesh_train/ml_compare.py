"""Experimental side-by-side comparison of the trained LightGBM surrogate with the physics map.

The surrogate is NOT approved for planning: the final evaluation (reports/eval/final/decision.json)
ships the deterministic map, and the main coverage route never uses the model. This separate route
exists so the learned model can be demonstrated and inspected next to the physics map it imitates.
All results are simulated.
"""
from collections import OrderedDict
from functools import lru_cache
import logging
from threading import Lock, Thread
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import rasterio
import yaml
from fastapi import APIRouter, HTTPException

from . import coverage as cov
from .config import configs_dir
from .paths import data_root

router = APIRouter(prefix='/api/coverage', tags=['surface coverage (experimental)'])
MODEL_LOCK = Lock()
MODEL_NAMES = ('los', 'margin', 'clearance')
EVAL_CONFIG = 'eval_haworth.yaml'
# A full 8 km map takes minutes, longer than a browser should hold one request open,
# so the app starts a background job and polls it. Only the newest job runs the model.
JOBS = OrderedDict()
JOBS_LOCK = Lock()
MAX_JOBS = 4


class Superseded(Exception):
    pass


def eval_config():
    return yaml.safe_load((configs_dir()/EVAL_CONFIG).read_text())


def model_dir():
    return data_root()/eval_config()['models']


def models_available():
    """The boosters, their metadata and the dataset manifest live under the data root, never in git."""
    cfg = eval_config()
    folder = data_root()/cfg['models']
    needed = [folder/'metadata.json', data_root()/cfg['dataset']/'manifest.json']
    needed += [folder/f'{n}.txt' for n in MODEL_NAMES]
    return all(p.is_file() for p in needed)


@lru_cache(maxsize=1)
def learned_context():
    """Load terrain features and the three LightGBM boosters once per server process."""
    from .evaluation import Context  # heavy imports (lightgbm, pandas) only when this route is used
    return Context(eval_config())


def refuse(entry_missing, meta):
    """Fast refusals, shared by the job start (before any heavy work) and the comparison itself."""
    if entry_missing:
        raise HTTPException(409, meta.get('message', 'Surface coverage is unavailable for this request.'))
    if not meta['in_evaluated_domain']:
        raise HTTPException(422, 'The experimental model only covers what it was trained on: Haworth, '
                                 'the MOONMESH 2.4 GHz profile, TX 5-50 m, RX 2 m, range up to 8 km, '
                                 'within 8 km of the research point. ' + ' '.join(meta['fallback_reasons']))
    if not models_available():
        raise HTTPException(503, f'Trained model files were not found under {data_root()}. They are created by '
                                 'training steps 9 and 10 and are kept outside git, so this only works on a '
                                 'computer that ran the training.')


def compare(body, should_stop=lambda: False):
    t0 = perf_counter()
    entry, meta = cov.calculate(body)
    physics_s = perf_counter()-t0
    refuse(entry is None, meta)
    a = entry['arrays']
    tr = entry['transform']
    shape = a['valid_mask'].shape
    radio = meta['radio']
    threshold = meta['threshold_db']
    row = SimpleNamespace(transmitter_x_m=meta['tx_xy_m'][0], transmitter_y_m=meta['tx_xy_m'][1],
                          tx_height_agl_m=body.h_tx, rx_height_agl_m=body.h_rx,
                          frequency_ghz=radio['frequency_ghz'], max_range_m=radio['max_range_m'],
                          required_margin_db=threshold)
    with MODEL_LOCK:
        if should_stop():
            raise Superseded
        ctx = learned_context()
        t0 = perf_counter()
        pred = ctx.predict(row, tr, shape, 'lightgbm')
        model_s = perf_counter()-t0

    valid = a['valid_mask'].astype(bool) & np.asarray(pred['valid'], dtype=bool)
    truth_cov = valid & (a['link_margin_db'] >= threshold)
    model_cov = valid & np.asarray(pred['covered'], dtype=bool)
    truth_los = a['los'] == 1
    model_los = np.asarray(pred['los'], dtype=bool)
    dead = valid & ~truth_cov
    pixel_km2 = abs(tr.a*tr.e)/1e6
    n = int(valid.sum())
    if n == 0:
        raise HTTPException(409, 'No pixels are valid for both the physics map and the model here.')
    margin_error = np.abs(np.asarray(pred['margin'], dtype=float)-a['link_margin_db'])[valid]
    scores = dict(
        valid_pixels=n,
        coverage_agreement=float((truth_cov == model_cov)[valid].mean()),
        los_agreement=float((truth_los == model_los)[valid].mean()),
        physics_covered_km2=float(truth_cov.sum()*pixel_km2),
        model_covered_km2=float(model_cov.sum()*pixel_km2),
        dead_zones_caught=float((dead & ~model_cov).sum()/dead.sum()) if dead.any() else None,
        false_covered_fraction=float((dead & model_cov).sum()/dead.sum()) if dead.any() else None,
        false_covered_km2=float((dead & model_cov).sum()*pixel_km2),
        margin_mae_db=float(margin_error.mean()),
    )
    layers = dict(
        physics_class=np.where(valid, truth_cov, 255).astype('uint8'),
        ml_class=np.where(valid, model_cov, 255).astype('uint8'),
        # 0 agree, 1 model says covered but physics says dead, 2 model says dead but physics says covered
        disagreement=np.where(valid, np.where(truth_cov == model_cov, 0, np.where(model_cov, 1, 2)), 255).astype('uint8'),
    )
    height, width = shape
    stride = max(1, int(np.ceil(max(height, width)/body.display_size)))
    display_tr = tr @ rasterio.Affine.translation((1-stride)/2, (1-stride)/2) @ rasterio.Affine.scale(stride)
    metadata = dict(
        label_origin='simulated', experimental=True, approved_for_planning=False,
        model='LightGBM surrogate (training step 10)', model_dir=str(model_dir()),
        evaluation_decision=meta.get('evaluation_decision'),
        threshold_db=threshold, h_tx_m=body.h_tx, h_rx_m=body.h_rx,
        tx_latitude=meta['tx_latitude'], tx_longitude=meta['tx_longitude'],
        physics_seconds=round(physics_s, 2), physics_cache_hit=meta.get('cache_hit', False),
        model_seconds=round(model_s, 2), display_stride=stride,
        display_transform=list(display_tr)[:6], native_shape=[height, width],
        classes={'covered': 1, 'below_margin': 0, 'unknown': 255},
        disagreement_classes={'agree': 0, 'model_covered_physics_dead': 1, 'model_dead_physics_covered': 2,
                              'unknown': 255},
    )
    return dict(metadata=metadata, scores=scores,
                layers={k: cov.json_array(v[::stride, ::stride]) for k, v in layers.items()})


@router.post('/ml-compare')
def ml_compare_route(body: cov.CoverageRequest):
    try:
        return compare(body)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(422, str(error)) from error


def run_job(job, body):
    try:
        job['result'] = compare(body, should_stop=lambda: job['superseded'])
        job['status'] = 'done'
    except Superseded:
        job.update(status='error', code=409, detail='A newer comparison replaced this one.')
    except HTTPException as error:
        job.update(status='error', code=error.status_code, detail=error.detail)
    except (ValueError, FileNotFoundError) as error:
        job.update(status='error', code=422, detail=str(error))
    except Exception as error:  # report, do not lose, unexpected failures in the background thread
        logging.getLogger(__name__).exception('Experimental ML comparison failed')
        job.update(status='error', code=500, detail=f'{type(error).__name__}: {error}')
    finally:
        job['seconds'] = round(perf_counter()-job['started'], 1)


@router.post('/ml-compare/jobs')
def start_job(body: cov.CoverageRequest):
    """Refuse fast, then run the comparison in the background; poll the returned job id."""
    try:
        context, meta = cov.prepare(body)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(422, str(error)) from error
    refuse(context is None, meta)
    key = body.model_dump_json()
    with JOBS_LOCK:
        for job_id, job in JOBS.items():
            if job['key'] == key and job['status'] in ('running', 'done'):
                return dict(job_id=job_id, status=job['status'])  # same request again: reuse it
        for job in JOBS.values():
            job['superseded'] = True  # only the newest request waits for the model
        job_id = uuid4().hex
        job = dict(key=key, status='running', superseded=False, started=perf_counter())
        JOBS[job_id] = job
        while len(JOBS) > MAX_JOBS:
            JOBS.popitem(last=False)
    Thread(target=run_job, args=(job, body), daemon=True, name='ml-compare-'+job_id[:8]).start()
    return dict(job_id=job_id, status='running')


@router.get('/ml-compare/jobs/{job_id}')
def job_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, 'Unknown or expired comparison job; start the comparison again.')
    seconds = job.get('seconds', round(perf_counter()-job['started'], 1))
    out = dict(status=job['status'], seconds=seconds)
    if job['status'] == 'done':
        out['result'] = job['result']
    elif job['status'] == 'error':
        out.update(code=job['code'], detail=job['detail'])
    return out
