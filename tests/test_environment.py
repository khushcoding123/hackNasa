"""Environmental service checks without downloading NASA data."""
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

import numpy as np
from pydantic import ValidationError

from lunar_core.terrain import TerrainGrid
from moonmesh.environment import CASES, node_states, optimize_environment
from moonmesh.models import Mission
from moonmesh.science import scenario
from ora.thermal import NodeDesign, simulate


class ThermalServiceTests(TestCase):
    def test_battery_cannot_supply_a_whole_interval_after_it_empties(self):
        state = simulate(NodeDesign(battery_wh=1, active_w=6, heater_w=0), [0], 1)
        self.assertEqual(state['mode'][0], 0)
        self.assertEqual(state['soc'][0], 0)
        self.assertEqual(state['reason'], ['Insufficient power'])

    def test_heater_uses_energy_and_cold_can_kill_a_powered_node(self):
        box = NodeDesign(battery_wh=10000, active_w=.1, heater_w=0, heat_capacity=1000)
        warm = simulate(box, np.zeros(24), 1, T_env_dark=250)
        cold = simulate(box, np.zeros(24), 1, T_env_dark=40)
        self.assertEqual(warm['frozen_step'], -1)
        self.assertGreaterEqual(cold['frozen_step'], 0)
        self.assertGreater(cold['soc'][-1], .9)
        heated = simulate(replace(box, heater_w=5), np.zeros(24), 1, T_env_dark=100)
        self.assertGreater(heated['heater_w'].sum(), 0)
        self.assertLess(heated['soc'][-1], warm['soc'][-1])

    def test_recharge_recovers_power_but_does_not_revive_frozen_node(self):
        box = NodeDesign(battery_wh=1, solar_w=20)
        state = simulate(box, [0, 1], 1, T_env_dark=250, soc0=0)
        self.assertEqual(state['mode'].tolist(), [0, 2])
        frozen = simulate(box, [0, 1], 1, T_env_dark=20, T0=100)
        self.assertEqual(frozen['mode'].tolist(), [0, 0])

    def test_extreme_valid_heat_loss_parameters_remain_finite(self):
        state = simulate(NodeDesign(heat_capacity=100, emissivity=1, area=100, k_loss=10), [0, 1], 1,
                         T_env_dark=20, T_env_lit=400)
        self.assertTrue(np.isfinite(state['temp_k']).all())
        self.assertTrue((state['temp_k'] > 0).all())

    def test_unknown_environment_never_receives_service_credit(self):
        state = simulate(NodeDesign(), [1, 1], 1, known=[False, True])
        self.assertEqual(state['mode'].tolist(), [0, 2])
        self.assertEqual(state['reason'][0], 'Unknown environment')


class EnvironmentTests(TestCase):
    def test_dust_event_timing_locality_cleaning_and_determinism(self):
        m = Mission(minutes=60, dust_start_h=.5, dust_duration_h=1, dust_deposition_per_day=1)
        args = (m, np.ones(4), np.ones(4, bool))
        states = node_states(*args)
        np.testing.assert_allclose(states['dust']['dust'], [1/48, 1/24, 1/24, 1/24])
        self.assertTrue((states['nominal']['dust'] == 0).all())
        self.assertTrue((node_states(*args, distance_km=9)['dust']['dust'] == 0).all())
        np.testing.assert_array_equal(states['cold_dust']['available'], node_states(*args)['cold_dust']['available'])
        clean = node_states(m.model_copy(update={'dust_cleaning_per_day': .5}), np.ones(4), np.ones(4, bool))
        self.assertLess(clean['dust']['dust'][-1], states['dust']['dust'][-1])

    def test_dust_reduces_solar_service_even_in_sunlight(self):
        m = Mission(minutes=60, battery_hours=0, initial_charge_pct=0, dust_start_h=0,
                    dust_duration_h=24, dust_deposition_per_day=1, dust_solar_loss=1)
        state = node_states(m, np.ones(48), np.ones(48, bool))
        self.assertTrue(state['nominal']['available'].all())
        self.assertFalse(state['dust']['available'][-1])
        self.assertAlmostEqual(state['dust']['solar_factor'][-1], 0)

    def test_optimizer_requires_all_cases_and_n_minus_one(self):
        m = Mission(availability=100, blackout_hours=0, max_relays=2)
        direct = {c: np.zeros(4, bool) for c in CASES}
        relays = {c: [np.ones(4, bool), np.ones(4, bool)] for c in CASES}
        relays['cold_dust'][0] = np.zeros(4, bool)
        self.assertEqual(optimize_environment(direct, relays, [0, 1], m), ((1,), True))
        self.assertFalse(optimize_environment(direct, relays, [0, 1], m.model_copy(update={'resilient': True}))[1])
        relays['cold_dust'][0] = np.ones(4, bool)
        self.assertEqual(optimize_environment(direct, relays, [0, 1], m.model_copy(update={'resilient': True})), ((0, 1), True))

    def test_invalid_envelopes_are_rejected(self):
        for change in ({'cold_sink_k': 200}, {'dust_solar_loss': 1.1}, {'dark_sink_k': float('nan')}):
            with self.assertRaises(ValidationError):
                Mission(**change)

    def test_full_mission_dust_changes_recommendation_and_outages(self):
        grid = TerrainGrid(np.zeros((30, 30)), -1500, 1500, 100)
        class Horizon:
            azimuth_deg = np.array([0., 180.])
            elevation_deg = np.zeros(2)
            distance_m = np.ones(2)
            def at(self, azimuth):
                return np.zeros_like(azimuth, dtype=float)
        def visible(site, point, times, mission):
            count = len(times)
            # Direct link blocked; relay has an Earth link and full sunlight.
            earth = np.full(count, point.mast > 2)
            return earth, np.ones(count, bool), Horizon(), dict(solar_known=[True]*count,
                reason=['Available' if earth[0] else 'Terrain obstruction']*count,
                status=['reliable']*count, rf_margin=[100.]*count,
                earth_az=[0.]*count, earth_el=np.where(earth, 10., -10.).tolist(),
                earth_range_km=[384400.]*count)
        candidates = [dict(id='R01',lat=-90.,lon=0.,elevation=0.,distance=1.,slope=0.,x=0.,y=0.)]
        m = Mission(start='2026-11-01',end='2026-11-03',minutes=60, availability=100,blackout_hours=0,
                    battery_hours=0, dust_start_h=0, dust_duration_h=24,
                    dust_deposition_per_day=1,dust_solar_loss=1,lander_battery_hours=100, max_relays=1)
        with patch('moonmesh.science.load_kernels'), patch('moonmesh.science.terrain', return_value=grid), \
             patch('moonmesh.science.SITES', {'haworth': ('Test', -90., 0.)}), \
             patch('moonmesh.science.slopes', return_value=np.zeros((30,30))), \
             patch('moonmesh.science.visibility', side_effect=visible), \
             patch('moonmesh.science.candidates', side_effect=lambda *a: [dict(c) for c in candidates]), \
             patch('moonmesh.science.surface_path', return_value=(True,1.)):
            dusty = scenario(m, include_map=False)
            clean = scenario(m.model_copy(update={'dust_deposition_per_day': 0}), include_map=False)
        self.assertTrue(clean['feasible'])
        self.assertFalse(dusty['feasible'])
        self.assertGreater(dusty['network']['longest'], clean['network']['longest'])
        self.assertFalse(dusty['environment']['cases']['cold_dust']['meets'])
        self.assertEqual(clean['failure_states']['nominal']['metrics'], clean['network'])
        self.assertEqual(len(dusty['candidates'][0]['environment']['cold_dust']['temperature_c']), 48)
