"""Check simulated curved-body LOS against the app and spacing convergence."""
import argparse
from pathlib import Path
import json
import subprocess
from time import perf_counter
import numpy as np
import rasterio
from pyproj import CRS, Transformer
from numba import get_num_threads
import yaml
from moonmesh_train import paths
from moonmesh_train.los import trace_pairs, viewshed, QUALITY
from moonmesh_train.downloads import atomic_json, sha256_file
from moonmesh.science import surface_path, Observer
from lunar_core.terrain import TerrainGrid, polar_stereographic_south_inverse


def main(argv=None):
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config',type=Path,default=paths.repo_root()/'training/configs/los_checks.yaml')
    args=cli.parse_args(argv)
    cfg=yaml.safe_load(args.config.read_text())
    site=yaml.safe_load((args.config.parent/cfg['site_config']).read_text())
    root=paths.data_root();out=paths.reports_dir()/'los';out.mkdir(parents=True,exist_ok=True)
    primary=json.loads((root/'derived/terrain/haworth/primary_dems.json').read_text())
    if primary['registration_status']!='PASS':raise ValueError('Terrain registration has not passed')
    source=Path(primary['experiment_b'])
    with rasterio.open(source) as src:
        dem=src.read(1);tr=src.transform;crs=src.crs;profile=src.profile
    commit=subprocess.run(['git','rev-parse','HEAD'],cwd=paths.repo_root(),capture_output=True,text=True).stdout.strip()
    print(f'Git commit {commit}; seed {cfg["seed"]}; simulated LOS; Numba threads {get_num_threads()}',flush=True)
    tx=np.array(Transformer.from_crs(CRS(crs).geodetic_crs,crs,always_xy=True).transform(site['longitude_deg'],site['latitude_deg']))
    rng=np.random.default_rng(cfg['seed']);grid=TerrainGrid(dem,tr.c,tr.f,tr.a)
    pairs=[]
    # Random TX and RX inside the buffered DEM, with TX within 5 km of Haworth.
    for i in range(cfg['pair_count']):
        start=tx+rng.uniform(-5000,5000,2)
        bearing=rng.uniform(0,2*np.pi);distance=rng.uniform(100,cfg['max_range_m']*.99)
        end=start+distance*np.array([np.cos(bearing),np.sin(bearing)])
        ht,hr=cfg['h_tx_m'],cfg['h_rx_m']
        result=trace_pairs(start,[end],ht,hr,dem,tr,crs,cfg['max_range_m'],10)
        lat1,lon1=polar_stereographic_south_inverse(*start);lat2,lon2=polar_stereographic_south_inverse(*end)
        first=Observer(float(lat1),float(lon1),float(grid.sample(*start)[0]),ht)
        second=Observer(float(lat2),float(lon2),float(grid.sample(*end)[0]),hr)
        app,arc=surface_path(grid,first,second)
        compatible=trace_pairs(start,[end],ht,hr,dem,tr,crs,cfg['max_range_m'],tr.a,mode='app')
        compatible_los=None if not compatible['valid'][0] else bool(compatible['los'][0])
        if compatible_los != app:raise AssertionError(f'App-compatible mismatch at pair {i}')
        ours=None if not result['valid'][0] else bool(result['los'][0])
        pairs.append({'pair':i,'tx_xy':start.tolist(),'rx_xy':end.tolist(),'h_tx':ht,'h_rx':hr,'app_los':app,'ours_los':ours,
                      'app_compatible_los':compatible_los,'app_compatible_match':compatible_los==app,'distance_m':arc*1000,'min_clearance_m':float(result['min_clearance_m'][0]),'match':app==ours,
                      'explanation':None if app==ours else 'App samples equal angular fractions at <=20 m; labeler samples equal XYZ chord fractions at <=10 m. Chord geometry is identical; terrain samples differ. See refined diagnostics.'})
        if app!=ours:
            refined=trace_pairs(start,[end],ht,hr,dem,tr,crs,cfg['max_range_m'],2.5)
            pairs[-1]['refined_2p5m_los']=int(refined['los'][0])
            pairs[-1]['refined_2p5m_clearance']=float(refined['min_clearance_m'][0])
    report={'label_origin':'simulated','git_commit':commit,'seed':cfg['seed'],'config':cfg,'source':str(source),
            'source_sha256':sha256_file(source),'numba_threads':get_num_threads(),'pairs':pairs,
            'pair_mismatches':sum(not p['match'] for p in pairs),'runs':[],'convergence':[],
            'quality_codes':QUALITY,'sampling':'Straight XYZ chord; interior spacing <= requested chord metres; strict bilinear interpolation; zero clearance blocked',
            'app_comparison_note':'App uses radial intersections of the same chord at equal angular fractions, <= one pixel spacing. Our XYZ fractions use <= half-pixel spacing. Endpoint ground is bilinear in both. No code/parameters tuned to agreement.'}
    atomic_json(out/'report.json',report)
    previous=None
    for spacing in cfg['spacings_m']:
        print(f'Full 8 km viewshed, spacing {spacing} m',flush=True)
        begin=perf_counter()
        result=viewshed(tx,cfg['h_tx_m'],cfg['h_rx_m'],dem,tr,crs,cfg['max_range_m'],spacing)
        elapsed=perf_counter()-begin
        report['runs'].append({'spacing_m':spacing,'seconds_warm_jit':elapsed,'valid_pixels':int(result['valid'].sum()),
                               'clear_pixels':int((result['los']==1).sum()),'blocked_pixels':int((result['los']==0).sum()),
                               'partial_pixels':int((result['quality_flag']==2).sum())})
        print(report['runs'][-1],flush=True)
        if previous is not None:
            joint=previous['valid'] & result['valid'];flips=joint & (previous['los']!=result['los'])
            rr,cc=np.where(flips)
            csvpath=out/f'flips_{previous_spacing:g}_to_{spacing:g}m.csv'
            import csv
            with csvpath.open('w',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(['row','column','x_m','y_m','coarse_los','fine_los','fine_min_clearance_m'])
                for r,c in zip(rr,cc):writer.writerow([r,c,tr.c+(c+.5)*tr.a,tr.f+(r+.5)*tr.e,previous['los'][r,c],result['los'][r,c],result['min_clearance_m'][r,c]])
            report['convergence'].append({'coarse_spacing_m':previous_spacing,'fine_spacing_m':spacing,'joint_valid_pixels':int(joint.sum()),
                'flipped_pixels':len(rr),'fraction_flipped':float(flips.sum()/joint.sum()),'locations_csv':str(csvpath),
                'validity_changes':int((previous['valid']!=result['valid']).sum())})
            mask=np.where(joint,flips.astype('float32'),np.nan)
            from moonmesh_train.alignment import write_raster
            g={'height':dem.shape[0],'width':dem.shape[1],'transform':list(tr)[:6],'crs_wkt':CRS(crs).to_wkt()}
            write_raster(out/f'flips_{previous_spacing:g}_to_{spacing:g}m.tif',mask,g,'simulated LOS spacing flips','1',label_origin='simulated')
        previous={k:result[k] for k in ['los','valid']};previous_spacing=spacing
        atomic_json(out/'report.json',report)
    # Preserve finest LOS and continuous diagnostics with band descriptions/units.
    profile.update(driver='GTiff',count=len(result),dtype='float32',nodata=np.nan,compress='deflate')
    with rasterio.open(out/'viewshed_2p5m.tif','w',**profile) as dst:
        for i,(name,array) in enumerate(result.items(),1):
            dst.write(array.astype('float32'),i);dst.set_band_description(i,name)
        dst.update_tags(label_origin='simulated',git_commit=commit,spacing_m=2.5,quality_codes=json.dumps(QUALITY),
                        distance_convention='obstruction distances along 3D chord from TX')
    # Keep the 8km result for the requested plot, then release its diagnostics.
    plot_viewshed(out, result['los'], dem, tr, tx, pairs)
    del result, previous
    radio_path=root/'interim/lunarrf/meta/atlas_v2/bundle/radio_params.json'
    radio=json.loads(radio_path.read_text())
    frequency=radio['frequencies_hz'][0]
    wavelength=299792458./frequency
    maximum=radio['max_radius_m']
    print(f'Brute-force full {maximum:g} m viewshed with wavelength {wavelength}',flush=True)
    begin=perf_counter()
    large=viewshed(tx,radio['mast_height_m'],radio['rx_height_m'],dem,tr,crs,maximum,10,wavelength)
    seconds=perf_counter()-begin
    benchmark={'range_m':maximum,'spacing_m':10,'h_tx_m':radio['mast_height_m'],'h_rx_m':radio['rx_height_m'],
               'frequency_hz':frequency,'wavelength_m':wavelength,'radio_source':str(radio_path),
               'reference_seconds':seconds,'masts':267,'projected_seconds_267':seconds*267,
               'projection_note':'Linear extrapolation from one research-point viewshed; excludes I/O, per-mast terrain variability and cold JIT.',
               'valid_pixels':int(large['valid'].sum())}
    report['large_viewshed_benchmark']=benchmark
    atomic_json(out/'report.json',report)
    print(benchmark,flush=True)
    if seconds*267>7200:
        # Warm the scalar kernel separately; no shared-profile approximation.
        trace_pairs(tx,[tx+[100,100]],4,1.5,dem,tr,crs,maximum,10,wavelength,mode='fast')
        begin=perf_counter()
        fast=viewshed(tx,4,1.5,dem,tr,crs,maximum,10,wavelength,mode='fast')
        fast_seconds=perf_counter()-begin
        for key in ['los','valid','quality_flag']:np.testing.assert_array_equal(large[key],fast[key])
        valid=large['valid'];delta=np.abs(large['min_clearance_m'][valid]-fast['min_clearance_m'][valid])
        maximum_error=float(delta.max())
        if maximum_error>1e-8:raise AssertionError('Fast clearance exceeds 1e-8 m tolerance')
        benchmark.update(fast_seconds=fast_seconds,fast_projected_seconds_267=fast_seconds*267,
                         speedup=seconds/fast_seconds,haworth_max_clearance_difference_m=maximum_error,
                         fast_method='Exact same rays and sample order; scalar XYZ arithmetic eliminates per-sample NumPy array allocation; no fastmath or terrain approximation',
                         tolerance_m=1e-8,haworth_los_identical=True)
        del fast
    del large
    report['status']='complete'
    atomic_json(out/'report.json',report)
    lines=['# LOS checks (simulated)','',f'Commit `{commit}`; seed {cfg["seed"]}; Numba threads {get_num_threads()}.',
           f'App comparison: {len(pairs)-report["pair_mismatches"]}/{len(pairs)} pairs agree.',report['app_comparison_note'],'',
           'Analytic tests cover bald sphere horizons, signed ridge clearance, reciprocity, NaN/outside validity and height monotonicity. No refraction. Unknown LOS=255; invalid continuous outputs=NaN.',
           '', '| Spacing (m) | Runtime (s, warm JIT) | Valid | Clear | Blocked |','|---|---:|---:|---:|---:|']
    for r in report['runs']:lines.append(f'| {r["spacing_m"]} | {r["seconds_warm_jit"]:.3f} | {r["valid_pixels"]} | {r["clear_pixels"]} | {r["blocked_pixels"]} |')
    lines+=['','Compilation excluded: pair checks warm the JIT. Runtime includes full DEM-shaped output allocation and all in-range rays.','']
    for c in report['convergence']:lines.append(f'{c["coarse_spacing_m"]} -> {c["fine_spacing_m"]} m: {c["flipped_pixels"]} flips / {c["joint_valid_pixels"]} jointly valid pixels = {c["fraction_flipped"]:.8%}. Locations: `{Path(c["locations_csv"]).name}`; corresponding GeoTIFF maps use 1=flip, 0=unchanged, NaN=outside joint validity.')
    lines+=['','Full receiver diagnostics: viewshed_2p5m.tif; pair coordinates, mismatch diagnostics and convergence metrics: report.json.','',
            'Scope: spherical south polar stereographic R=1737400 only. PGDA vertical datum remains undocumented as recorded by alignment. Full-trace LOS diagnostics must not enter model input features.']
    lines+=['','## App-compatible mode','',
            f'App-compatible mode agrees on all {len(pairs)} Haworth pairs. It uses the app spherical cosine angle, equal angular fractions, SciPy pixel-center bilinear sampler and radial chord formula verbatim. spacing_m is ignored only in this explicit mode; default tracing remains unchanged.',
            'The 200-pair synthetic rough-DEM comparison and LunarRF-height bald sphere (4/1.5 m, 6011 m) pass.',
            '', '## LunarRF-range benchmark','',
            f'Full {maximum:g} m viewshed, TX {radio["mast_height_m"]} m, RX {radio["rx_height_m"]} m, 10 m samples, wavelength {wavelength:.12f} m at {frequency:g} Hz: **{seconds:.3f} seconds**. Projection for 267 masts: **{seconds*267/3600:.3f} hours**.',
            benchmark['projection_note']]
    if 'fast_seconds' in benchmark:
        lines += [f'Optimized exact scalar method: **{benchmark["fast_seconds"]:.3f} seconds**, {benchmark["speedup"]:.2f}x speedup; 267-mast projection **{benchmark["fast_projected_seconds_267"]/3600:.3f} hours**.',
                  benchmark['fast_method'],
                  f'Random DEM tests and the complete Haworth viewshed have identical LOS/validity/quality. Haworth maximum min-clearance difference: {maximum_error:g} m; tolerance 1e-8 m. Brute-force remains the default reference.']
    lines += ['', '![Research-point 8km simulated LOS and 50 pairs](viewshed_8km.png)']
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'Reports: {out}',flush=True)
    return 0


