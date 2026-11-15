"""Scientific evaluation checks: area correction, boundaries, split safety."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from rasterio import Affine

from moonmesh_train.evaluation import area_and_scale, boundary_distances, score, select_scenarios
from moonmesh_train.los import PROJECTION


def test_stratified_selection_keeps_all_heights_and_final_keeps_all():
    rows=[]
    for i in range(10):
        for h in (5,10,20,50):
            rows.append(dict(transmitter_id=f't{i}',stratum=f's{i%5}',tx_height_agl_m=h,scenario_id=f't{i}_{h}'))
    frame=pd.DataFrame(rows)
    selected=select_scenarios(frame)
    assert len(selected)==24
    assert selected.stratum.nunique()==5
    assert (selected.groupby('transmitter_id').size()==4).all()
    pd.testing.assert_frame_equal(selected,select_scenarios(frame.sample(frac=1,random_state=3)))
    assert len(select_scenarios(frame,split='test'))==40
    with pytest.raises(ValueError,match='every scenario'):
        select_scenarios(frame,['t0_5'],split='test')
    allowed=select_scenarios(frame,['t0_5'],split='test',allow_final_subset=True)
    assert allowed.scenario_id.tolist()==['t0_5']
    with pytest.raises(ValueError):
        select_scenarios(frame,['foreign'])


def test_test_cli_requires_final_before_reading_data():
    script=Path(__file__).parents[1]/'scripts/10_evaluate.py'
    spec=importlib.util.spec_from_file_location('eval_cli',script)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with pytest.raises(SystemExit) as e:
        module.main(['--split','test'])
    assert e.value.code==2


def test_final_subset_cli_requires_explicit_opt_in(monkeypatch):
    script=Path(__file__).parents[1]/'scripts/10_evaluate.py'
    spec=importlib.util.spec_from_file_location('eval_subset_cli',script)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    frame=pd.DataFrame([dict(transmitter_id='t0',stratum='floor',tx_height_agl_m=5,scenario_id='t0_5')])
    selected=[]
    def run(args):
        selected.append(select_scenarios(frame,args.scenarios,args.split,args.allow_final_subset))
    monkeypatch.setattr(module,'run',run)
    argv=['--split','test','--final','--scenarios','t0_5']
    with pytest.raises(ValueError,match='Final test evaluates every scenario'):
        module.main(argv)
    module.main(argv+['--allow-final-subset'])
    assert selected[0].scenario_id.tolist()==['t0_5']


def test_final_subset_report_records_selection_and_all_predictor_decisions(tmp_path,monkeypatch):
    from moonmesh_train import evaluation
    monkeypatch.setattr(evaluation,'sha256_file',lambda path:'test-sha256')
    monkeypatch.setattr(evaluation,'commit',lambda:'test-commit')
    mask=np.array([[True,False],[True,False]])
    valid=np.ones_like(mask)
    values=score(mask,mask,valid,np.ones_like(mask,float),valid,
        np.zeros_like(mask,float),np.zeros_like(mask,float),0,np.array([0.]))
    rows=[dict(scenario_id='s0',predictor=p,dead_zone=k,group=g,level='all',label_origin='simulated',**values)
        for p in ('deterministic','distance','lightgbm') for k in ('terrain','rf') for g in ('all','stratum')]
    pd.DataFrame(rows).to_csv(tmp_path/'s0_metrics.csv',index=False)
    for i in range(5):
        pd.DataFrame([dict(scenario_id='s0',predictor=p,warm_ms=t,cold_ms=t,label_origin='simulated')
            for p,t in [('deterministic',100.),('distance',1.),('lightgbm',5.)]]).to_csv(tmp_path/f'runtime_{i}.csv',index=False)
    cfg=dict(_final_subset=True,_selection_rule='Test fixture selection rule',_split='test',_signature='test',
        acceptance='acceptance.yaml',dataset='unused',seed=42)
    evaluation.report(cfg,pd.DataFrame({'scenario_id':['s0']}),tmp_path)
    metrics=json.loads((tmp_path/'metrics.json').read_text())
    assert metrics['final_subset'] is True
    assert metrics['scenario_ids']==['s0']
    assert metrics['selection_rule']==cfg['_selection_rule']
    assert set(metrics['acceptance_by_predictor'])=={'deterministic','distance','lightgbm'}
    assert metrics['acceptance_by_predictor']['lightgbm']['min_speedup']['passed']
    summary=(tmp_path/'summary.md').read_text()
    assert cfg['_selection_rule'] in summary and 's0' in summary and 'final_subset: true' in summary
    assert json.loads((tmp_path/'decision.json').read_text())['ship']=='model'


def test_lunar_polar_pixel_area_uses_scale_squared():
    tr=Affine(20,0,100000,0,-20,100020)
    area,k=area_and_scale(tr,(1,1),PROJECTION)
    assert k[0,0]>1
    assert area[0,0]==pytest.approx(400/k[0,0]**2/1e6)
    with pytest.raises(ValueError):
        area_and_scale(tr,(1,1),'EPSG:3857')


def test_boundary_edt_in_metres_excludes_domain_edge():
    truth=np.zeros((12,12),bool); truth[:,6:]=True
    pred=np.zeros_like(truth); pred[:,8:]=True
    valid=np.ones_like(truth)
    _,dist,a,b=boundary_distances(truth,pred,valid,(20,20),np.ones_like(truth,float))
    assert np.all(dist==40)
    assert not a[0].any() and not b[-1].any()
    _,same,_,_=boundary_distances(truth,truth,valid,(20,20),np.ones_like(truth,float))
    assert np.all(same==0)
    _,missing,_,_=boundary_distances(truth,np.zeros_like(truth),valid,(20,20),np.ones_like(truth,float))
    assert missing is None


def test_dead_zone_area_denominators_and_near_threshold():
    truth=np.array([[1,1],[0,0]],bool)
    pred=np.array([[1,0],[1,0]],bool)
    valid=np.ones_like(truth)
    area=np.array([[1.,3.],[2.,4.]])
    margin=np.array([[0.,1.],[9.,10.]])
    result=score(truth,pred,valid,area,valid,margin,margin+2,3,np.array([20.,40.]))
    assert result['false_covered_frac']==.75
    assert result['false_covered_km2']==3
    assert result['area_error_km2']==-1
    assert result['precision']==pytest.approx(1/3)
    assert result['dead_iou']==pytest.approx(1/6)
    assert result['margin_bias_db']==2
    assert result['near_threshold_n']==2
    assert result['near_threshold_margin_mae_db']==2
