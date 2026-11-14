"""Blasting: a scheduled, spatially correlated failure, and how far ejecta fly.

With no air and one sixth of Earth's gravity, a fragment thrown at the same speed lands about
six times farther on the Moon than the drag-free Earth figure. Exclusion zones should start from
the lunar number and be calibrated against the literature before use.
"""
import numpy as np

G_MOON, G_EARTH = 1.62, 9.81


def ballistic_range(v, g, angle_deg=45.0):
    """Range of a projectile launched at speed v (m/s) on a flat, airless body."""
    return v ** 2 * np.sin(2 * np.radians(angle_deg)) / g


def blast_effects(G, pos, center, r_kill, r_dmg):
    """(nodes to remove, nodes with raised hazard). pos: {node: (x, y)} in metres. G is unused."""
    dead, hurt = [], []
    for n, (x, y) in pos.items():
        d = np.hypot(x - center[0], y - center[1])
        if d <= r_kill:
            dead.append(n)
        elif d <= r_dmg:
            hurt.append(n)
    return dead, hurt
