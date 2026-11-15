"""Independent search and sampled-outage evidence checks, without downloading data."""
from datetime import UTC, datetime, timedelta
from itertools import combinations
from math import comb
from pathlib import Path
from types import SimpleNamespace
import json
import unittest

import numpy as np

from lunar_core.horizon import HorizonMask
from moonmesh.diagnostics import (add_path, gate, horizon_evidence, optimization_evidence,
                                  outage_report, path_issues, sky_link)
from moonmesh.environment import CASES, optimize_environment
from moonmesh.models import Mission, Satellite
from moonmesh.network import metrics


class OutageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.times = tuple(datetime(2026, 11, 1, tzinfo=UTC)+timedelta(minutes=30*i) for i in range(4))
        self.end = self.times[-1]+timedelta(minutes=30)
        self.diagnostic = dict(cases=['cold_dust'], nodes={
            'Lander': dict(cases={'cold_dust': dict(available=[True]*4, gates=[gate('node_power', [True, False, False, True])])})},
            links={'Lander->Earth': dict(available=[True,False,False,True], gates=[
                gate('terrain_clearance', [True,False,False,True]),
                gate('rf_margin', [True,False,True,True])])}, paths={})
        add_path(self.diagnostic, 'DTE', ['Lander','Earth'], ['Lander->Earth'])

    def test_simultaneous_causes_and_changes_are_retained(self):
        path = self.diagnostic['paths']['DTE']
        outages = outage_report(path['available']['cold_dust'], [path], self.diagnostic,
                                 self.times, self.end, .5, 'cold_dust')
        self.assertEqual(len(outages), 1)
        self.assertEqual(outages[0]['hours'], 1)
        self.assertEqual(len(outages[0]['segments']), 2)
        first = outages[0]['segments'][0]['paths'][0]['issues']
        self.assertEqual({i['code'] for i in first}, {'node_power','terrain_clearance','rf_margin'})
        self.assertEqual(outages[0]['end'], self.times[3].isoformat())

    def test_rescued_path_does_not_create_network_outage(self):
        path = self.diagnostic['paths']['DTE']
        self.assertEqual(outage_report([True]*4, [path], self.diagnostic,
                                      self.times, self.end, .5, 'cold_dust'), [])

    def test_mission_boundary_and_injected_failure(self):
        path = self.diagnostic['paths']['DTE']
        outages = outage_report([False]*4, [path], self.diagnostic,
                               self.times, self.end, .5, 'cold_dust', ['Lander'])
        self.assertEqual(outages[0]['start_boundary'], 'mission_start')
        self.assertEqual(outages[0]['end_boundary'], 'mission_end')
        self.assertEqual(outages[0]['hours'], 2)
        self.assertTrue(any(issue['code']=='injected_failure' for issue in outages[0]['segments'][0]['paths'][0]['issues']))

    def test_unknown_rf_and_terrain_are_distinct_from_failure(self):
        mission = Mission(satellite=Satellite())
        point = SimpleNamespace(lat=-90.,lon=0.)
        horizon = HorizonMask(np.array([0.,180.]), np.array([0.,np.nan]), np.array([100.,np.nan]))
        data = dict(az=[0.,180.],el=[20.,20.],range_km=[500.,500.],margin=None)
        link = sky_link('Lander','S1',point,horizon,data,mission)
        self.assertEqual(link['available'], [True,False])
        self.assertEqual(link['gates'][0]['ok'], [True,None])
        self.assertEqual(link['gates'][-1]['ok'], [None,None])
        self.assertFalse(link['gates'][-1]['unknown_blocks'])
        self.assertIsNone(link['horizon']['rays'][1]['lat'])
        self.assertTrue(link['horizon']['rays'][0]['approximate'])
        self.assertEqual(link['horizon']['rays'][0]['azimuth_deg'],0.)
        json.dumps(link, allow_nan=False)

    def test_horizon_location_uses_sampled_ray_not_interpolated_bearing(self):
        horizon = HorizonMask(np.array([0.,180.]), np.array([0.,1.]), np.array([100.,200.]))
        result = horizon_evidence(SimpleNamespace(lat=-89.,lon=10.), horizon, [90.])
        self.assertEqual(result['weight'], [.5])
        self.assertEqual([r['azimuth_deg'] for r in result['rays']], [0.,180.])


def independent_metric(flags, hours):
    longest = current = total = 0
    for up in flags:
        if up:
            current = 0
        else:
            current += 1
            total += 1
            longest = max(current,longest)
    return sum(flags)/len(flags)*100, longest*hours, total*hours


