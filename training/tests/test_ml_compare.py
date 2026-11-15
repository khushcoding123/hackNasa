"""Experimental ML comparison route: scores, layers and refusals, with a stand-in for the trained model."""
import json
import time

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from lunar_core.terrain import polar_stereographic_south
from moonmesh.server import app
from moonmesh_train import coverage as c
from moonmesh_train import ml_compare as m
from moonmesh_train.los import PROJECTION


class FakeModel:
    """Copies the physics arrays from the coverage cache, optionally forcing every pixel to 'covered'."""
    def __init__(self, everything_covered=False):
        self.everything_covered = everything_covered
        self.rows = []

    def predict(self, row, tr, shape, predictor):
        assert predictor == 'lightgbm'
        self.rows.append(row)
        a = next(iter(c.CACHE.values()))['arrays']
        assert a['valid_mask'].shape == shape
        covered = np.ones(shape, bool) if self.everything_covered else a['link_margin_db'] >= row.required_margin_db
        return dict(los=a['los'] == 1, margin=a['link_margin_db'].astype('float32'), covered=covered,
                    valid=a['valid_mask'].astype(bool), clearance=np.zeros(shape, 'float32'))


@pytest.fixture
def terrain(tmp_path, monkeypatch):
    monkeypatch.setenv('MOONMESH_DATA_ROOT', str(tmp_path))
    x, y = polar_stereographic_south(-86.8, 337.2)
    tr = from_origin(x-400, y+400, 20, 20)
    for name in ('dem', 'slope'):
        with rasterio.open(tmp_path/(name+'.tif'), 'w', driver='GTiff', width=40, height=40,
                           count=1, dtype='float32', crs=PROJECTION, transform=tr, nodata=-9999) as dst:
            dst.write(np.zeros((40, 40), dtype='float32'), 1)
    (tmp_path/'grid.json').write_text(json.dumps({'vertical_reference': None}))
    (tmp_path/'decision.json').write_text(json.dumps({'ship': 'deterministic', 'model': None}))
    cfg = dict(seed=42, decision='decision.json', experiment_config='exp_b_haworth_pilot.yaml',
               numba_threads=2, cache_entries=2,
               sites={'haworth': dict(dem='dem.tif', slope='slope.tif', grid='grid.json', source='analytic fixture')})
    monkeypatch.setattr(c, 'settings', lambda: cfg)
    c.CACHE.clear()
    yield TestClient(app), monkeypatch
    c.CACHE.clear()


def use_model(monkeypatch, model):
    monkeypatch.setattr(m, 'models_available', lambda: True)
    monkeypatch.setattr(m, 'learned_context', lambda: model)


def test_matching_model_scores_perfect_and_layers_line_up(terrain):
    client, monkeypatch = terrain
    model = FakeModel()
    use_model(monkeypatch, model)
    body = dict(range_m=300)
    physics = client.post('/api/coverage', json=body).json()
    response = client.post('/api/coverage/ml-compare', json=body)
    assert response.status_code == 200
    result = response.json()
    meta, scores, layers = result['metadata'], result['scores'], result['layers']
    assert meta['experimental'] and not meta['approved_for_planning']
    assert meta['label_origin'] == 'simulated' and meta['physics_cache_hit']
    assert scores['coverage_agreement'] == scores['los_agreement'] == 1.0
    assert scores['margin_mae_db'] < 1e-3
    assert scores['false_covered_km2'] == 0
    assert scores['valid_pixels'] == physics['metadata']['valid_pixels'] > 0
    assert layers['physics_class'] == layers['ml_class'] == physics['layers']['class']
    diff = np.asarray(layers['disagreement'])
    valid = np.asarray(physics['layers']['valid'], bool)
    assert np.all(diff[valid] == 0) and np.all(diff[~valid] == 255)
    assert meta['display_transform'] == physics['metadata']['display_transform']
    row = model.rows[0]
    assert [row.transmitter_x_m, row.transmitter_y_m] == physics['metadata']['tx_xy_m']
    assert (row.tx_height_agl_m, row.rx_height_agl_m, row.max_range_m) == (5, 2, 300)


