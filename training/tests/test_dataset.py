"""Sampling, leakage and spherical coarse-profile checks."""
import numpy as np
import pytest
from rasterio.transform import from_origin

from moonmesh_train.dataset import FEATURES, check_features, coarse_profiles, config_hash, sample_receivers


def test_feature_allowlist():
    check_features(FEATURES)
    for name in ('min_clearance_m','obstruction_x_m','best_prx','los_count','transmitter_id','split','sample_weight'):
        with pytest.raises(ValueError):
            check_features(FEATURES+[name])


def test_sampling_validity_boundary_weights_and_seed():
    arrays=dict(valid_mask=np.array([[1,1,1,0]]),min_clearance_m=np.array([[1,20,-30,np.nan]]),
                link_margin_db=np.array([[20,30,-10,np.nan]]))
    cfg=dict(uniform_receivers=2,boundary_receivers=1,clearance_boundary_m=5,margin_boundary_db=3)
    a=sample_receivers(arrays,cfg,np.random.default_rng(42),0)
    b=sample_receivers(arrays,cfg,np.random.default_rng(42),0)
    for x,y in zip(a,b):
        np.testing.assert_array_equal(x,y)
    indices,kinds,weights,boundary=a
    assert 3 not in indices
    assert indices[kinds=='boundary'].tolist()==[0]
    np.testing.assert_allclose(weights,1/(2/3+boundary.ravel()[indices]))


def test_coarse_bald_sphere_chord():
    dem=np.zeros((101,101),dtype=np.float32)
    tr=from_origin(-1010,1010,20,20)
    profile,angles=coarse_profiles(np.array([0.,0.]),np.array([[600.,0.]]),5.,2.,dem,np.array(tuple(tr)[:6]),16)
    assert profile.shape==(1,16)
    assert np.all(profile<0)
    assert np.isfinite(angles).all()
    # For short paths, curvature adds a small positive bulge to terrain-minus-line.
    flat=-5+(np.arange(1,17)/17)*3
    np.testing.assert_allclose(profile[0],flat,atol=.03)


def test_hash_order_independent():
    assert config_hash(dict(a=1,b=2))==config_hash(dict(b=2,a=1))