class SearchEvidenceTests(unittest.TestCase):
    def reference(self, direct, relays, mission, limit, fallback=None):
        best, best_key = (), None
        for count in range(limit+1):
            winners=[]
            for selection in combinations(range(len(relays[CASES[0]])),count):
                scores=[]
                feasible=True
                for case in CASES:
                    def online(chosen, base=direct, paths=relays):
                        return [bool(base[case][i]) or any(bool(paths[case][j][i]) for j in chosen) for i in range(len(base[case]))]
                    variants=[online(selection)]
                    if mission.resilient:
                        variants=[online(tuple(j for j in selection if j!=removed)) for removed in selection] or variants
                        if fallback:
                            variants.append(online(selection,*fallback))
                    reports=[independent_metric(flags,mission.minutes/60) for flags in variants]
                    availability=min(r[0] for r in reports)
                    longest=max(r[1] for r in reports)
                    scores.append((availability,longest))
                    feasible &= availability>=mission.availability and longest<=mission.blackout_hours
                key=(min(s[0] for s in scores),-max(s[1] for s in scores),-count)
                if best_key is None or key>best_key:
                    best,best_key=selection,key
                if feasible:
                    winners.append((key,selection))
            if winners:
                return max(winners)[1],True
        return best,False

    def test_collected_search_matches_independent_enumeration_and_legacy(self):
        random=np.random.default_rng(37)
        direct={c:random.random(12)<.3 for c in CASES}
        relays={c:[random.random(12)<.5 for _ in range(5)] for c in CASES}
        fallback=({c:np.zeros(12,bool) for c in CASES},{c:[np.zeros(12,bool) for _ in range(5)] for c in CASES})
        for resilient in (False,True):
            for limit in (0,1,3):
                for backup in (None,fallback):
                    mission=Mission(max_relays=3,budget_usd=limit*100,relay_cost_usd=100,
                                    resilient=resilient,availability=60,blackout_hours=1)
                    records=[]
                    result=optimize_environment(direct,relays,list(range(5)),mission,backup,records)
                    self.assertEqual(result,self.reference(direct,relays,mission,limit,backup))
                    self.assertEqual(result,optimize_environment(direct,relays,list(range(5)),mission,backup))
                    self.assertEqual(len(records),sum(comb(5,k) for k in range(limit+1)))

    def test_ties_removals_replacements_and_overlap(self):
        mission=Mission(max_relays=2,resilient=True,availability=100,blackout_hours=0)
        direct={c:np.zeros(4,bool) for c in CASES}
        relays={c:[np.ones(4,bool) for _ in range(3)] for c in CASES}
        records=[]
        selected,feasible=optimize_environment(direct,relays,[0,1,2],mission,evidence=records)
        self.assertEqual(selected,(1,2))
        self.assertTrue(feasible)
        options=[dict(id=f'R{i+1}') for i in range(3)]
        evidence=optimization_evidence(records,selected,options,direct,relays,mission,None)
        self.assertEqual(evidence['tie_count'],2)
        self.assertEqual(len(evidence['alternatives']),2)
        for marginal in evidence['marginal']:
            self.assertEqual(marginal['availability_gain_pp'],0)
            self.assertEqual(marginal['recovered_hours'],0)
            self.assertIsNotNone(marginal['replacement'])
        for config in evidence['configurations'].values():
            self.assertEqual(metrics(config['timeline'],.5),config['network'])


DATA=Path(__file__).resolve().parents[1]/'.moonmesh-data'
@unittest.skipUnless((DATA/'terrain/haworth_near20km.npz').is_file() and (DATA/'kernels/de440s.bsp').is_file(),
                     'Existing cached Haworth terrain and kernels are required')
class IntegratedEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from moonmesh.science import scenario
        cls.result=scenario(Mission(end='2026-11-04',satellite=Satellite()),include_map=False)

    def test_all_cases_and_combined_failures_reconstruct_production_timelines(self):
        r=self.result
        d=r['diagnostics']
        selected=[p for p in d['paths'].values() if p['relay'] is None or p['relay'] in r['recommended']]
        for key,state in r['failure_states'].items():
            disabled=[] if key=='nominal' else key.split(',')
            for case,report in state['cases'].items():
                flags=np.zeros(len(r['times']),bool)
                for path in selected:
                    if not set(disabled).intersection(path['nodes']):
                        flags|=path['available'][case]
                self.assertEqual(report['combined'],flags.tolist())
                self.assertEqual(report['metrics'],metrics(flags,.5))
                self.assertAlmostEqual(sum(o['hours'] for o in report['outages']),report['metrics']['total'])
                for outage in report['outages']:
                    self.assertTrue(outage['segments'])
                    self.assertTrue(outage['causes'])
            self.assertEqual(state['combined'],state['cases']['cold_dust']['combined'])

    def test_selected_search_evidence_and_export_agree(self):
        from io import BytesIO
        from zipfile import ZipFile
        from moonmesh.excel import ExportRequest,export_workbook
        r=self.result
        e=r['optimization_evidence']
        self.assertEqual(e['configurations'][e['selected']]['relay_ids'],r['recommended'])
        self.assertEqual(e['configurations'][e['selected']]['network'],r['network'])
        self.assertEqual(e['configurations'][e['selected']]['worst'],r['worst'])
        json.dumps(r,allow_nan=False)
        archive=ZipFile(BytesIO(export_workbook(ExportRequest(result=r,disabled=['S1'],environment_case='dust'))))
        sheets=archive.read('xl/workbook.xml').decode()
        for name in ('Network Outages','Outage Causes','Link Evidence','Node Evidence','Relay Alternatives','Marginal Gains'):
            self.assertIn(name,sheets)


if __name__=='__main__':
    unittest.main()
