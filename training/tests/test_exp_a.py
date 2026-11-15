import numpy as np
import pytest
from moonmesh_train.exp_a import (empty_aggregates, update, save_checkpoint,
                                  load_checkpoint, select_masts, confusion)


def test_network_ties_validity_and_zero_counts():
    a = empty_aggregates((1, 4)); idx = np.arange(4)
    update(a, idx, np.array([-90., -100, -80, np.nan]), np.array([1, 0, 1, 255]),
           np.array([True, True, False, False]), 10, -94.4)
    update(a, idx, np.array([-90., -95, -110, np.nan]), np.array([0, 1, 0, 255]),
           np.array([True, True, True, False]), 5, -94.4)
    assert a['best_id'].tolist() == [[5, 5, 5, -1]]
    assert a['best_los'].tolist() == [[0, 1, 0, 255]]
    assert a['los_count'].tolist() == [[1, 1, 0, 0]]
    assert a['count'].tolist() == [[2, 0, 0, 0]]
    assert a['valid_count'].tolist() == [[2, 2, 1, 0]]
    # Later higher ID must not replace the tied lower ID or its LOS.
    update(a, np.array([0]), np.array([-90.]), np.array([1]), np.array([True]), 20, -94.4)
    assert a['best_id'][0, 0] == 5 and a['best_los'][0, 0] == 0


def test_checkpoint_resume_equals_uninterrupted_and_checks_provenance(tmp_path):
    a = empty_aggregates((1, 2)); idx = np.arange(2)
    args = (idx, np.array([-90., -100.]), np.array([1, 0]), np.ones(2, bool), 8, -94.4)
    update(a, *args)
    file = tmp_path/'checkpoint.npz'
    save_checkpoint(file, a, dict(identity={'source': 'one'}, finished=[8]))
    restored, meta = load_checkpoint(file, {'source': 'one'})
    update(a, *args); update(restored, *args)
    for name in a: np.testing.assert_array_equal(a[name], restored[name])
    assert meta['finished'] == [8]
    with pytest.raises(ValueError, match='provenance'):
        load_checkpoint(file, {'source': 'two'})


def test_mast_selection_uses_rectangle_corners_and_includes_boundary(tmp_path):
    catalog = tmp_path/'catalog.csv'
    catalog.write_text('cand_id,x_m,y_m,z_m\n3,12,5,0\n2,12,12,0\n1,5,5,0\n')
    assert [m['cand_id'] for m in select_masts(catalog, (0, 0, 10, 10), 2)] == [1, 3]


def test_confusion_masks_unknown_and_retains_zero_reference():
    m = confusion(np.array([True, False, True, False, True]),
                  np.array([True, False, False, True, False]),
                  np.array([True, True, True, True, False]), .0004)
    assert [m[k] for k in ('tp', 'tn', 'fp', 'fn')] == [1, 1, 1, 1]
    assert m['iou'] == pytest.approx(1/3)
    assert m['false_covered_km2'] == .0004


def test_published_count_zeros_recovered_from_alignment_source(tmp_path):
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.windows import Window
    from moonmesh_train.exp_a import read_layer
    crs = '+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +R=1737400 +units=m'
    original = tmp_path/'original.tif'
    with rasterio.open(original, 'w', driver='GTiff', height=1, width=2, count=1,
                       dtype='uint16', nodata=0, crs=crs, transform=from_origin(20, 40, 20, 20)) as dst:
        dst.write(np.array([[0, 3]], dtype='uint16'), 1)
    aligned = tmp_path/'Haworth__los_count.tif'
    with rasterio.open(aligned, 'w', driver='GTiff', height=3, width=4, count=1,
                       dtype='float32', nodata=np.nan, crs=crs, transform=from_origin(0, 60, 20, 20)) as dst:
        a = np.full((3, 4), np.nan, np.float32); a[1, 2] = 3
        dst.write(a, 1); dst.update_tags(source=str(original))
    values, valid = read_layer(tmp_path, 'los_count', Window(1, 1, 2, 1), counts=True)
    assert values.tolist() == [[0, 3]]
    assert valid.tolist() == [[True, True]]
