"""Dust as a state variable and a failure driver.

Electrostatically charged dust that sticks to everything is the top surface hazard in the
team's notes. Every number here is a parameter to sweep or calibrate, not a measured value.
"""
import numpy as np


def update_dust(load, dt, deposition_rate, cleaning_rate=0.0):
    """Dust load in normalized units (0 clean .. 1 fully fouled)."""
    return float(np.clip(load + (deposition_rate - cleaning_rate) * dt, 0.0, 1.0))


def node_hazard(base_rate, dust_load, k_dust=3.0, charging_factor=1.0):
    """Failure rate per unit time. k_dust and charging_factor are parameters, not measurements."""
    return base_rate * (1.0 + k_dust * dust_load) * charging_factor


def survival_prob(hazard, dt):
    return float(np.exp(-hazard * dt))


def failure_step(rng, base_rate, loads, dt, k_dust=3.0, charging=None):
    """First step at which a node fails given its dust load per step, or -1 if it survives.

    loads: dust load per step. charging: optional per-step charging factor.
    """
    loads = np.asarray(loads, float)
    charging = np.ones(len(loads)) if charging is None else np.asarray(charging, float)
    hazard = base_rate * (1.0 + k_dust * loads) * charging
    failed = rng.random(len(loads)) > np.exp(-hazard * dt)
    return int(np.argmax(failed)) if failed.any() else -1
