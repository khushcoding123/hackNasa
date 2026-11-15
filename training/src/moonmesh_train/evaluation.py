"""Full-map evaluation of simulated predictors with terrain-only features.

Positive class is dead zone; false-covered fraction divides by true-dead area.
Boundary distances are symmetric nearest-boundary distances, without counting
the evaluation-domain perimeter. All stratified boundaries use the full map.
"""
import json
import math
import shutil
from pathlib import Path
from time import perf_counter

import lightgbm as lgb
import numpy as np
import pandas as pd
import rasterio
import yaml
from numba import set_num_threads
from scipy.ndimage import binary_erosion, distance_transform_edt

from . import paths, rf
from .config import configs_dir
from .dataset import FEATURES, TERRAIN, coarse_profiles, config_hash
from .downloads import atomic_json, sha256_file
from .los import R, sample, unit, trace_pairs
from .sampling import commit
from .tabular import safe_features, ratio

# Additional predictors (for example a locally trained U-Net) can register a
# callable (context, scenario_row, transform, shape) -> the same five arrays.
# No weights are discovered or downloaded implicitly.
EXTRA_PREDICTORS = {}


def register_predictor(name, function):
    if name in ('deterministic', 'distance', 'lightgbm') or name in EXTRA_PREDICTORS:
        raise ValueError('Predictor name already registered')
    EXTRA_PREDICTORS[name] = function


def select_scenarios(frame, requested=None, split='val', allow_final_subset=False):
    frame = frame.sort_values(['transmitter_id', 'tx_height_agl_m'])
    if split == 'test' and requested and not allow_final_subset:
        raise ValueError('Final test evaluates every scenario')
    if requested and not (len(requested) == 1 and requested[0].isdigit()):
        if not set(requested) <= set(frame.scenario_id):
            raise ValueError('Scenario IDs must belong to the selected split')
        return frame[frame.scenario_id.isin(requested)]
    if split == 'test' and not requested:
        return frame
    n = int(requested[0]) if requested else 24
    tx = frame.drop_duplicates('transmitter_id')
    # Round-robin across strata, stable transmitter order; never inspect labels.
    groups = [list(g.transmitter_id) for _, g in tx.groupby('stratum', sort=True)]
    chosen = []
    while any(groups) and len(chosen)*4 < n:
        for group in groups:
            if group and len(chosen)*4 < n:
                chosen.append(group.pop(0))
    if n % 4 or len(chosen)*4 != n:
        raise ValueError('Subset must contain complete four-height transmitters')
    return frame[frame.transmitter_id.isin(chosen)]


def area_and_scale(tr, shape, crs):
    from .inventory import same_projection
    from .los import PROJECTION
    if not same_projection(crs, PROJECTION):
        raise ValueError('Area formula requires the inspected lunar spherical polar CRS')
    rr, cc = np.indices(shape)
    x, y = tr.c+(cc+.5)*tr.a, tr.f+(rr+.5)*tr.e
    k = 1+(x*x+y*y)/(4*R*R)
    return abs(tr.a*tr.e-tr.b*tr.d)/k**2/1e6, k


def boundary(mask, valid):
    # Restrict to interior valid pixels so disk/nodata rims are not class edges.
    interior = binary_erosion(valid)
    return interior & (mask != binary_erosion(mask))


def boundary_distances(truth, predicted, valid, spacing, scale):
    a, b = boundary(truth, valid), boundary(predicted, valid)
    da = distance_transform_edt(~a, sampling=spacing)/scale
    db = distance_transform_edt(~b, sampling=spacing)/scale
    if not a.any() and not b.any():
        da[:] = np.inf
        return da, np.zeros(0), a, b
    if not a.any() or not b.any():
        return da, None, a, b
    return da, np.concatenate([db[a], da[b]]), a, b


def score(truth, pred, valid, area, selection, margin, predicted_margin, required, distances):
    v = valid & selection
    tp, fp = v & truth & pred, v & ~truth & pred
    fn, tn = v & truth & ~pred, v & ~truth & ~pred
    sums = [float(area[m].sum()) for m in (tp, fp, fn, tn)]
    t, f, n, z = sums
    e = predicted_margin[v]-margin[v]
    near = v & (np.abs(margin-required) <= 3)
    return dict(n=int(v.sum()), tp_km2=t, fp_km2=f, fn_km2=n, tn_km2=z,
        precision=ratio(t,t+f), recall=ratio(t,t+n), dead_iou=ratio(t,t+f+n),
        covered_iou=ratio(z,z+f+n), area_error_km2=f-n, absolute_area_error_km2=abs(f-n),
        false_covered_km2=n, false_covered_frac=ratio(n,t+n),
        boundary_mean_m=None if distances is None else float(np.mean(distances)) if len(distances) else 0.,
        boundary_p95_m=None if distances is None else float(np.percentile(distances,95)) if len(distances) else 0.,
        margin_mae_db=float(np.mean(abs(e))) if len(e) else None,
        margin_bias_db=float(np.mean(e)) if len(e) else None,
        near_threshold_margin_mae_db=float(np.mean(abs(predicted_margin[near]-margin[near]))) if near.any() else None,
        near_threshold_n=int(near.sum()),
        unknown_fraction=ratio(int((selection & ~valid).sum()),int(selection.sum())))


