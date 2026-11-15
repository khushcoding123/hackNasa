"""The real trained LightGBM files, run through the app's own routes, with no stand-ins.

test_ml_compare.py checks the comparison plumbing with a fake model; this file checks that the shipped
model files load, read the features they were trained on, and give the published results inside the app.
It needs the unpacked shared data (python shared-data/unpack.py, see shared-data/README.md) and skips
with that instruction when it is absent. All results are simulated.
"""
import json
import time

import numpy as np
import pandas as pd
import pytest
import rasterio
from fastapi.testclient import TestClient

from lunar_core.terrain import polar_stereographic_south, polar_stereographic_south_inverse
from moonmesh.server import app
from moonmesh_train import coverage as c
from moonmesh_train import ml_compare as m
from moonmesh_train.config import _load_mapping, configs_dir
from moonmesh_train.paths import data_root, repo_root

PUBLISHED = 'haworth_tx_045_tx5_rx2'  # a held-out test scenario with published per-scenario metrics


def shared_data_present():
    try:
        site = c.settings()['sites']['haworth']
        return m.models_available() and all((data_root()/site[k]).is_file() for k in ('dem', 'slope', 'grid'))
    except (OSError, KeyError, ValueError):
        return False


pytestmark = pytest.mark.skipif(not shared_data_present(), reason=(
    'needs the trained model and Haworth terrain: run python shared-data/unpack.py and set '
    'MOONMESH_DATA_ROOT if you unpacked somewhere other than hackNasa/data (see shared-data/README.md)'))


@pytest.fixture(scope='module')
def client():
    m.learned_context.cache_clear()
    c.CACHE.clear()
    yield TestClient(app)
    c.CACHE.clear()
    m.learned_context.cache_clear()


def published_lightgbm_rf(scenario_id):
    path = data_root()/'reports/eval/final'/f'{scenario_id}_metrics.csv'
    if not path.is_file():
        pytest.skip(f'{path} is missing from the unpacked shared data')
    rows = pd.read_csv(path)
    return rows[(rows.predictor == 'lightgbm') & (rows.dead_zone == 'rf') & (rows.group == 'all')].iloc[0]


def test_the_shipped_boosters_are_loaded_with_their_training_features(client):
    meta = json.loads((m.model_dir()/'metadata.json').read_text())
    ctx = m.learned_context()
    assert set(ctx.models) == {'los', 'margin'}  # clearance is not used by the comparison
    for name, booster in ctx.models.items():
        assert booster.current_iteration() == meta['best_iterations'][name]
        assert booster.feature_name() == meta['feature_columns']


def test_every_training_validation_and_test_tower_is_inside_the_model_domain():
    pilot = _load_mapping(configs_dir()/'exp_b_haworth_pilot.yaml')
    splits = json.loads((repo_root()/'training/splits.json').read_text())
    used = {tid for ids in splits['splits'].values() for tid in ids}
    with rasterio.open(data_root()/c.settings()['sites']['haworth']['dem']) as src:
        towers = [p for p in splits['transmitters'] if p['transmitter_id'] in used]
        assert len(towers) == 147
        assert all(c.full_range_on_terrain(src, (p['x_m'], p['y_m']), pilot['max_range_m']) for p in towers)
        assert c.full_range_on_terrain(src, polar_stereographic_south(pilot['latitude_deg'], pilot['longitude_deg']),
                                       pilot['max_range_m'])
        # A transmitter 2 km inside the terrain edge cannot have its whole 8 km range on terrain.
        edge = (src.bounds.left+2000, (src.bounds.bottom+src.bounds.top)/2)
        assert not c.full_range_on_terrain(src, edge, pilot['max_range_m'])


def test_the_app_job_runs_the_real_model_at_the_research_point(client):
    body = dict(site='haworth', latitude=-86.8, longitude=337.2, h_tx=5, h_rx=2, range_m=3000)
    physics = client.post('/api/coverage', json=body).json()['metadata']
    assert physics['state'] == 'ready' and physics['in_evaluated_domain']
    started = client.post('/api/coverage/ml-compare/jobs', json=body)
    assert started.status_code == 200, started.text
    job_id = started.json()['job_id']
    for _ in range(1200):
        job = client.get(f'/api/coverage/ml-compare/jobs/{job_id}').json()
        if job['status'] != 'running':
            break
        time.sleep(0.25)
    assert job['status'] == 'done', job
    result = job['result']
    assert result['metadata']['experimental'] and not result['metadata']['approved_for_planning']
    scores = result['scores']
    assert scores['valid_pixels'] == physics['valid_pixels']
    assert scores['coverage_agreement'] > 0.98 and scores['los_agreement'] > 0.98
    assert scores['margin_mae_db'] < 1.0
    assert scores['physics_dead_km2'] > 0
    shape = np.asarray(result['layers']['ml_class']).shape
    assert shape == np.asarray(result['layers']['physics_class']).shape and min(shape) > 100


def test_the_app_reproduces_a_published_test_scenario(client):
    """Full 8 km map (about a minute on 4 cores). This rechecks numbers already published in
    reports/eval/final; nothing is tuned on the test split."""
    expected = published_lightgbm_rf(PUBLISHED)
    frame = pd.read_parquet(data_root()/'derived/datasets/haworth_pilot/scenarios.parquet')
    row = frame[frame.scenario_id == PUBLISHED].iloc[0]
    assert row.split == 'test'
    lat, lon = polar_stereographic_south_inverse(row.transmitter_x_m, row.transmitter_y_m)
    body = dict(site='haworth', latitude=float(lat), longitude=float(lon) % 360, h_tx=float(row.tx_height_agl_m),
                h_rx=float(row.rx_height_agl_m), range_m=float(row.max_range_m))
    response = client.post('/api/coverage/ml-compare', json=body)
    assert response.status_code == 200, response.text
    scores = response.json()['scores']
    # The app counts pixels; the evaluation weights them by polar-stereographic area, which is
    # constant to about 1e-5 across one 16 km map, so the two agree far inside these tolerances.
    assert scores['dead_zones_caught'] == pytest.approx(expected.recall, abs=2e-4)
    assert scores['false_covered_fraction'] == pytest.approx(expected.false_covered_frac, abs=2e-4)
    assert scores['margin_mae_db'] == pytest.approx(expected.margin_mae_db, abs=2e-3)
