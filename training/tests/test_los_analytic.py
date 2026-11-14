import numpy as np
import pytest
from rasterio.transform import from_origin
from moonmesh_train.los import trace_pairs, R, PROJECTION, unit

TR=from_origin(-20000,20000,20,20)

def trace(tx,rx,ht,hr,z,spacing=5,wave=None):
    return trace_pairs(tx,rx,ht,hr,z,TR,PROJECTION,30000,spacing,wave)

@pytest.mark.parametrize("ht,hr,expected",[(2,2,5272),(5,2,6804),(10,2,8531),(50,2,15817),(4,1.5,6011)])
def test_bald_sphere(ht,hr,expected):
    z=np.zeros((2000,2000),np.float32)
    distances=np.arange(expected-60,expected+61,10.)
    out=trace((0,0),np.column_stack((distances,np.zeros(len(distances)))),ht,hr,z)
    boundary=distances[out['los']==1].max()
    assert abs(boundary-expected)<=20

def test_ridge_and_reciprocity():
    z=np.zeros((2000,2000),np.float32)
    tx,rx=(-1010.,10.),(1010.,10.)
    a,b=unit(*tx)*(R+5),unit(*rx)*(R+2)
    middle=(a+b)/2
    height=np.linalg.norm(middle)-R-.5
    # Wide flat ridge; slight unequal endpoint slope shifts the minimum only ~0.015 m.
    z[:,999:1001]=height
    out=trace(tx,[rx],5,2,z,wave=.16)
    assert out['los'][0]==1
    assert out['min_clearance_m'][0]==pytest.approx(.5,abs=.05)
    reverse=trace(rx,[tx],2,5,z,wave=.16)
    assert reverse['min_clearance_m'][0]==pytest.approx(out['min_clearance_m'][0],abs=1e-8)
    assert reverse['max_knife_edge_nu'][0]==pytest.approx(out['max_knife_edge_nu'][0],abs=1e-8)
    z[:,999:1001]+=1
    out=trace(tx,[rx],5,2,z)
    assert out['los'][0]==0
    assert out['min_clearance_m'][0]==pytest.approx(-.5,abs=.05)

def test_nan_and_outside():
    z=np.zeros((2000,2000),np.float32);z[999,1000]=np.nan
    out=trace((-1000,10),[(1000,10)],5,2,z)
    assert not out['valid'][0] and out['los'][0]==255 and out['quality_flag'][0]==2
    out=trace((0,0),[(20100,0)],5,2,z)
    assert not out['valid'][0] and out['quality_flag'][0]==2

def test_monotonic_rough_terrain():
    z=np.random.default_rng(42).normal(0,2,(2000,2000)).astype('float32')
    xy=np.random.default_rng(43).uniform(-4000,4000,(200,2))
    low=trace((10,10),xy,5,2,z);high=trace((10,10),xy,10,2,z)
    assert np.all(high['los'][low['los']==1]==1)


def test_out_of_range_and_missing_wavelength():
    z=np.zeros((2000,2000),np.float32)
    out=trace_pairs((0,0),[(2000,0)],5,2,z,TR,PROJECTION,1000,10)
    assert out['los'][0]==255 and out['quality_flag'][0]==3
    assert not out['valid'][0]
    out=trace((0,0),[(100,0)],5,2,z)
    assert np.isnan(out['max_knife_edge_nu'][0])
    with pytest.raises(ValueError):trace((0,0),[(100,0)],5,2,z,spacing=20)


def test_knife_edge_value_at_controlling_ridge():
    z=np.zeros((2000,2000),np.float32)
    tx,rx=(-1010.,10.),(1010.,10.)
    a,b=unit(*tx)*(R+2),unit(*rx)*(R+2)
    z[:,999:1001]=np.linalg.norm((a+b)/2)-R+.5
    result=trace(tx,[rx],2,2,z,wave=.16)
    length=np.linalg.norm(a-b)
    assert result['max_knife_edge_nu'][0]==pytest.approx(.5*np.sqrt(8/(.16*length)),abs=.003)
    assert result['knife_edge_distance_m'][0]==pytest.approx(length/2,abs=12)


def test_interior_chord_leaves_raster_with_valid_endpoints():
    # A projected great circle bows north of the top center row by ~0.83 m.
    tr=from_origin(-11010,100010,20,20)
    z=np.zeros((20,1101),np.float32)
    result=trace_pairs((-10000,100000),[(10000,100000)],5,2,z,tr,PROJECTION,30000,10)
    assert result['quality_flag'][0]==2 and result['los'][0]==255
    assert not result['valid'][0] and np.isnan(result['min_clearance_m'][0])


def test_app_mode_200_random_pairs():
    from lunar_core.terrain import TerrainGrid, polar_stereographic_south_inverse
    from moonmesh.science import surface_path, Observer
    from scipy.ndimage import gaussian_filter
    rng=np.random.default_rng(17)
    z=gaussian_filter(rng.normal(0,30,(400,400)),3).astype('float32')
    tr=from_origin(-4000,104000,20,20);grid=TerrainGrid(z,tr.c,tr.f,20)
    for _ in range(200):
        tx=rng.uniform([-3500,96500],[3500,103500]);rx=rng.uniform([-3500,96500],[3500,103500])
        ht,hr=rng.uniform(.5,10,2)
        def observer(xy,h):
            lat,lon=polar_stereographic_south_inverse(*xy)
            return Observer(float(lat),float(lon),float(grid.sample(*xy)[0]),h)
        expected,_=surface_path(grid,observer(tx,ht),observer(rx,hr))
        actual=trace_pairs(tx,[rx],ht,hr,z,tr,PROJECTION,20000,20,mode='app')
        assert (None if not actual['valid'][0] else bool(actual['los'][0]))==expected


@pytest.mark.parametrize("seed",[52,53,54])
def test_fast_matches_reference_random_dem(seed):
    rng=np.random.default_rng(seed);z=rng.normal(0,10,(400,400)).astype('float32')
    tr=from_origin(-4000,104000,20,20)
    xy=rng.uniform([-3500,96500],[3500,103500],(300,2))
    args=((10,100010),xy,4,1.5,z,tr,PROJECTION,22040,10,.1665513656)
    a=trace_pairs(*args);b=trace_pairs(*args,mode='fast')
    for name in ['los','valid','quality_flag']:np.testing.assert_array_equal(a[name],b[name])
    np.testing.assert_allclose(a['min_clearance_m'],b['min_clearance_m'],atol=1e-8,rtol=0,equal_nan=True)


def test_app_mode_unknown_and_spacing_rule():
    from lunar_core.terrain import TerrainGrid, polar_stereographic_south_inverse
    from moonmesh.science import Observer, surface_path
    z=np.zeros((400,400),np.float32);z[200,200]=np.nan
    tr=from_origin(-4000,104000,20,20);grid=TerrainGrid(z,tr.c,tr.f,20)
    tx,rx=(-1000.,99990.),(1000.,99990.)
    def observer(xy):
        lat,lon=polar_stereographic_south_inverse(*xy)
        return Observer(float(lat),float(lon),float(grid.sample(*xy)[0]),2)
    expected,_=surface_path(grid,observer(tx),observer(rx))
    assert expected is None
    out=trace_pairs(tx,[rx],2,2,z,tr,PROJECTION,8000,20,mode='app')
    assert out['los'][0]==255 and not out['valid'][0]
    with pytest.raises(ValueError):
        trace_pairs(tx,[rx],2,2,z,tr,PROJECTION,8000,20)
