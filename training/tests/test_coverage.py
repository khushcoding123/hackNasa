"""Route, physical-cache and native export contracts on analytic terrain."""
import hashlib
import io
import json
from unittest.mock import patch
import zipfile

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from lunar_core.terrain import polar_stereographic_south
from moonmesh.server import app
from moonmesh_train import coverage as c
from moonmesh_train.config import load_radio_profile
from moonmesh_train.los import PROJECTION
from moonmesh_train.paths import repo_root

REAL_DOMAIN_RULE = c.full_range_on_terrain  # captured before any fixture patches it


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
    # The 800 m analytic DEM cannot hold the real 8 km model domain; apply the same rule at 300 m.
    real_rule = c.full_range_on_terrain
    monkeypatch.setattr(c, 'full_range_on_terrain', lambda src, tx, radius_m: real_rule(src, tx, 300))
    c.CACHE.clear()
    yield TestClient(app), tmp_path
    c.CACHE.clear()


def test_key_invalidation():
    profile = load_radio_profile('moonmesh_2p4_ref')
    params = dict(terrain_version='sha', grid={'crs': PROJECTION, 'shape': [40, 40]},
                  tx=[1, 2], heights=[5, 2], profile=profile)
    key = c.cache_key(**params)
    assert c.cache_key(**dict(params, profile={**profile, 'required_margin_db': 99})) == key
    for change in (dict(terrain_version='new'), dict(grid={'crs': 'changed'}), dict(tx=[2, 2]),
                   dict(heights=[10, 2]), dict(heights=[5, 3]),
                   dict(profile={**profile, 'tx_power_dbm': 40}),
                   dict(profile={**profile, 'max_range_m': 4000}),
                   dict(propagation_model='other'), dict(implementation='new')):
        assert c.cache_key(**{**params, **change}) != key


def test_route_cache_threshold_reclassification(terrain):
    client, _ = terrain
    body = dict(range_m=300)
    first = client.post('/api/coverage', json=body)
    assert first.status_code == 200
    a = first.json()
    assert a['metadata']['valid_pixels'] > 0
    assert a['metadata']['predictor'] == 'deterministic'
    assert set(a['layers']) == {'los', 'margin', 'class', 'valid', 'elevation', 'slope'}
    with patch.object(c.rf, 'label_arrays', side_effect=AssertionError('cache recomputed')):
        b = client.post('/api/coverage', json={**body, 'threshold': 200}).json()
    assert b['metadata']['cache_hit']
    assert a['metadata']['cache_key'] == b['metadata']['cache_key']
    assert a['layers']['margin'] == b['layers']['margin']
    assert b['metadata']['state'] == 'no_link'
    valid = np.asarray(b['layers']['valid'], bool)
    assert np.all(np.asarray(b['layers']['class'])[valid] == 0)
    assert np.all(np.asarray(b['layers']['class'])[~valid] == 255)
    assert np.any(np.asarray(a['layers']['class'])[valid] == 1)


def test_model_and_domain_fallback(terrain):
    client, _ = terrain
    result = client.post('/api/coverage', json=dict(range_m=300, predictor='model', h_tx=3)).json()
    m = result['metadata']
    assert m['predictor'] == 'deterministic'
    assert not m['in_evaluated_domain']
    assert len(m['fallback_reasons']) == 2
    assert m['state'] == 'out_of_domain'
    assert m['valid_pixels'] > 0


def test_states_and_validation(terrain):
    client, root = terrain
    assert client.post('/api/coverage', json={'latitude': -86.8}).status_code == 422
    assert client.post('/api/coverage', json={'h_tx': -1}).status_code == 422
    assert client.post('/api/coverage', json={'predictor': 'bogus'}).status_code == 422
    for route in ('', '/export', '/inspect'):
        body = {'radio_profile': 'bogus', 'rx_x_m': 0, 'rx_y_m': 0}
        assert client.post('/api/coverage'+route, json=body).status_code == 422
    assert client.post('/api/coverage', json={'site': 'nobile-rim-1'}).json()['metadata']['state'] == 'missing_data'
    assert client.post('/api/coverage', json={'latitude': -85.1, 'longitude': 0}).json()['metadata']['state'] == 'out_of_domain'
    with rasterio.open(root/'dem.tif', 'r+') as dst:
        dst.write(np.full((40, 40), -9999, dtype='float32'), 1)
    result = client.post('/api/coverage', json={'range_m': 300}).json()
    assert result['metadata']['state'] == 'no_valid_pixels'
    assert not np.asarray(result['layers']['valid']).any()
    (root/'dem.tif').unlink()
    assert client.post('/api/coverage', json={}).json()['metadata']['state'] == 'missing_data'


