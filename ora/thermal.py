"""Surviving the lunar night: a lumped thermal model and the survival energy budget.

Illustrative planning numbers. The default node is a small insulated electronics box; calibrate
heat capacity, emissivity, area and conductance against a real design before quoting results.
"""
from dataclasses import dataclass

import numpy as np

SIGMA = 5.670374419e-8  # W m^-2 K^-4
LUNAR_NIGHT_H = 354.0  # about 14.75 Earth days of darkness at most sites


@dataclass(frozen=True)
class NodeDesign:
    battery_wh: float = 600.0
    solar_w: float = 40.0  # panel output in full sunlight
    active_w: float = 6.0  # radio on, relaying
    survival_w: float = 0.5  # hibernating: clock and wake receiver only
    heater_w: float = 4.0  # electric heater, thermostat controlled
    rhu_w: float = 0.0  # radioisotope heat, no electrical draw (about 1 W thermal each)
    heat_capacity: float = 3000.0  # J/K
    emissivity: float = 0.03  # effective, through multi-layer insulation
    area: float = 0.15  # radiating area, m^2
    k_loss: float = 0.004  # conductive loss to the ground, W/K
    heater_on_k: float = 243.15  # thermostat set point (-30 C)
    kill_k: float = 213.15  # electronics do not recover below this (-60 C)
    radiator_on_k: float = 303.15  # above this (30 C) a louvred radiator opens to dump heat
    radiator_emissivity: float = 0.6
    duty: float = 1.0  # fraction of each hour a hibernating node wakes to relay
    hibernate: bool = False  # sleep whenever unlit


def step_temp(T, P_in, P_heater, dt, C, eps, A, k_loss, T_env):
    """One explicit step of the lumped model. T in K; C in J/K; A in m^2; k_loss in W/K."""
    rad = eps * SIGMA * A * (T ** 4 - T_env ** 4)
    cond = k_loss * (T - T_env)
    return T + (P_in + P_heater - rad - cond) * dt / C


def survival_energy_wh(power_w, hours=LUNAR_NIGHT_H):
    """Energy to keep a constant draw alive through the dark: E = P * t."""
    return power_w * hours


def heat_to_hold(T_hold, design, T_env):
    """Steady heat input (W) that keeps the box at ``T_hold`` against radiation and conduction."""
    return design.emissivity * SIGMA * design.area * (T_hold ** 4 - T_env ** 4) + design.k_loss * (T_hold - T_env)


def night_budget(design, hours=LUNAR_NIGHT_H, T_env=100.0):
    """Closed-form night budget: mean electrical draw, energy needed and battery margin."""
    awake = 0.0 if design.hibernate and design.duty <= 0 else (design.duty if design.hibernate else 1.0)
    electronics = design.survival_w + awake * (design.active_w - design.survival_w)
    needed_heat = heat_to_hold(design.heater_on_k, design, T_env)
    heater = float(np.clip(needed_heat - electronics - design.rhu_w, 0, design.heater_w))
    holds = electronics + design.rhu_w + heater >= needed_heat - 1e-9
    draw = electronics + heater
    energy = survival_energy_wh(draw, hours)
    return dict(electronics_w=electronics, heater_w=heater, draw_w=draw, heat_needed_w=needed_heat, holds_temperature=bool(holds),
                energy_wh=energy, battery_wh=design.battery_wh, margin_wh=design.battery_wh - energy,
                survives=bool(holds and design.battery_wh >= energy), hours=hours,
                endurance_h=design.battery_wh / draw if draw > 0 else float("inf"))


def simulate(design, lit, dt_h, T_env_lit=250.0, T_env_dark=100.0, beam_w=None, soc0=1.0, T0=273.15):
    """Time-step one node. lit: illumination fraction per step (0..1). beam_w: delivered beam power per step.

    Returns per-step arrays: soc (0..1), temp_k, mode (0 off, 1 hibernate, 2 active), heater_w,
    plus the step at which the node froze for good (or -1).
    """
    lit = np.asarray(lit, float)
    beam = np.zeros(len(lit)) if beam_w is None else np.broadcast_to(np.asarray(beam_w, float), lit.shape)
    soc, temp, mode, heat = (np.zeros(len(lit)) for _ in range(4))
    energy, T, frozen = design.battery_wh * soc0, T0, -1
    sub = max(1, int(np.ceil(dt_h * 3600 / 300)))
    dt = dt_h * 3600 / sub
    for i, sun in enumerate(lit):
        supply = design.solar_w * sun + beam[i]
        if frozen >= 0:
            state, electronics = 0, 0.0
        elif energy <= 0 and supply <= 0:
            state, electronics = 0, 0.0
        elif design.hibernate and sun <= 0 and beam[i] <= 0:
            state = 1
            electronics = design.survival_w + design.duty * (design.active_w - design.survival_w)
        else:
            state, electronics = 2, design.active_w
        T_env = T_env_dark + (T_env_lit - T_env_dark) * sun
        used = 0.0
        for _ in range(sub):
            heater = design.heater_w if (state and T < design.heater_on_k and (energy > 0 or supply > electronics)) else 0.0
            emissivity = design.radiator_emissivity if T > design.radiator_on_k else design.emissivity
            T = step_temp(T, electronics + design.rhu_w, heater, dt, design.heat_capacity, emissivity,
                          design.area, design.k_loss, T_env)
            used += heater * dt / 3600
            energy = float(np.clip(energy + (supply - electronics - heater) * dt / 3600, 0, design.battery_wh))
        if frozen < 0 and T < design.kill_k:
            frozen, state = i, 0
        soc[i], temp[i], mode[i], heat[i] = energy / max(design.battery_wh, 1e-9), T, state, used / dt_h
    return dict(soc=soc, temp_k=temp, mode=mode, heater_w=heat, frozen_step=frozen)


def minimum_battery(design, hours=LUNAR_NIGHT_H, T_env=100.0):
    """Smallest battery (Wh) that carries this design through the night, or None if it freezes anyway."""
    budget = night_budget(design, hours, T_env)
    return budget["energy_wh"] if budget["holds_temperature"] else None
