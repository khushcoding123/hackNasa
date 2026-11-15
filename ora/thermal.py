"""Surviving the lunar night: a lumped thermal model and the survival energy budget.

Illustrative planning numbers. The default node is a small insulated electronics box; calibrate
heat capacity, emissivity, area and conductance against a real design before quoting results.
"""
from dataclasses import dataclass
from math import expm1, inf

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
    sub = max(1, int(np.ceil(dt_h * 3600 / 60)))
    dt = dt_h * 3600 / sub
    soc, temp, mode, heat, reasons, frozen = _integrate(design, sun.tolist(), factor.tolist(), beam.tolist(), sink.tolist(),
                                                        valid.tolist(), float(design.battery_wh * soc0), float(T0), sub, dt, dt_h)
    return dict(soc=np.array(soc), temp_k=np.array(temp), mode=np.array(mode, float), heater_w=np.array(heat),
                frozen_step=frozen, reason=reasons, sink_k=sink)


def _charge(energy, battery_wh):
    """Battery clamp, ``float(min(battery_wh, max(0, energy)))`` with the same tie behaviour."""
    energy = energy if energy > 0 else 0.0
    return energy if energy < battery_wh else battery_wh


def _quiet_interval(alive, energy, supply, desired, heater_w, battery_wh, sub, dt):
    """Decide the electrical branches of a whole interval in advance, when that is exact.

    Returns ``(electronics, heater draw when on, charge increment with heater off, with heater on,
    heater-on case valid)`` or None. It is exact when at every internal step the electronics and
    heater draws are the ones returned and adding the increment reproduces the clamped charge update:

    * pinned: the clamped update returns the same charge (full and charging, empty and short of
      supply, or no net flow), so every draw is a function of the thermostat alone; or
    * moving: neither clamp can act and the charge stays high enough for the full draws.
      Floating-point rounding is monotone, so bounds on the charge also bound ``available``;
      the margin in ``_charge_floor`` covers the rounding accumulated over the interval.

    A frozen node draws nothing, so only the clamp matters for it.
    """
    available = supply + energy * 3600 / dt
    if alive:
        electronics = desired if not available < desired else available
        spare = available - electronics
        spare = spare if spare > 0 else 0.0  # min(heater_w, max(0, spare)), same ties
        heater_on = spare if spare < heater_w else heater_w
    else:
        electronics = heater_on = 0.0
    if _charge(energy + (supply - electronics - 0.0) * dt / 3600, battery_wh) == energy:
        on_ok = heater_on == 0.0 or _charge(energy + (supply - electronics - heater_on) * dt / 3600, battery_wh) == energy
        return electronics, heater_on, 0.0, 0.0, on_ok
    if not alive:
        d_off = (supply - 0.0 - 0.0) * dt / 3600
        return (0.0, 0.0, d_off, d_off, True) if _charge_floor(energy, (d_off,), battery_wh, sub) is not None else None
    d_off, d_on = (supply - desired - 0.0) * dt / 3600, (supply - desired - heater_w) * dt / 3600
    floor = _charge_floor(energy, (d_off,), battery_wh, sub)
    if floor is None or supply + floor * 3600 / dt < desired:
        return None
    floor = _charge_floor(energy, (d_off, d_on), battery_wh, sub)
    on_ok = floor is not None and not supply + floor * 3600 / dt - desired < heater_w
    return desired, heater_w, d_off, d_on, on_ok


def _charge_floor(energy, increments, battery_wh, sub):
    """A lower bound on the charge over ``sub`` steps of these increments if neither clamp can act."""
    margin = 1e-12 * sub * (abs(battery_wh) + abs(energy) + 1.0)
    rise, fall = max(increments), min(increments)
    if rise > 0 and not energy + sub * rise + margin < battery_wh:
        return None
    if fall >= 0:
        return energy  # never falls: rounding is monotone, and a charge of exactly 0 clamps to itself
    floor = energy + sub * fall - margin
    return floor if floor > 0 else None