class Context:
    def __init__(self, cfg, learned=True):
        self.cfg = cfg
        self.root = paths.data_root()
        self.dc = yaml.safe_load((configs_dir()/cfg['dataset_config']).read_text())
        self.manifest = json.loads((self.root/cfg['dataset']/'manifest.json').read_text())
        self.radio = self.manifest['experiment']['radio']
        with rasterio.open(self.root/self.dc['dem']) as f:
            self.dem = f.read(1, masked=True).filled(np.nan)
            self.tr, self.crs = f.transform, f.crs
        self.terrain = {}
        self.models = {}
        if learned:
            for name in TERRAIN:
                with rasterio.open(self.root/self.dc['features_dir']/(name+'.tif')) as f:
                    self.terrain[name] = f.read(1, masked=True).filled(np.nan)
            meta = json.loads((self.root/cfg['models']/'metadata.json').read_text())
            safe_features(meta['feature_columns'])
            self.threshold = meta['los_threshold']
            self.models = {n:lgb.Booster(model_file=str(self.root/cfg['models']/(n+'.txt'))) for n in ('los','margin','clearance')}

    def geometry(self, row, tr, shape):
        rr, cc = np.indices(shape)
        x, y = tr.c+(cc+.5)*tr.a, tr.f+(rr+.5)*tr.e
        tx = np.array([row.transmitter_x_m,row.transmitter_y_m])
        gr = sample(self.dem,*tx,np.array(tuple(self.tr)[:6]))
        rows = np.rint((y-self.tr.f)/self.tr.e-.5).astype(int)
        cols = np.rint((x-self.tr.c)/self.tr.a-.5).astype(int)
        z = self.dem[rows,cols]
        a = rf.xyz(*tx, gr+row.tx_height_agl_m)
        b = rf.xyz(x,y,z+row.rx_height_agl_m)
        d = np.linalg.norm(b-a,axis=-1)
        u = unit(*tx)
        v = b/np.linalg.norm(b,axis=-1)[...,None]
        arc = R*np.arctan2(np.linalg.norm(np.cross(u,v),axis=-1),v@u)
        valid = np.isfinite(z) & np.isfinite(gr) & (arc <= row.max_range_m)
        return tx,gr,rows,cols,x,y,a,b,d,valid

    def predict(self, row, tr, shape, predictor):
        if predictor in EXTRA_PREDICTORS:
            return EXTRA_PREDICTORS[predictor](self,row,tr,shape)
        if predictor not in ('deterministic','distance','lightgbm'):
            raise ValueError('Unknown predictor: '+predictor)
        if predictor == 'deterministic':
            arrays, _, _ = rf.label_arrays(np.array([row.transmitter_x_m,row.transmitter_y_m]),row.tx_height_agl_m,
                row.rx_height_agl_m,self.dem,self.tr,self.crs,self.radio)
            return dict(los=arrays['los'],margin=arrays['link_margin_db'],clearance=arrays['min_clearance_m'],
                        covered=arrays['rf_covered'].astype(bool),valid=arrays['valid_mask'].astype(bool))
        tx,gr,rows,cols,x,y,a,b,d,valid = self.geometry(row,tr,shape)
        free = rf.received_power_dbm(rf.fspl_db(np.where(valid,d,np.nan),self.radio['frequency_ghz']*1e9),self.radio)-self.radio['receiver_sensitivity_dbm']
        if predictor == 'distance':
            los = d <= np.sqrt(2*R*row.tx_height_agl_m)+np.sqrt(2*R*row.rx_height_agl_m)
            return dict(los=los,margin=free,covered=los & (free >= row.required_margin_db),valid=valid,
                        clearance=np.full(shape,np.nan))
        idx = np.flatnonzero(valid)
        output = {n:np.full(shape,np.nan,dtype='float32') for n in self.models}
        bins = self.dc['horizon_bins']
        az = np.arange(bins)*2*np.pi/bins
        end = tx+self.radio['max_range_m']*np.column_stack((np.sin(az),np.cos(az)))
        _, horizon = coarse_profiles(tx,end,row.tx_height_agl_m,0.,self.dem,np.array(tuple(self.tr)[:6]),
            math.ceil(self.radio['max_range_m']/self.dc['horizon_spacing_m'])-1)
        txr,txc = rasterio.transform.rowcol(self.tr,*tx)
        lon = math.atan2(*tx)
        east = np.array([-math.sin(lon),math.cos(lon),0.])
        north = np.cross(unit(*tx),east)
        for offset in range(0,len(idx),self.cfg['feature_chunk_pixels']):
            ii = idx[offset:offset+self.cfg['feature_chunk_pixels']]
            xx,yy = x.ravel()[ii],y.ravel()[ii]
            delta = b.reshape(-1,3)[ii]-a
            bearing = np.arctan2(delta@east,delta@north)
            rxg = self.dem[rows.ravel()[ii],cols.ravel()[ii]]
            f = pd.DataFrame(dict(distance_m=d.ravel()[ii],sin_bearing=np.sin(bearing),cos_bearing=np.cos(bearing),
                h_tx=row.tx_height_agl_m,h_rx=row.rx_height_agl_m,freq_ghz=row.frequency_ghz,
                tx_ground_m=gr,rx_ground_m=rxg,ground_difference_m=rxg-gr))
            for name,layer in self.terrain.items():
                f['tx_'+name]=layer[txr,txc]
                f['rx_'+name]=layer[rows.ravel()[ii],cols.ravel()[ii]]
            gb = np.arctan2(xx-tx[0],yy-tx[1])%(2*np.pi)
            f['tx_horizon_angle_deg']=horizon[np.floor(gb*bins/(2*np.pi)+.5).astype(int)%bins]
            profile,angle=coarse_profiles(tx,np.column_stack((xx,yy)),row.tx_height_agl_m,row.rx_height_agl_m,
                self.dem,np.array(tuple(self.tr)[:6]),16)
            f['coarse_max_angle_deg']=angle
            for j in range(16):
                f[f'profile_{j:02d}_relative_m']=profile[:,j]
            for name,model in self.models.items():
                output[name].ravel()[ii]=model.predict(f[FEATURES].astype('float32'),num_threads=self.cfg['num_threads'])
        output['los']=output['los']>=self.threshold
        output['covered']=output['margin']>=row.required_margin_db
        output['valid']=valid & np.isfinite(output['margin'])
        return output