def test_optimistic_model_is_flagged_as_false_covered(terrain):
    client, monkeypatch = terrain
    model = FakeModel(everything_covered=True)
    use_model(monkeypatch, model)
    result = client.post('/api/coverage/ml-compare', json=dict(range_m=300, threshold=200)).json()
    assert model.rows[0].required_margin_db == 200
    scores = result['scores']
    assert scores['physics_covered_km2'] == 0 and scores['model_covered_km2'] > 0
    assert scores['false_covered_fraction'] == 1.0 and scores['dead_zones_caught'] == 0.0
    assert scores['coverage_agreement'] == 0.0
    diff = np.asarray(result['layers']['disagreement'])
    assert set(np.unique(diff)) <= {1, 255} and np.any(diff == 1)


def test_outside_training_domain_is_refused(terrain):
    client, monkeypatch = terrain
    model = FakeModel()
    use_model(monkeypatch, model)
    response = client.post('/api/coverage/ml-compare', json=dict(range_m=300, h_tx=3))
    assert response.status_code == 422
    assert 'only covers what it was trained on' in response.json()['detail']
    assert model.rows == []


def test_missing_model_files_are_reported(terrain):
    client, _ = terrain
    response = client.post('/api/coverage/ml-compare', json=dict(range_m=300))
    assert response.status_code == 503
    assert 'kept outside git' in response.json()['detail']


def wait_for(client, job_id):
    for _ in range(600):
        job = client.get(f'/api/coverage/ml-compare/jobs/{job_id}').json()
        if job['status'] != 'running':
            return job
        time.sleep(0.05)
    raise AssertionError('job did not finish')


def test_background_job_matches_direct_comparison(terrain):
    client, monkeypatch = terrain
    m.JOBS.clear()
    use_model(monkeypatch, FakeModel())
    body = dict(range_m=300)
    started = client.post('/api/coverage/ml-compare/jobs', json=body)
    assert started.status_code == 200
    job = wait_for(client, started.json()['job_id'])
    assert job['status'] == 'done'
    direct = client.post('/api/coverage/ml-compare', json=body).json()
    assert job['result']['scores'] == direct['scores']
    assert job['result']['layers'] == direct['layers']
    again = client.post('/api/coverage/ml-compare/jobs', json=body).json()
    assert again['job_id'] == started.json()['job_id'] and again['status'] == 'done'


def test_background_job_refuses_fast_and_reports_failures(terrain):
    client, monkeypatch = terrain
    m.JOBS.clear()
    assert client.post('/api/coverage/ml-compare/jobs', json=dict(range_m=300)).status_code == 503
    use_model(monkeypatch, FakeModel())
    assert client.post('/api/coverage/ml-compare/jobs', json=dict(range_m=300, h_rx=5)).status_code == 422
    assert m.JOBS == {}
    assert client.get('/api/coverage/ml-compare/jobs/unknown').status_code == 404

    class Broken(FakeModel):
        def predict(self, *args):
            raise RuntimeError('boom')
    use_model(monkeypatch, Broken())
    job = wait_for(client, client.post('/api/coverage/ml-compare/jobs', json=dict(range_m=300)).json()['job_id'])
    assert job['status'] == 'error' and job['code'] == 500 and 'boom' in job['detail']


def test_newer_job_supersedes_waiting_one(terrain):
    client, monkeypatch = terrain
    m.JOBS.clear()
    use_model(monkeypatch, FakeModel())
    with m.MODEL_LOCK:  # hold the model so the first job is still waiting when the second arrives
        first = client.post('/api/coverage/ml-compare/jobs', json=dict(range_m=300)).json()['job_id']
        second = client.post('/api/coverage/ml-compare/jobs', json=dict(range_m=300, threshold=10)).json()['job_id']
    assert wait_for(client, first)['detail'] == 'A newer comparison replaced this one.'
    assert wait_for(client, second)['status'] == 'done'


def test_main_coverage_route_still_ships_deterministic(terrain):
    client, monkeypatch = terrain
    use_model(monkeypatch, FakeModel())
    result = client.post('/api/coverage', json=dict(range_m=300, predictor='model')).json()
    assert result['metadata']['predictor'] == 'deterministic'
