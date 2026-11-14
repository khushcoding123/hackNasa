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
        ours=None if not result['valid'][0] else bool(result['los'][0])
        pairs.append({'pair':i,'tx_xy':start.tolist(),'rx_xy':end.tolist(),'h_tx':ht,'h_rx':hr,'app_los':app,'ours_los':ours,
                      'distance_m':arc*1000,'min_clearance_m':float(result['min_clearance_m'][0]),'match':app==ours,
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
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'Reports: {out}',flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