def read_truth(row):
    with rasterio.open(row.label_path) as f:
        return dict(zip(f.descriptions,f.read())),f.transform,f.crs


def write_prediction(path,pred,tr,crs):
    with rasterio.open(path.with_suffix('.tmp'),'w',driver='GTiff',height=pred['los'].shape[0],width=pred['los'].shape[1],
        count=5,dtype='float32',transform=tr,crs=crs,compress='deflate',predictor=3,tiled=True) as dst:
        for i,name in enumerate(('los','margin','clearance','covered','valid'),1):
            dst.write(np.where(pred['valid'],pred[name],np.nan).astype('float32') if name!='valid' else pred[name].astype('float32'),i)
            dst.set_band_description(i,name)
            dst.set_band_unit(i,'dB' if name=='margin' else 'm' if name=='clearance' else '1')
        dst.update_tags(label_origin='simulated',git_commit=commit())
    path.with_suffix('.tmp').replace(path)


def evaluate_map(ctx,row,out,representative=False):
    truth,tr,crs=read_truth(row)
    shape=truth['los'].shape
    valid=truth['valid_mask'].astype(bool)
    area,k=area_and_scale(tr,shape,crs)
    geometry=ctx.geometry(row,tr,shape)
    distance=geometry[-2]
    records=[]
    predictions={}
    for name in ('deterministic','distance','lightgbm',*EXTRA_PREDICTORS):
        if name=='deterministic':
            # Metrics reference the cached identical simulator, timing regenerates it.
            pred=dict(los=truth['los'],margin=truth['link_margin_db'],covered=truth['rf_covered'].astype(bool),
                clearance=truth['min_clearance_m'],valid=valid)
        else:
            pred=ctx.predict(row,tr,shape,name)
        predictions[name]=pred
        write_prediction(out/(row.scenario_id+'_'+name+'.tif'),pred,tr,crs)
        for kind,t,p in [('terrain',truth['los']==0,pred['los']==0),('rf',~truth['rf_covered'].astype(bool),~pred['covered'])]:
            domain=valid & pred['valid']
            da,dist,ba,bb=boundary_distances(t,p,domain,(abs(tr.e),abs(tr.a)),k)
            selections=[('all','all',np.ones(shape,bool))]
            for lo,hi in [(0,2000),(2000,4000),(4000,6000),(6000,8000.001)]:
                selections.append(('range_m',f'{lo}-{min(hi,8000):g}',(distance>=lo)&(distance<hi)))
            selections.extend([(n,str(value),np.ones(shape,bool)) for n,value in
                [('h_tx',row.tx_height_agl_m),('stratum',row.stratum),('site',row.site_id)]])
            edges=ctx.cfg['distance_boundary_bins_m']
            for lo,hi in zip(edges[:-1],edges[1:]):
                selections.append(('boundary_distance_m',f'{lo}-{hi}',(da>=lo)&(da<hi)))
            for group,level,selection in selections:
                localdist=dist
                if group=='range_m':
                    localdist=None if not ba.any() or not bb.any() else np.concatenate([
                        (distance_transform_edt(~bb,sampling=(abs(tr.e),abs(tr.a)))/k)[ba&selection],da[bb&selection]])
                elif group=='boundary_distance_m':
                    # Boundaries themselves have zero distance; retain full-map values
                    # only in all/range/height/stratum/site rows, not receiver bands.
                    localdist=None
                values=score(t,p,domain,area,selection,truth['link_margin_db'],pred['margin'],row.required_margin_db,localdist)
                records.append(dict(scenario_id=row.scenario_id,predictor=name,dead_zone=kind,group=group,level=level,
                    label_origin='simulated',**values))
    pd.DataFrame(records).to_csv(out/(row.scenario_id+'_metrics.csv'),index=False)
    if representative:
        figures(ctx,row,truth,predictions['lightgbm'],tr,out)


