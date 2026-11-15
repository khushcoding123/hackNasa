"""Check terrain support, reproducibility, and split leakage prevention."""
import numpy as np
from affine import Affine
from scipy.spatial.distance import cdist
from moonmesh_train.sampling import classify, full_disk_valid, stratified_sample, spatial_split, STRATA


def test_strata_boundaries_and_unknown():
    rules = dict(floor_max_m=-15, shoulder_min_m=0, crest_min_m=15, steep_min_deg=20)
    result = classify(np.array([15.,0.,100.,-15.,-1.,np.nan]), np.array([0.,0.,20.,0.,0.,0.]), rules)
    assert result.tolist() == [0,1,2,3,4,-1]


def test_disk_rejects_holes_and_exterior():
    valid = np.ones((31,31),bool)
    valid[15,15] = False
    eligible = full_disk_valid(valid,20,60)
    assert not eligible[0].any()
    assert not eligible[15,18]
    assert eligible[7,7]


def test_sample_seed_spacing_and_strata():
    classes = np.indices((100,100))[1] % 5
    args = (classes,np.ones_like(classes,bool),Affine(20,0,0,0,-20,2000),50,100,42)
    first = stratified_sample(*args)
    assert first == stratified_sample(*args)
    xy = np.array([[p['x_m'],p['y_m']] for p in first])
    distances = cdist(xy,xy); np.fill_diagonal(distances,np.inf)
    assert distances.min() >= 100
    assert all(sum(p['stratum']==s for p in first)==10 for s in STRATA)


def test_spatial_split_buffer_blocks_seed_and_dropped():
    rng = np.random.default_rng(23)
    points = [dict(transmitter_id=str(i),x_m=float(x),y_m=float(y),stratum=STRATA[i%5]) for i,(x,y) in enumerate(rng.uniform(0,80000,(150,2)))]
    cfg = dict(block_size_m=5000,split_fractions=[.7,.15,.15],seed=42,split_layout_trials=64,buffer_m=8000)
    first = spatial_split(points,cfg)
    assert first == spatial_split(points,cfg)
    membership = first[1]
    assert set(membership) <= {'train','val','test','dropped'}
    assert membership.count('dropped') > 0
    xy = np.array([[p['x_m'],p['y_m']] for p in points])
    train = np.array(membership)=='train'
    holdout = np.isin(membership,['val','test'])
    assert cdist(xy[train],xy[holdout]).min() >= cfg['buffer_m']
    groups = {s:{p['transmitter_id'] for p,v in zip(points,membership) if s==v} for s in ('train','val','test','dropped')}
    assert sum(map(len,groups.values())) == len(points)
    assert not (groups['train'] & groups['val'] | groups['train'] & groups['test'] | groups['val'] & groups['test'])
    for block in first[2]:
        splits = {s for p,s in zip(points,membership) if tuple(np.floor(np.array([p['x_m'],p['y_m']])/5000).astype(int))==block and s!='dropped'}
        assert len(splits)<=1
