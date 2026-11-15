"""Assumed thermal envelopes and local dust events, shared by mission planning.

These inputs describe an effective thermal sink, not regolith measurements or a forecast.
"""
from dataclasses import replace
from itertools import combinations

import numpy as np

from ora.thermal import NodeDesign, simulate
from .network import evaluate, passes

CASES = ('nominal', 'cold', 'dust', 'cold_dust')


def node_states(mission, sunlight, known, distance_km=0, lander=False):
    hours = mission.minutes / 60
    duration = mission.lander_battery_hours if lander else mission.battery_hours
    charge = mission.lander_initial_charge_pct if lander else mission.initial_charge_pct
    design = NodeDesign(battery_wh=duration * mission.node_load_w,
                        solar_w=(1 + mission.charge_ratio) * mission.node_load_w,
                        heat_capacity=mission.heat_capacity, emissivity=mission.emissivity,
                        area=mission.radiating_area_m2, k_loss=mission.conductance_w_k,
                        heater_on_k=mission.heater_on_k, kill_k=mission.freeze_k,
                        active_w=mission.node_load_w, heater_w=mission.heater_w)
    time = np.arange(len(sunlight)) * hours
    # Integrate event overlap exactly, including events between mission samples.
    exposure = np.maximum(0, np.minimum(time + hours, mission.dust_start_h + mission.dust_duration_h)
                          - np.maximum(time, mission.dust_start_h))
    load, value = [], 0.0
    for dt in exposure:
        value = float(np.clip(value + mission.dust_deposition_per_day * dt / 24
                              - mission.dust_cleaning_per_day * hours / 24, 0, 1))
        load.append(value if distance_km <= mission.dust_radius_km else 0.0)
    load = np.asarray(load)
    states = {}
    for case in CASES:
        dust = load if 'dust' in case else np.zeros(len(load))
        state = simulate(design, sunlight, hours, T_env_lit=mission.lit_sink_k,
                         T_env_dark=mission.cold_sink_k if 'cold' in case else mission.dark_sink_k,
                         soc0=charge / 100, solar_factor=1 - dust * mission.dust_solar_loss, known=known)
        state['dust'] = dust
        state['solar_factor'] = 1 - dust * mission.dust_solar_loss
        state['available'] = state['mode'] == 2
        states[case] = state
    return states


def public_state(state, mission):
    return dict(temperature_c=(state['temp_k'] - 273.15).tolist(), sink_k=state['sink_k'].tolist(),
                heater_w=state['heater_w'].tolist(), dust_load=state['dust'].tolist(),
                solar_factor=state['solar_factor'].tolist(), soc=state['soc'].tolist(),
                available=state['available'].tolist(), reason=state['reason'],
                minimum_temperature_c=float(state['temp_k'].min() - 273.15),
                minimum_soc=float(state['soc'].min()), heater_energy_wh=float(state['heater_w'].sum() * mission.minutes / 60),
                frozen_hour=None if state['frozen_step'] < 0 else state['frozen_step'] * mission.minutes / 60)


def optimize_environment(direct, relays, eligible, mission):
    """Search increasing relay count; all cases must pass, including N−1 if requested."""
    best, best_key = (), None
    for count in range(min(mission.max_relays, len(eligible)) + 1):
        winners = []
        for selection in combinations(eligible, count):
            reports = {case: evaluate(direct[case], relays[case], selection, mission) for case in CASES}
            targets = [r[2] if mission.resilient else r[1] for r in reports.values()]
            key = (min(r['availability'] for r in targets), -max(r['longest'] for r in targets), -count)
            if best_key is None or key > best_key:
                best, best_key = selection, key
            if all(passes(r, mission) for r in targets):
                winners.append((key, selection))
        if winners:
            return max(winners)[1], True
    return best, False
