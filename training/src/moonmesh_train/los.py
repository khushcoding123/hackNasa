"""Straight-chord lunar LOS (simulated), with strict bilinear terrain validity.

Quality codes: clear=0, blocked=1, partial=2, out_of_range=3.
Only spherical south polar stereographic, lon_0=0, R=1737400 is supported;
unsupported CRSs fail rather than silently using a terrestrial transform.
"""
import math
import numpy as np
from numba import njit, prange
from pyproj import CRS
from .inventory import same_projection

R = 1737400.
QUALITY = {"clear": 0, "blocked": 1, "partial": 2, "out_of_range": 3}
PROJECTION = "+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +R=1737400 +units=m"

@njit(cache=True)
def sample(dem, x, y, tr):
    col = (x-tr[2])/tr[0]-.5
    row = (y-tr[5])/tr[4]-.5
    if col < -1e-8 or row < -1e-8 or col > dem.shape[1]-1+1e-8 or row > dem.shape[0]-1+1e-8:
        return np.nan
    col = min(max(col, 0.), dem.shape[1]-1.)
    row = min(max(row, 0.), dem.shape[0]-1.)
    c, r = int(math.floor(col)), int(math.floor(row))
    c1, r1 = min(c+1, dem.shape[1]-1), min(r+1, dem.shape[0]-1)
    a,b,cval,d = dem[r,c],dem[r,c1],dem[r1,c],dem[r1,c1]
    if not (math.isfinite(a) and math.isfinite(b) and math.isfinite(cval) and math.isfinite(d)):
        return np.nan
    u,v = col-c,row-r
    return (1-v)*((1-u)*a+u*b)+v*((1-u)*cval+u*d)

@njit(cache=True)
def unit(x,y):
    rho = math.hypot(x,y)
    lat = 2*math.atan(rho/(2*R))-math.pi/2
    lon = math.atan2(x,y)
    return np.array([math.cos(lat)*math.cos(lon),math.cos(lat)*math.sin(lon),math.sin(lat)])

@njit(cache=True, parallel=True)
def _trace(tx, receivers, ht, hr, dem, tr, maximum, spacing, wavelength):
    n = len(receivers)
    # columns: minimum, obstruction x/y/distance, maximum nu, nu x/y/distance
    metrics = np.full((n,8),np.nan)
    los = np.full(n,255,np.uint8)
    quality = np.full(n,2,np.uint8)
    valid = np.zeros(n,np.bool_)
    u = unit(tx[0],tx[1])
    ground = sample(dem,tx[0],tx[1],tr)
    for i in prange(n):
        x,y = receivers[i,0],receivers[i,1]
        v = unit(x,y)
        angle = math.atan2(np.linalg.norm(np.cross(u,v)),np.dot(u,v))
        arc = R*angle
        if arc > maximum:
            quality[i] = 3
            continue
        z = sample(dem,x,y,tr)
        if not math.isfinite(z) or not math.isfinite(ground):
            continue
        a,b = u*(R+ground+ht),v*(R+z+hr)
        length = np.linalg.norm(b-a)
        if length < 1e-9:
            los[i],quality[i],valid[i] = 1,0,True
            metrics[i,0] = min(ht,hr)
            continue
        segments = max(2,int(math.ceil(length/spacing)))
        minimum,bestnu = math.inf,-math.inf
        bad = False
        for j in range(1,segments):
            f = j/segments
            point = a+(b-a)*f
            radius = np.linalg.norm(point)
            # XYZ -> planetocentric lat/lon -> spherical stereographic.
            # rho=2R*cos(lat)/(1-sin(lat)), evaluated without pole singularity.
            denom = radius-point[2]
            px,py = 2*R*point[1]/denom,2*R*point[0]/denom
            terrain = sample(dem,px,py,tr)
            if not math.isfinite(terrain):
                bad = True
                break
            clearance = radius-(R+terrain)
            if clearance < minimum:
                minimum = clearance
                metrics[i,0] = clearance
                metrics[i,1] = px
                metrics[i,2] = py
                metrics[i,3] = f*length
            if wavelength > 0:
                d1,d2 = f*length,(1-f)*length
                nu = -clearance*math.sqrt(2*(d1+d2)/(wavelength*d1*d2))
                if nu > bestnu:
                    bestnu = nu
                    metrics[i,4] = nu
                    metrics[i,5] = px
                    metrics[i,6] = py
                    metrics[i,7] = d1
        if bad:
            for k in range(8):
                metrics[i,k] = np.nan
            continue
        valid[i] = True
        los[i] = int(minimum > 0)
        quality[i] = 0 if minimum > 0 else 1
    return los,valid,quality,metrics