def test_export_georeferencing_masks_and_checksums(terrain):
    client, _ = terrain
    a = client.post('/api/coverage', json={'range_m': 300}).json()
    response = client.post('/api/coverage/export', json={'range_m': 300, 'threshold': 7})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        meta = json.loads(archive.read('scenario.json'))
        assert meta['threshold_db'] == 7
        assert meta['label_origin'] == 'simulated'
        assert meta['sources'] and meta['implementation_sha256']
        # Null (not a failure) outside a git checkout, e.g. a GitHub ZIP download.
        assert meta['code_version'] == c.code_version()
        if (repo_root()/'.git').exists():
            assert meta['code_version']
        for name, info in meta['exports'].items():
            content = archive.read(name)
            assert hashlib.sha256(content).hexdigest() == info['sha256']
            with rasterio.io.MemoryFile(content) as memory, memory.open() as src:
                assert [src.height, src.width] == a['metadata']['native_shape']
                assert src.crs
                assert list(src.transform)[:6] == meta['transform']
                assert src.tags()['label_origin'] == 'simulated'
                if name == 'valid.tif':
                    assert src.nodata is None  # zero is data, not missing


def test_inspection_matches_native_cell(terrain):
    client, _ = terrain
    result = client.post('/api/coverage', json={'range_m': 300, 'display_size': 256}).json()
    t = result['metadata']['display_transform']
    valid = np.asarray(result['layers']['valid'], bool)
    row, col = np.argwhere(valid)[len(np.argwhere(valid))//3]
    response = client.post('/api/coverage/inspect', json=dict(range_m=300,
        rx_x_m=t[2]+(int(col)+.5)*t[0], rx_y_m=t[5]+(int(row)+.5)*t[4]))
    assert response.status_code == 200
    inspect = response.json()
    assert inspect['profile']
    assert inspect['margin_db'] == pytest.approx(result['layers']['margin'][row][col], abs=1e-4)
    assert inspect['los'] == result['layers']['los'][row][col]
    assert inspect['state'] in ('covered', 'below_margin')


def test_coincident_phase_center_is_unknown_without_losing_map():
    from moonmesh_train.rf import label_arrays
    radio = dict(load_radio_profile('moonmesh_2p4_ref'), max_range_m=100)
    arrays, _, _ = label_arrays([0, 0], 2, 2, np.zeros((21, 21), dtype='float32'),
                                from_origin(-210, 210, 20, 20), PROJECTION, radio)
    assert arrays['valid_mask'].sum() > 0
    # The transmitter lies on a receiver center in the odd-sized lattice.
    assert arrays['valid_mask'][arrays['valid_mask'].shape[0]//2, arrays['valid_mask'].shape[1]//2] == 0


def test_model_domain_rule_matches_the_tower_sampler(tmp_path):
    """The app's model domain must be exactly where step 07 could place training and test towers."""
    from moonmesh_train.sampling import full_disk_valid
    rng = np.random.default_rng(3)
    dem = np.zeros((45, 60), dtype='float32')
    dem[rng.random(dem.shape) < 0.004] = np.nan  # scattered holes
    dem[30:33, 5:9] = np.nan
    tr = from_origin(0, 900, 20, 20)
    with rasterio.open(tmp_path/'dem.tif', 'w', driver='GTiff', width=60, height=45, count=1, dtype='float32',
                       crs=PROJECTION, transform=tr, nodata=np.nan) as dst:
        dst.write(dem, 1)
    for radius in (100, 200):
        expected = full_disk_valid(np.isfinite(dem), 20, radius)
        with rasterio.open(tmp_path/'dem.tif') as src:
            got = np.array([[REAL_DOMAIN_RULE(src, src.xy(r, k), radius) for k in range(60)] for r in range(45)])
        assert expected.any() and (~expected).any()
        assert np.array_equal(got, expected)


def test_transmitter_near_the_terrain_edge_is_out_of_model_domain(terrain, monkeypatch):
    client, _ = terrain
    # 500 m of range does not fit around the centre of the 800 m fixture terrain.
    monkeypatch.setattr(c, 'full_range_on_terrain', lambda src, tx, radius_m: REAL_DOMAIN_RULE(src, tx, 500))
    m = client.post('/api/coverage', json=dict(range_m=300)).json()['metadata']
    assert not m['in_evaluated_domain'] and m['state'] == 'out_of_domain'
    assert any('off the prepared terrain' in r for r in m['fallback_reasons'])
    assert m['valid_pixels'] > 0  # the physics map is still drawn
    refused = client.post('/api/coverage/ml-compare/jobs', json=dict(range_m=300))
    assert refused.status_code == 422 and 'whole 8 km range is on the prepared Haworth terrain' in refused.json()['detail']


def test_coverage_works_outside_a_git_checkout(terrain, monkeypatch):
    """A GitHub "Download ZIP" copy, or a computer without git, has no commit to record: report null, do not fail."""
    import subprocess
    client, _ = terrain

    def no_git():
        raise subprocess.CalledProcessError(128, ['git', 'rev-parse', 'HEAD'])
    monkeypatch.setattr(c, 'commit', no_git)
    response = client.post('/api/coverage', json=dict(range_m=300))
    assert response.status_code == 200 and response.json()['metadata']['code_version'] is None
    exported = client.post('/api/coverage/export', json=dict(range_m=300))
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert json.loads(archive.read('scenario.json'))['code_version'] is None