def _integrate(design, sun, factor, beam, sink, valid, energy, T, sub, dt, dt_h):
    """The internal-step loop of ``simulate``.

    Each internal step is the original update: ``step_temp`` and the battery clamp, in the same
    operation order (or an exact IEEE identity of it), so the results are bit-for-bit unchanged.
    The speed comes from Python floats, hoisted constants and deciding the electrical branches once
    per interval where that is exact (``_quiet_interval``).
    """
    B, solar_w, hibernate = float(design.battery_wh), float(design.solar_w), design.hibernate
    sleep_w = float(design.survival_w + design.duty * (design.active_w - design.survival_w))
    active_w, hw, rhu = float(design.active_w), float(design.heater_w), float(design.rhu_w)
    hon, ron, kill = float(design.heater_on_k), float(design.radiator_on_k), float(design.kill_k)
    C, kl = float(design.heat_capacity), float(design.k_loss)
    # step_temp's radiative term is eps * SIGMA * A * (T + T_env) * (...); its left-hand prefix is constant.
    rad_closed, rad_open = design.emissivity * SIGMA * design.area, design.radiator_emissivity * SIGMA * design.area
    # step_temp's T + (eq - T) * -expm1(-g * dt / C), rewritten with exact sign identities:
    # (-g) * dt == g * (-dt) and x + y * (-e) == x - y * e in IEEE arithmetic.
    back = -dt
    lean = B >= 0 and C > 0 and kl > 0 and rad_closed >= 0 and rad_open >= 0 and hon <= ron  # designs the fast path covers
    soc_scale = max(B, 1e-9)
    soc, temp, mode, heat, reasons = [], [], [], [], []
    frozen, alive = -1, True
    energy = energy + 0.0  # a -0.0 start charge reads as 0.0 after the first clamp; the pinned path never clamps
    for i in range(len(sun)):
        illumination, Te = sun[i], sink[i]
        Te2 = Te * Te
        supply = solar_w * illumination * factor[i] + beam[i]
        full_service, used, lowest = True, 0.0, T
        asleep = hibernate and illumination <= 0 and beam[i] <= 0
        desired = sleep_w if asleep else active_w
        short = desired - 1e-9
        start = 0
        quiet = _quiet_interval(alive, energy, supply, desired, hw, B, sub, dt) if lean and T > 0 and not (alive and T < kill) else None
        if quiet is not None:
            electronics, heater_on, d_off, d_on, on_ok = quiet
            power_off, power_on, used_on = electronics + rhu + 0.0, electronics + rhu + heater_on, heater_on * dt / 3600
            if not (power_off >= 0 and power_on >= 0):
                quiet = None
        if quiet is not None:
            # The general step below with its branches taken in advance: the electrical ones are
            # fixed for the interval (_quiet_interval); with heater_on_k <= radiator_on_k at most
            # one of heater and radiator is on; and the conductance stays positive, because T
            # starts positive and each step moves it toward a positive equilibrium.
            if alive and electronics < short:
                full_service = False
            freeze_k = kill if alive else -inf
            for k in range(sub):
                if T > ron:
                    conductance = kl + rad_open * (T + Te) * (T*T + Te2)
                    T = T - (Te + power_off / conductance - T) * expm1(conductance * back / C)
                    energy = energy + d_off
                elif T < hon:
                    if not on_ok:
                        start = k
                        break
                    used += used_on  # adding 0.0 when the heater has nothing to draw changes nothing
                    conductance = kl + rad_closed * (T + Te) * (T*T + Te2)
                    T = T - (Te + power_on / conductance - T) * expm1(conductance * back / C)
                    energy = energy + d_on
                else:
                    conductance = kl + rad_closed * (T + Te) * (T*T + Te2)
                    T = T - (Te + power_off / conductance - T) * expm1(conductance * back / C)
                    energy = energy + d_off
                if T < lowest:
                    lowest = T
                    if T < freeze_k:  # lowest started at or above kill, so a freeze always lands here
                        frozen, alive, full_service = i, False, False
                        start = k + 1
                        break
            else:
                start = sub
        for _ in range(start, sub):
            if alive:
                available = supply + energy * 3600 / dt
                electronics = desired if not available < desired else available
                if T < hon:
                    spare = available - electronics
                    spare = spare if spare > 0 else 0.0  # min(hw, max(0, spare)), same ties
                    heater = spare if spare < hw else hw
                else:
                    heater = 0.0
                if electronics < short:
                    full_service = False
            else:
                electronics = heater = 0.0
            if T > ron:
                conductance = kl + rad_open * (T + Te) * (T*T + Te2)
            else:
                conductance = kl + rad_closed * (T + Te) * (T*T + Te2)
            power = electronics + rhu + heater
            if conductance <= 0.0:
                T = T + power * dt / C
            else:
                T = T - (Te + power / conductance - T) * expm1(conductance * back / C)
            if T < lowest:
                lowest = T
            if heater:
                used += heater * dt / 3600
            energy = energy + (supply - electronics - heater) * dt / 3600
            energy = energy if energy > 0 else 0.0  # _charge, inlined
            energy = energy if energy < B else B
            if alive and T < kill:
                frozen, alive, full_service = i, False, False
        if not valid[i]:
            reasons.append("Unknown environment")
        elif not alive:
            reasons.append("Frozen")
        else:
            reasons.append("Available" if full_service else "Insufficient power")
        mode.append((1 if asleep else 2) if valid[i] and full_service and alive else 0)
        soc.append(energy / soc_scale)
        temp.append(lowest)
        heat.append(used / dt_h)
    return soc, temp, mode, heat, reasons, frozen


def minimum_battery(design, hours=LUNAR_NIGHT_H, T_env=100.0):
    """Smallest battery (Wh) that carries this design through the night, or None if it freezes anyway."""
    budget = night_budget(design, hours, T_env)
    return budget["energy_wh"] if budget["holds_temperature"] else None