def figures(ctx,row,truth,pred,tr,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    valid=truth['valid_mask'].astype(bool)
    window=rasterio.windows.from_bounds(*rasterio.transform.array_bounds(*valid.shape,tr),transform=ctx.tr)
    with rasterio.open(ctx.root/'derived/terrain/haworth/hillshade_pgda90.tif') as f:
        shade=f.read(1,window=window)
    fig,axs=plt.subplots(1,2,figsize=(12,6),layout='constrained')
    colors=['#4580ab','#d7a83c','#df4e58','#31ab77']
    for ax,kind,t,p in [(axs[0],'Terrain',truth['los']==0,pred['los']==0),
                         (axs[1],'RF',truth['rf_covered']==0,~pred['covered'])]:
        classes=np.where(t,np.where(p,3,2),np.where(p,1,0))
        ax.imshow(shade,cmap='gray')
        ax.imshow(np.ma.masked_where(~valid,classes),cmap=ListedColormap(colors),vmin=0,vmax=3,alpha=.65)
        ax.set_title(kind+' dead zones (simulated)')
        ax.legend(handles=[Patch(color=c,label=l) for c,l in zip(colors,['TN','FP','FN','TP'])],fontsize=7)
    fig.suptitle(row.scenario_id+' LightGBM')
    fig.savefig(out/(row.scenario_id+'_confusion.png'),dpi=130); plt.close(fig)
    if (out/'contested_path.png').exists():
        return
    contested=valid & ((truth['los']==1)!=pred['los'])
    if not contested.any():
        return
    ii=np.flatnonzero(contested)[0]
    rr,cc=np.unravel_index(ii,valid.shape)
    rx=np.array(tr*(cc+.5,rr+.5)); tx=np.array([row.transmitter_x_m,row.transmitter_y_m])
    tv=np.array(tuple(ctx.tr)[:6])
    a=rf.xyz(*tx,sample(ctx.dem,*tx,tv)+row.tx_height_agl_m)
    b=rf.xyz(*rx,sample(ctx.dem,*rx,tv)+row.rx_height_agl_m)
    length=np.linalg.norm(b-a); f=np.linspace(0,1,int(np.ceil(length/10))+1)
    xyz=a+f[:,None]*(b-a); rad=np.linalg.norm(xyz,axis=1)
    x,y=2*R*xyz[:,1]/(rad-xyz[:,2]),2*R*xyz[:,0]/(rad-xyz[:,2])
    z=np.array([sample(ctx.dem,xx,yy,tv) for xx,yy in zip(x,y)])
    fresnel=np.sqrt(rf.C/(row.frequency_ghz*1e9)*length*f*(1-f))
    fig,(ax,clearance_ax)=plt.subplots(2,1,figsize=(10,7),layout='constrained')
    ax.plot(f*length,z,label='Terrain'); ax.plot(f*length,rad-R,label='Antenna chord')
    ax.fill_between(f*length,rad-R-fresnel,rad-R+fresnel,alpha=.2,label='First Fresnel zone')
    obs=np.nanargmin(rad-R-z); ax.scatter(f[obs]*length,z[obs],c='red',label='Obstruction')
    ax.set(xlabel='Chord distance (m)',ylabel='Radial elevation (m)',title=row.scenario_id+' contested simulated path'); ax.legend()
    clearance_ax.plot(f*length,rad-R-z,label='Signed terrain clearance')
    clearance_ax.axhline(0,color='red',ls='--',label='Terrain intersection')
    clearance_ax.scatter(f[obs]*length,(rad-R-z)[obs],color='red')
    clearance_ax.set(xlabel='Chord distance (m)',ylabel='Clearance (m)',
        title=f'Minimum interior clearance: {np.min((rad-R-z)[1:-1]):.3f} m')
    clearance_ax.legend()
    fig.savefig(out/'contested_path.png',dpi=140); plt.close(fig)
    pd.DataFrame(dict(distance_m=f*length,terrain_m=z,chord_m=rad-R,fresnel_radius_m=fresnel)).to_csv(out/'contested_path.csv',index=False)
    atomic_json(out/'contested_path.json',dict(scenario_id=row.scenario_id,rx_xy_m=rx.tolist(),
        truth_los=int(truth['los'][rr,cc]),predicted_los=bool(pred['los'][rr,cc]),label_origin='simulated'))


def runtime_one(cfg,row,out,index):
    records=[]
    truth,tr,_=read_truth(row)
    for name in ('deterministic','distance','lightgbm'):
        cold=None
        if index==0:
            start=perf_counter()
            ctx=Context(cfg,learned=name=='lightgbm')
            ctx.predict(row,tr,truth['los'].shape,name)
            cold=perf_counter()-start
            del ctx
        # Application-cold read/model construction included; OS cache uncontrolled.
        start=perf_counter()
        fresh=Context(cfg,learned=name=='lightgbm')
        fresh.predict(row,tr,truth['los'].shape,name)
        warm=perf_counter()-start
        records.append(dict(scenario_id=row.scenario_id,predictor=name,warm_ms=warm*1000,
            cold_ms=cold*1000 if cold is not None else None,label_origin='simulated'))
        del fresh
    pd.DataFrame(records).to_csv(out/f'runtime_{index}.csv',index=False)


def cross_source(ctx,row,out):
    truth,tr,_=read_truth(row)
    with rasterio.open(ctx.root/'derived/terrain/haworth/pgda78_native_5m.tif') as f:
        dem=f.read(1,masked=True).filled(np.nan); native_tr,crs=f.transform,f.crs
    tx=np.array([row.transmitter_x_m,row.transmitter_y_m])
    rr,cc=np.indices(truth['los'].shape); x,y=tr.c+(cc+.5)*tr.a,tr.f+(rr+.5)*tr.e
    valid=truth['valid_mask'].astype(bool)
    idx=np.flatnonzero(valid)
    traced=trace_pairs(tx,np.column_stack((x.ravel()[idx],y.ravel()[idx])),row.tx_height_agl_m,row.rx_height_agl_m,
        dem,native_tr,crs,row.max_range_m,2.5,rf.C/(row.frequency_ghz*1e9),mode='fast')
    common=traced['valid']; n=int(common.sum())
    flips=int(np.sum(traced['los'][common]!=truth['los'].ravel()[idx][common]))
    atomic_json(out/('cross_'+row.scenario_id+'.json'),dict(scenario_id=row.scenario_id,compared_pixels=n,
        flips=flips,flip_fraction=ratio(flips,n),native_unknown_pixels=int((~common).sum()),
        primary_valid_pixels=int(valid.sum()),label_origin='simulated',
        native_spacing_m=2.5,primary_spacing_m=10,
        method='Native PGDA78 5m terrain traced at identical PGDA90 20m receiver centers; 2.5m fast spacing required by tracer half-pixel validation. Only jointly valid full paths compared. Source and spacing effects are combined.'))


def report(cfg,selected,out):
    rows=pd.concat([pd.read_csv(out/(sid+'_metrics.csv')) for sid in selected.scenario_id],ignore_index=True)
    rows.to_csv(out/'raw_metrics.csv',index=False)
    runtimes=pd.concat([pd.read_csv(out/f'runtime_{i}.csv') for i in range(5)],ignore_index=True)
    runtimes.to_csv(out/'runtime.csv',index=False)
    final_subset=cfg.get('_final_subset',False)
    cross_plan={'scenario_ids':[]} if final_subset else json.loads((out/'cross_source_plan.json').read_text())
    cross=[json.loads((out/('cross_'+sid+'.json')).read_text()) for sid in cross_plan['scenario_ids']]
    pd.DataFrame(cross).to_csv(out/'cross_source.csv',index=False)
    main=rows[rows.group=='all']
    def aggregate_group(g):
        t,f,n,z=[g[c].sum() for c in ['tp_km2','fp_km2','fn_km2','tn_km2']]
        return dict(n=int(g.n.sum()),precision=ratio(t,t+f),recall=ratio(t,t+n),dead_iou=ratio(t,t+f+n),
            covered_iou=ratio(z,z+f+n),false_covered_km2=n,false_covered_frac=ratio(n,t+n),
            area_error_km2=f-n,boundary_p95_m=None if g.boundary_p95_m.isna().any() else float(g.boundary_p95_m.max()),
            boundary_mean_m=None if g.boundary_mean_m.isna().any() else float(g.boundary_mean_m.mean()),
            near_threshold_margin_mae_db=ratio((g.near_threshold_margin_mae_db.fillna(0)*g.near_threshold_n).sum(),g.near_threshold_n.sum()),
            margin_mae_db=ratio((g.margin_mae_db*g.n).sum(),g.n.sum()),
            margin_bias_db=ratio((g.margin_bias_db*g.n).sum(),g.n.sum()),unknown_fraction=float(g.unknown_fraction.mean()))
    aggregate={predictor+'_'+kind:aggregate_group(g) for (predictor,kind),g in main.groupby(['predictor','dead_zone'])}
    stratified=[]
    for (predictor,kind,group,level),g in rows[rows.group!='all'].groupby(['predictor','dead_zone','group','level']):
        stratified.append(dict(predictor=predictor,dead_zone=kind,group=group,level=level,
            label_origin='simulated',**aggregate_group(g)))
    pd.DataFrame(stratified).to_csv(out/'stratified_metrics.csv',index=False)
    times=runtimes.groupby('predictor').warm_ms.mean().to_dict()
    speedup=times['deterministic']/times['lightgbm']
    accept=yaml.safe_load((configs_dir()/cfg['acceptance']).read_text())
    learned=[aggregate['lightgbm_'+k] for k in ('terrain','rf')]
    measured={k:None if any(v[k] is None for v in learned) else max(v[k] for v in learned) for k in ('false_covered_frac','boundary_p95_m','near_threshold_margin_mae_db')}
    measured['min_speedup']=speedup
    criteria={k:dict(value=None if measured[k] is None else float(measured[k]),threshold=float(limit),passed=False if measured[k] is None else bool(measured[k]>=limit if k=='min_speedup' else measured[k]<=limit)) for k,limit in accept.items()}
    recommended=not criteria['boundary_p95_m']['passed'] or not criteria['false_covered_frac']['passed']
    warnings=['All references and predictions are simulated, not field validation.',
        'PGDA quality layers unavailable: observation-count/quality stratification skipped.',
        'PGDA vertical datum remains null/TODO in inherited metadata; cross-source flips include source differences.',
        'Native-5m cross-source tracing uses 2.5m spacing (required by the tracer half-pixel guard), versus primary 10m spacing; uncertainty includes both source and sampling differences. Only two val transmitters overlap PGDA78, so five scenarios reuse heights at these locations.',
        'Cold means application-cold raster/model reads, not flushed OS disk cache; Python/import startup excluded.',
        'Boundary acceptance uses worst scenario p95 across both dead-zone types; mean boundary distance is scenario-macro averaged. Two empty boundaries have distance 0; one empty boundary is undefined/null and fails acceptance.',
        'Unknown fraction includes out-of-range rectangular-window corners; invalid truth/prediction excluded from class metrics.',
        'Distance baseline continuous margin is free-space; horizon gating separately controls coverage.',
        'Terrain boundary distance strata use full-map reference boundaries; receiver-band boundary metrics are undefined/null.',
        'U-Net deferred in this time-boxed run; torch was not installed.']
    result=dict(label_origin='simulated',split='val' if 'val' in cfg['_split'] else 'test',code_commit=commit(),
        scenario_ids=selected.scenario_id.tolist(),aggregate=aggregate,acceptance=criteria,
        warm_ms=times,speedup=speedup,cross_source_flip_fraction=ratio(sum(c['flips'] for c in cross),sum(c['compared_pixels'] for c in cross)),
        warnings=warnings,config_hash=cfg['_signature'],aggregation='Area-weighted confusion; pixel-weighted margin; worst-scenario boundary p95; scenario-mean unknown',
        provenance=dict(dataset_manifest_sha256=sha256_file(paths.data_root()/cfg['dataset']/'manifest.json'),
            native5m_dem_sha256=sha256_file(paths.data_root()/'derived/terrain/haworth/pgda78_native_5m.tif'),
            acceptance_sha256=sha256_file(configs_dir()/cfg['acceptance']),
            evaluation_implementation_sha256=sha256_file(Path(__file__))))
    if final_subset:
        result.update(final_subset=True,selection_rule=cfg['_selection_rule'])
        by_predictor={}
        for predictor in ('deterministic','distance','lightgbm'):
            values={k:None if any(aggregate[predictor+'_'+kind][k] is None for kind in ('terrain','rf')) else
                max(aggregate[predictor+'_'+kind][k] for kind in ('terrain','rf'))
                for k in ('false_covered_frac','boundary_p95_m','near_threshold_margin_mae_db')}
            values['min_speedup']=times['deterministic']/times[predictor]
            by_predictor[predictor]={k:dict(value=values[k],threshold=float(limit),
                passed=False if values[k] is None else bool(values[k]>=limit if k=='min_speedup' else values[k]<=limit))
                for k,limit in accept.items()}
        result['acceptance_by_predictor']=by_predictor
        warnings.append('Time-boxed final subset; cross-source checks remain the separate validation results and were not rerun on test.')
    atomic_json(out/'metrics.json',result)
    if cfg['_split']=='val':
        atomic_json(paths.reports_dir()/'eval/unet_decision.json',dict(recommended=recommended,
            reasons=[f'{k}: {"PASS" if c["passed"] else "FAIL"} ({c["value"]}, limit {c["threshold"]})' for k,c in criteria.items() if k in ('boundary_p95_m','false_covered_frac')]+['U-Net deferred in this time-boxed run.'],label_origin='simulated',split=cfg['_split']))
    lines=['# Full-map evaluation (simulated)', '',f'Commit `{commit()}`; split {cfg["_split"]}; seed {cfg["seed"]}; {len(selected)} scenarios.',
        '', 'Scenario IDs: '+', '.join(selected.scenario_id), '', '| Criterion | Value | Threshold | Result |','|---|---:|---:|---|']
    lines += [f'| {k} | {c["value"]} | {c["threshold"]} | {"PASS" if c["passed"] else "FAIL"} |' for k,c in criteria.items()]
    lines += ['', '| Predictor / dead zone | Precision | Recall | Dead IoU | Covered IoU | False-covered km² | Boundary p95 m | Margin MAE dB |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for key,g in aggregate.items():
        fields=['precision','recall','dead_iou','covered_iou','false_covered_km2','boundary_p95_m','margin_mae_db']
        lines.append('| '+key+' | '+' | '.join('null' if g[k] is None else f'{g[k]:.6g}' for k in fields)+' |')
    lines += ['', '| Predictor | Warm mean ms/scenario | Speedup |', '|---|---:|---:|']
    lines += [f'| {k} | {v:.3f} | {times["deterministic"]/v:.3f} |' for k,v in times.items()]
    cross_fraction=result['cross_source_flip_fraction']
    lines += ['',f'One application-cold run and five warm scenarios are recorded per predictor in runtime.csv. Cross-source LOS flip fraction: {cross_fraction} on five validation scenarios (jointly valid paths only).',
        '', 'Areas use projected pixel area divided by the spherical polar stereographic scale factor squared: k = 1 + rho²/(4R²), with CRS verified against the labeler projection. Boundary EDT distances use local 1/k correction.',
        '', 'False-covered fraction = false-covered area / true-dead area, separately for terrain and RF. Near threshold means absolute true margin minus required margin <= 3 dB. Full per-scenario and stratified metrics are in raw_metrics.csv; aggregate metrics in metrics.json.',
        '', '## Limitations','']+['- '+w for w in warnings]
    (out/'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    if final_subset:
        criteria=by_predictor['lightgbm']
        ship=all(c['passed'] for c in criteria.values())
        reasons=[f'LightGBM {k}: {"PASS" if c["passed"] else "FAIL"} (value {c["value"]}, threshold {c["threshold"]})'
            for k,c in criteria.items()]
        decision=dict(ship='model' if ship else 'deterministic',model='lightgbm' if ship else None,
            scenarios_evaluated=len(selected),final_subset=True,reasons=reasons)
        atomic_json(out/'decision.json',decision)
        lines=['# Time-boxed final test (simulated)', '',
            f'Time-boxed final test on {len(selected)} of 80 test scenarios; final_subset: true. Commit `{commit()}`; seed {cfg["seed"]}.',
            '', 'Selection rule: '+cfg['_selection_rule'], '', 'Scenario IDs: '+', '.join(selected.scenario_id),
            '', '| Predictor | Criterion | Value | Threshold | Result |','|---|---|---:|---:|---|']
        for predictor,checks in by_predictor.items():
            lines += [f'| {predictor} | {k} | {c["value"]} | {c["threshold"]} | {"PASS" if c["passed"] else "FAIL"} |'
                for k,c in checks.items()]
        lines += ['', '**Ship '+('the learned LightGBM model.' if ship else 'the deterministic map.')+'**', '']+reasons
        lines += ['', 'Acceptance uses the worse of terrain and RF results; boundary p95 is the worst scenario. '
            'The deterministic reference has speedup 1 and therefore fails the acceleration criterion by definition.',
            '', 'Runtime includes raster reads, model construction, horizon/profile features, all three heads and post-processing. '
            'One application-cold and five warm scenario measurements per predictor are in runtime.csv; Python startup is excluded and OS cache is uncontrolled.',
            '', '| Predictor | Warm mean ms/scenario | Speedup |','|---|---:|---:|']
        lines += [f'| {k} | {v:.3f} | {times["deterministic"]/v:.3f} |' for k,v in times.items()]
        lines += ['', 'Full aggregate, scenario and stratified results are in metrics.json, raw_metrics.csv and stratified_metrics.csv. '
            'No test tuning was performed. All labels and predictions are simulated.', '', 'Limitations:']+['- '+w for w in warnings]
        (out/'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    if cfg['_split']!='val':
        return
    gates=[dict(name='Three predictors on all 24 validation scenarios',passed=len(selected)==24 and len(main)==144,value=len(main)),
        dict(name='Runtime and five cross-source checks',passed=len(runtimes)==15 and len(cross)==5 and all(c['compared_pixels']>0 for c in cross),value=len(cross)),
        dict(name='Acceptance and U-Net decision written',passed=(paths.reports_dir()/'eval/unet_decision.json').exists(),value=criteria)]
    atomic_json(paths.reports_dir()/'pipeline/step_11.json',dict(step=11,title='Validation full-map evaluation',
        status='pass' if all(g['passed'] for g in gates) else 'fail',
        summary='Completed full-map simulated validation of three predictors on 24 scenarios, CPU timing and five native-5m cross-source checks; LightGBM acceptance results and the deferred U-Net decision are reported.',
        gates=gates,decisions=['Stable round-robin terrain strata selection: six transmitters, all four heights.',
            'Cross-source scenarios independently selected from validation transmitters inside the native-5m footprint; compare only complete jointly valid paths.',
            'Charge raster reads, model loading, horizon/profile features, all three heads and post-processing to timing.',
            'Use worst-scenario boundary p95 and area-weighted false coverage for acceptance; no test tuning.'],
        warnings=warnings,outputs=[str(out),str(configs_dir()/'acceptance.yaml'),str(paths.reports_dir()/'eval/unet_decision.json')],tests='Pending final required suites'))


def run(args):
    cfg=yaml.safe_load(args.config.read_text())
    set_num_threads(cfg['numba_threads'])
    root=paths.data_root()
    if shutil.disk_usage(root.anchor).free < 12*1024**3:
        raise OSError('Evaluation writes could leave less than 10 GB free')
    frame=pd.read_parquet(root/cfg['dataset']/'scenarios.parquet',filters=[('split','==',args.split)])
    selected=select_scenarios(frame,args.scenarios,args.split,getattr(args,'allow_final_subset',False))
    if args.split=='test' and args.scenarios and getattr(args,'allow_final_subset',False):
        cfg['_final_subset']=True
        selection_path=paths.reports_dir()/'eval/final/selection.json'
        selection=json.loads(selection_path.read_text()) if selection_path.exists() else {}
        cfg['_selection_rule']=selection.get('selection_rule','Explicit scenario IDs supplied via --scenarios; sorted by transmitter ID then height.') if set(selection.get('scenario_ids',[]))==set(selected.scenario_id) else 'Explicit scenario IDs supplied via --scenarios; sorted by transmitter ID then height.'
    cfg['_split']=args.split
    cfg['_signature']=config_hash(dict(config=cfg,scenarios=selected.scenario_id.tolist(),
        implementation=sha256_file(Path(__file__)),models={n:sha256_file(root/cfg['models']/(n+'.txt')) for n in ('los','margin','clearance')}))
    out=paths.reports_dir()/'eval'/(cfg['run_id'] if args.split=='val' else cfg['run_id']+'_final_test')
    if cfg.get('_final_subset'):
        out=paths.reports_dir()/'eval/final'
    out.mkdir(parents=True,exist_ok=True)
    plan=out/'plan.json'
    if plan.exists() and json.loads(plan.read_text())['config_hash']!=cfg['_signature']:
        raise ValueError('Evaluation config/implementation changed: use a fresh run_id or explicitly remove only this generated evaluation directory')
    atomic_json(plan,dict(config_hash=cfg['_signature'],scenario_ids=selected.scenario_id.tolist(),config=cfg,code_commit=commit(),label_origin='simulated'))
    print(f'Commit {commit()}; phase {args.phase}; {len(selected)} {args.split} scenarios; simulated',flush=True)
    if args.phase=='maps':
        pending=[r for r in selected.itertuples() if not (out/(r.scenario_id+'_done.json')).exists()][:args.max_scenarios]
        if pending:
            ctx=Context(cfg)
            representatives=set(selected.groupby('transmitter_id',sort=True).head(1).scenario_id.tolist()[:4])
            for row in pending:
                start=perf_counter(); evaluate_map(ctx,row,out,row.scenario_id in representatives)
                atomic_json(out/(row.scenario_id+'_done.json'),dict(seconds=perf_counter()-start,config_hash=cfg['_signature'],label_origin='simulated'))
                print(f'{row.scenario_id} complete in {perf_counter()-start:.1f}s',flush=True)
    elif args.phase=='runtime':
        # Five distinct scenarios, spreading heights/transmitters across the subset.
        timed=selected.iloc[np.linspace(0,len(selected)-1,5,dtype=int)]
        pending=[(i,r) for i,r in enumerate(timed.itertuples()) if not (out/f'runtime_{i}.csv').exists()]
        for i,row in pending[:args.max_scenarios]:
            runtime_one(cfg,row,out,i); print(f'Runtime {i} complete',flush=True)
    elif args.phase=='cross-source':
        ctx=Context(cfg,learned=False)
        with rasterio.open(root/'derived/terrain/haworth/pgda78_native_5m.tif') as f:
            b=f.bounds
        candidates=frame[(frame.transmitter_x_m>b.left)&(frame.transmitter_x_m<b.right)&
            (frame.transmitter_y_m>b.bottom)&(frame.transmitter_y_m<b.top)].sort_values(['transmitter_id','tx_height_agl_m'])
        candidates=candidates.drop_duplicates('transmitter_id')
        if len(candidates)<5:
            candidates=frame[frame.transmitter_id.isin(candidates.transmitter_id)].sort_values(['tx_height_agl_m','transmitter_id'])
        chosen=candidates.head(5)
        atomic_json(out/'cross_source_plan.json',dict(scenario_ids=chosen.scenario_id.tolist(),label_origin='simulated'))
        for row in [r for r in chosen.itertuples() if not (out/('cross_'+r.scenario_id+'.json')).exists()][:args.max_scenarios]:
            cross_source(ctx,row,out); print('Cross-source '+row.scenario_id+' complete',flush=True)
    else:
        report(cfg,selected,out)
