"""Assumed thermal envelopes and local dust events, shared by mission planning.

These inputs describe an effective thermal sink, not regolith measurements or a forecast.
"""
from itertools import combinations

from .costs import relay_limit

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


def optimize_environment(direct, relays, eligible, mission, without_satellite=None, evidence=None):
    """Search increasing relay count; all cases must pass, including N−1 if requested."""
    best, best_key = (), None
    first_winner = None
    for count in range(relay_limit(min(mission.max_relays, len(eligible)), mission.budget_usd, mission.relay_cost_usd) + 1):
        winners = []
        for selection in combinations(eligible, count):
            reports = {case: evaluate(direct[case], relays[case], selection, mission,
                None if without_satellite is None else (without_satellite[0][case], without_satellite[1][case])) for case in CASES}
            targets = [r[2] if mission.resilient else r[1] for r in reports.values()]
            key = (min(r['availability'] for r in targets), -max(r['longest'] for r in targets), -count)
            feasible = all(passes(r, mission) for r in targets)
            if evidence is not None:
                evidence.append(dict(selection=selection, key=key, feasible=feasible,
                    cases={case: dict(network=r[1], worst=r[2]) for case, r in reports.items()}))
            if best_key is None or key > best_key:
                best, best_key = selection, key
            if feasible:
                winners.append((key, selection))
        if winners:
            if evidence is None:
                return max(winners)[1], True
            if first_winner is None:
                first_winner = max(winners)[1]
    return (first_winner, True) if first_winner is not None else (best, False)
