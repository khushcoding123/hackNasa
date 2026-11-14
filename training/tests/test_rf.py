"""Analytic, app-parity and georeferenced round-trip checks for simulated RF."""
import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from moonmesh.models import Mission
from moonmesh.network import link_margin
from moonmesh_train.config import load_radio_profile
from moonmesh_train.los import PROJECTION
from moonmesh_train.rf import (BYTE_BANDS, FLOAT_BANDS, classify, fspl_db,
                               knife_edge_loss_db, label_arrays, received_power_dbm,
                               write_scenario, xyz)


@pytest.mark.parametrize('frequency,expected', [(2.4e9, 118.1), (1.8e9, 115.6)])
def test_fspl(frequency, expected):
    assert float(fspl_db(8000, frequency)) == pytest.approx(expected, abs=.1)


def test_free_space_matches_app():
    radio = load_radio_profile('moonmesh_2p4_ref')
    distance = np.array([.01, .1, 1., 3., 8., 20.])
    ours = received_power_dbm(fspl_db(distance*1000, 2.4e9), radio)-radio['receiver_sensitivity_dbm']
    np.testing.assert_allclose(ours, link_margin(Mission().surface_radio, distance), atol=.01, rtol=0)


def test_edge_loss():
    assert float(knife_edge_loss_db(0)) == pytest.approx(6.0, abs=.1)
    assert float(knife_edge_loss_db(-1)) == 0
    assert float(knife_edge_loss_db(-.78)) == 0
    assert np.isnan(knife_edge_loss_db(np.nan))
    assert np.all(knife_edge_loss_db(np.linspace(-5, 100, 1000)) >= 0)


def test_classes():
    valid = np.array([1, 1, 1, 1, 0], dtype=bool)
    los = np.array([1, 0, 1, 0, 255])
    margin = np.array([3., 3., 2.9, -4, np.nan])
    free = np.array([3., 8., 2.9, 8, np.nan])
    classes = classify(valid, los, margin, free, 3)
    for name, expected in dict(rf_covered=[1, 1, 0, 0, 0], terrain_dead_zone=[0, 1, 0, 1, 0],
                               rf_dead_zone=[0, 0, 1, 1, 0], terrain_caused_rf_outage=[0, 0, 0, 1, 0],
                               unknown=[0, 0, 0, 0, 1]).items():
        np.testing.assert_array_equal(classes[name], expected)


def test_roundtrip_and_chord(tmp_path):
    dem = np.zeros((61, 61), dtype='float32')
    master = from_origin(-610, 610, 20, 20)
    radio = dict(load_radio_profile('moonmesh_2p4_ref'), max_range_m=400)
    tx = np.array([13., 17.])
    arrays, tr, window = label_arrays(tx, 5, 2, dem, master, PROJECTION, radio)
    valid = arrays['valid_mask'].astype(bool)
    assert valid.any() and (~valid).any()
    assert arrays['los'].shape[0] < dem.shape[0]
    for k in FLOAT_BANDS:
        assert arrays[k].dtype == np.float32
        assert np.isnan(arrays[k][~valid]).all()
    for k in BYTE_BANDS:
        assert arrays[k].dtype == np.uint8
    # Direct 3D phase-center distance, including unequal heights; no map/arc substitute.
    row, col = np.argwhere(valid)[0]
    rx = (tr.c+(float(col)+.5)*tr.a, tr.f+(float(row)+.5)*tr.e)
    length = np.linalg.norm(xyz(*tx, 5)-xyz(*rx, 2))
    expected = received_power_dbm(fspl_db(length, 2.4e9), radio)-radio['receiver_sensitivity_dbm']
    assert arrays['free_space_margin_db'][row, col] == pytest.approx(expected, abs=1e-5)
    assert np.all(arrays['link_margin_db'][valid] <= arrays['free_space_margin_db'][valid]+1e-6)
    target = tmp_path/'simulated.tif'
    companion = write_scenario(target, arrays, tr, PROJECTION, {'code_commit': 'test'})
    for path, bands, dtype in [(target, BYTE_BANDS+FLOAT_BANDS, 'float32'), (companion, BYTE_BANDS, 'uint8')]:
        with rasterio.open(path) as src:
            assert src.transform == tr
            assert src.crs == rasterio.crs.CRS.from_string(PROJECTION)
            assert src.descriptions == bands
            assert set(src.dtypes) == {dtype}
            assert (src.transform.c-master.c)/20 == window.col_off
            assert (master.f-src.transform.f)/20 == window.row_off
            assert src.tags()['label_origin'] == 'simulated'
            for i, name in enumerate(bands, 1):
                np.testing.assert_array_equal(src.read(i), arrays[name])
                assert src.tags(i)['units']
    meta = json.loads(target.with_suffix('.json').read_text())
    assert meta['window_transform'] == list(tr)[:6]
    assert meta['bands']['los']['dtype'] == 'uint8'


@pytest.mark.parametrize('distance,frequency', [(0, 2.4e9), (-1, 2.4e9), (1, 0)])
def test_fspl_invalid(distance, frequency):
    with pytest.raises(ValueError):
        fspl_db(distance, frequency)