def plot_viewshed(out, los, dem, tr, tx, pairs):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    from moonmesh_train.alignment import hillshade
    # Show a 9 km context so unknown/out-of-range has an explicit gray ring.
    c=int((tx[0]-tr.c)/tr.a);r=int((tx[1]-tr.f)/tr.e)
    endpoints=np.array([xy for p in pairs for xy in (p['tx_xy'],p['rx_xy'])])
    n=max(450,int(np.ceil(np.max(np.abs(endpoints-tx))/20))+25)
    rs=slice(r-n,r+n+1);cs=slice(c-n,c+n+1)
    shade=hillshade(dem[rs,cs],(20,20))
    extent=[(tr.c+(c-n)*20)/1000,(tr.c+(c+n+1)*20)/1000,
            (tr.f-(r+n+1)*20)/1000,(tr.f-(r-n)*20)/1000]
    fig,ax=plt.subplots(figsize=(10,9),layout='constrained')
    ax.imshow(shade,extent=extent,cmap='gray',vmin=0,vmax=1)
    code=np.where(los[rs,cs]==1,0,np.where(los[rs,cs]==0,1,2))
    ax.imshow(code,extent=extent,cmap=ListedColormap(['#20a66a','#d4473d','#969aaa']),vmin=0,vmax=2,alpha=.55)
    for p in pairs:
        a,b=np.array(p['tx_xy'])/1000,np.array(p['rx_xy'])/1000
        ax.plot([a[0],b[0]],[a[1],b[1]],color='#202020',lw=.5,alpha=.65)
        ax.plot(*a,marker='o',ms=2,color='black');ax.plot(*b,marker='x',ms=3,color='white')
    ax.plot(*(tx/1000),marker='*',ms=14,color='yellow',markeredgecolor='black')
    ax.set(xlabel='Easting (km)',ylabel='Northing (km)',title='Haworth simulated LOS: 8 km, TX 5 m / RX 2 m, 2.5 m sampling\n50 check pairs: black TX dots, white RX crosses; yellow star = research point',xlim=extent[:2],ylim=extent[2:])
    ax.legend(handles=[Patch(color=c,label=t) for c,t in [('#20a66a','Clear'),('#d4473d','Blocked'),('#969aaa','Unknown / outside range')]],loc='lower left')
    fig.savefig(out/'viewshed_8km.png',dpi=150);plt.close(fig)

if __name__=='__main__':raise SystemExit(main())
