"""Surviving the lunar night: a lumped thermal model and the survival energy budget.

Illustrative planning numbers. The default node is a small insulated electronics box; calibrate
heat capacity, emissivity, area and conductance against a real design before quoting results.
"""
from dataclasses import dataclass
from math import expm1

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
    """Frozen-conductance exponential step; stable even for a small uninsulated box.

    Radiation is factored as G(T, sink) * (T - sink). Holding G over one
    internal step avoids explicit Euler cooling below absolute zero at large G/C.
    """
    conductance = k_loss + eps * SIGMA * A * (T + T_env) * (T*T + T_env*T_env)
    if conductance <= 0:
        return T + (P_in + P_heater) * dt / C
    equilibrium = T_env + (P_in + P_heater) / conductance
    return T + (equilibrium - T) * (-expm1(-conductance * dt / C))


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


def simulate(design, lit, dt_h, T_env_lit=250.0, T_env_dark=100.0, beam_w=None, soc0=1.0, T0=273.15,
             solar_factor=None, known=None):
    """Conserve electrical energy and test thermal survival at every internal step.

    Environment temperatures may be arrays. Unknown samples receive no solar credit or
    service credit. Output states describe complete intervals; a partial powered interval
    cannot count as continuous communication. Freezing is irreversible in this model.
    """
    lit = np.asarray(lit, float)
    if lit.ndim != 1 or dt_h <= 0 or not 0 <= soc0 <= 1:
        raise ValueError("Expected a one-dimensional illumination series, positive step and charge in 0..1.")
    valid = np.isfinite(lit) if known is None else np.asarray(known, bool) & np.isfinite(lit)
    sun = np.clip(np.nan_to_num(lit), 0, 1)
    sun = np.where(valid, sun, 0)
    beam = np.zeros(len(sun)) if beam_w is None else np.broadcast_to(np.asarray(beam_w, float), sun.shape)
    factor = np.ones(len(sun)) if solar_factor is None else np.broadcast_to(np.asarray(solar_factor, float), sun.shape)
    dark = np.broadcast_to(np.asarray(T_env_dark, float), sun.shape)
    light = np.broadcast_to(np.asarray(T_env_lit, float), sun.shape)
    sink = dark + (light - dark) * sun
    if not np.all(np.isfinite(sink)) or np.any(sink <= 0) or np.any(~np.isfinite(factor)) or np.any((factor < 0) | (factor > 1)):
        raise ValueError("Thermal sinks must be finite positive kelvin; solar factors must be in 0..1.")
    soc, temp, mode, heat = (np.zeros(len(sun)) for _ in range(4))
    reasons, energy, T, frozen = [], design.battery_wh * soc0, T0, -1
    sub = max(1, int(np.ceil(dt_h * 3600 / 60)))
    dt = dt_h * 3600 / sub
    for i, illumination in enumerate(sun):
        supply = design.solar_w * illumination * factor[i] + beam[i]
        full_service, used, lowest = True, 0.0, T
        asleep = design.hibernate and illumination <= 0 and beam[i] <= 0
        desired = design.survival_w + design.duty * (design.active_w - design.survival_w) if asleep else design.active_w
        for _ in range(sub):
            available = supply + energy * 3600 / dt
            electronics = min(desired, available) if frozen < 0 else 0.0
            heater = min(design.heater_w, max(0, available - electronics)) if frozen < 0 and T < design.heater_on_k else 0.0
            if electronics < desired - 1e-9 or frozen >= 0:
                full_service = False
            emissivity = design.radiator_emissivity if T > design.radiator_on_k else design.emissivity
            T = step_temp(T, electronics + design.rhu_w, heater, dt, design.heat_capacity, emissivity,
                          design.area, design.k_loss, sink[i])
            lowest = min(lowest, T)
            used += heater * dt / 3600
            energy = float(min(design.battery_wh, max(0, energy + (supply - electronics - heater) * dt / 3600)))
            if frozen < 0 and T < design.kill_k:
                frozen, full_service = i, False
        reason = "Unknown environment" if not valid[i] else "Frozen" if frozen >= 0 else "Insufficient power" if not full_service else "Available"
        state = (1 if asleep else 2) if valid[i] and full_service and frozen < 0 else 0
        soc[i], temp[i], mode[i], heat[i] = energy / max(design.battery_wh, 1e-9), lowest, state, used / dt_h
        reasons.append(reason)
    return dict(soc=soc, temp_k=temp, mode=mode, heater_w=heat, frozen_step=frozen, reason=reasons, sink_k=sink)


def minimum_battery(design, hours=LUNAR_NIGHT_H, T_env=100.0):
    """Smallest battery (Wh) that carries this design through the night, or None if it freezes anyway."""
    budget = night_budget(design, hours, T_env)
    return budget["energy_wh"] if budget["holds_temperature"] else None
