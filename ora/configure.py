"""Modular node configurator: the node changes with the ground it stands on.

Each node is treated like a small satellite: power, thermal, comms, structure. ``configure``
picks modules from local surface conditions and ``parts_list`` totals them from the catalogue in
``modules.yaml``. When conditions change, ``swaps`` reports what has to be exchanged.
"""
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

CATALOG = Path(__file__).with_name("modules.yaml")
THRESHOLDS = {"lit_min": 0.5, "slope_max": 20.0, "rock_min": 0.5, "dust_min": 0.4}  # parameters: tune or sweep


@dataclass
class SiteFeatures:
    lit_fraction: float  # 0..1 from the twin (illumination)
    earth_vis: float  # 0..1 direct-to-Earth availability
    slope_deg: float
    rockiness: float  # 0..1 sub-grid roughness / boulder index
    dust_risk: float  # 0..1
    blast_risk: bool
    role: str  # 'mesh' | 'trunk' | 'gateway'


@dataclass
class NodeConfig:
    modules: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def configure(f: SiteFeatures, thresholds=None):
    t = {**THRESHOLDS, **(thresholds or {})}
    c = NodeConfig(modules=["core"])
    # power
    if f.lit_fraction >= t["lit_min"]:
        c.modules += ["power:solar+battery"]
    else:
        c.modules += ["power:battery", "power:laser_receiver"]
        c.notes.append("dark site: needs beam or heater for night survival")
    # thermal
    c.modules += ["thermal:insulation"] + (["thermal:phase_change"] if f.lit_fraction < t["lit_min"] else [])
    # antenna by role
    c.modules += {"gateway": ["antenna:directional", "antenna:optical"],
                  "trunk": ["antenna:optical", "antenna:omni"],
                  "mesh": ["antenna:omni"]}[f.role]
    # anchoring
    c.modules += ["anchor:rock_bolt"] if f.rockiness >= t["rock_min"] or f.slope_deg > t["slope_max"] \
        else ["anchor:regolith_spike"]
    # dust and shock
    if f.dust_risk >= t["dust_min"]:
        c.modules += ["dust:shield"]
    if f.blast_risk:
        c.modules += ["shock:isolated_mount"]
    return c


@lru_cache(maxsize=1)
def catalog():
    return yaml.safe_load(CATALOG.read_text())


def parts_list(config):
    """Modules with their catalogue role, and total mass and power (placeholder catalogue values)."""
    entries = catalog()
    parts = [dict(module=m, **entries.get(m, {"mass_kg": 0.0, "power_w": 0.0, "role": "not in catalogue"})) for m in config.modules]
    assumed = [p["module"] for p in parts if p.get("assumed", True)]
    return dict(parts=parts, notes=list(config.notes), mass_kg=sum(p["mass_kg"] for p in parts),
                power_w=sum(p["power_w"] for p in parts), assumed_modules=assumed)


def swaps(before, after):
    """What changes between two configurations: modules to add and remove, and the mass and power difference."""
    entries = catalog()
    add = [m for m in after.modules if m not in before.modules]
    remove = [m for m in before.modules if m not in after.modules]
    total = lambda modules, key: sum(entries.get(m, {}).get(key, 0.0) for m in modules)
    return dict(add=add, remove=remove, mass_kg=total(add, "mass_kg") - total(remove, "mass_kg"),
                power_w=total(add, "power_w") - total(remove, "power_w"))