def trace_pairs(tx_xy, rx_xy, h_tx, h_rx, dem, transform, crs, max_range_m, spacing_m, wavelength_m=None):
    """Trace receivers; distances to obstructions are 3D chord metres from TX."""
    tr = np.asarray(tuple(transform)[:6],dtype=float)
    if not same_projection(crs,PROJECTION) or tr[1] != 0 or tr[3] != 0 or tr[0] <= 0 or tr[4] >= 0:
        raise ValueError("Requires north-up lunar south polar stereographic R=1737400, lon_0=0")
    if not 0 < spacing_m <= min(tr[0],-tr[4])/2:
        raise ValueError("spacing_m must be positive and at most half a DEM pixel")
    if (not all(math.isfinite(v) for v in [max_range_m, spacing_m, h_tx, h_rx, *tx_xy])
        or max_range_m <= 0 or min(h_tx,h_rx) < 0
        or (wavelength_m is not None and (not math.isfinite(wavelength_m) or wavelength_m <= 0))):
        raise ValueError("Invalid range, antenna height or wavelength")
    los,valid,quality,metrics = _trace(np.asarray(tx_xy,dtype=float),np.asarray(rx_xy,dtype=float).reshape(-1,2),
        float(h_tx),float(h_rx),np.asarray(dem),tr,float(max_range_m),float(spacing_m),float(wavelength_m or 0))
    names = ["min_clearance_m","obstruction_x_m","obstruction_y_m","obstruction_distance_m",
             "max_knife_edge_nu","knife_edge_x_m","knife_edge_y_m","knife_edge_distance_m"]
    return {"los":los,"valid":valid,"quality_flag":quality,**{k:metrics[:,i] for i,k in enumerate(names)}}

def viewshed(tx_xy,h_tx,h_rx,dem,transform,crs,max_range_m,spacing_m,wavelength_m=None):
    """Return full DEM-shaped arrays; outside great-circle range is unknown/code 3.

    Only receivers in the projected bounding box of the range disk are traced;
    the kernel enforces true spherical surface distance, including projection scale.
    """
    rows,cols = dem.shape
    dx,dy = transform.a,-transform.e
    # Stereographic local scale = 1+rho^2/(4R^2); conservative over range.
    pad = max_range_m*(1+((math.hypot(*tx_xy)+2*max_range_m)/(2*R))**2)*1.001
    c0=max(0,int((tx_xy[0]-pad-transform.c)/dx));c1=min(cols,int((tx_xy[0]+pad-transform.c)/dx)+1)
    r0=max(0,int((transform.f-tx_xy[1]-pad)/dy));r1=min(rows,int((transform.f-tx_xy[1]+pad)/dy)+1)
    rr,cc=np.mgrid[r0:r1,c0:c1]
    xy=np.column_stack((transform.c+(cc.ravel()+.5)*dx,transform.f-(rr.ravel()+.5)*dy))
    result=trace_pairs(tx_xy,xy,h_tx,h_rx,dem,transform,crs,max_range_m,spacing_m,wavelength_m)
    out={}
    for name,values in result.items():
        fill = 255 if name=="los" else 3 if name=="quality_flag" else False if name=="valid" else np.nan
        out[name]=np.full(dem.shape,fill,dtype=values.dtype)
        out[name][r0:r1,c0:c1]=values.reshape(rr.shape)
    return out
