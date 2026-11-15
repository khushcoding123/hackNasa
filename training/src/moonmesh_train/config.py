"""Load run configurations, rejecting incomplete radio metadata before a run."""

import math
import os
from pathlib import Path
from typing import Any

import yaml

from .paths import repo_root

RADIO_FIELDS = (
    "frequency_ghz", "tx_power_dbm", "tx_gain_dbi", "rx_gain_dbi",
    "fixed_loss_db", "receiver_sensitivity_dbm", "required_margin_db", "max_range_m",
)


class ConfigError(ValueError):
    """A configuration cannot safely describe an executable experiment."""


def configs_dir() -> Path:
    return repo_root() / "training" / "configs"


def numba_threads(requested: int) -> int:
    """The configured Numba thread count, capped at what this computer allows.

    Numba raises "The number of threads must be between 1 and N" when asked for more threads than
    NUMBA_NUM_THREADS (by default the number of CPUs), so a config written on a 16-thread PC would
    otherwise break surface coverage on a 4-thread laptop.
    """
    import numba
    return max(1, min(int(requested), numba.config.NUMBA_NUM_THREADS))


def cpu_threads(requested: int) -> int:
    """LightGBM accepts any thread count but slows down when given more threads than CPUs."""
    return max(1, min(int(requested), os.cpu_count() or 1))


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            result = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read configuration {path}: {exc}") from exc
    if not isinstance(result, dict) or not all(isinstance(key, str) for key in result):
        raise ConfigError(f"Expected a YAML mapping with string keys in {path}")
    return result


def load_radio_profile(name: str, path: str | Path | None = None) -> dict[str, float]:
    """Validate only the selected profile; incomplete unused profiles are allowed."""
    source = Path(path) if path is not None else configs_dir() / "radio_profiles.yaml"
    profiles = _load_mapping(source)
    if not isinstance(name, str) or name not in profiles:
        raise ConfigError(f"Unknown radio profile {name!r} in {source}")
    profile = profiles[name]
    if not isinstance(profile, dict):
        raise ConfigError(f"Radio profile {name!r} must be a mapping in {source}")
    missing = [field for field in RADIO_FIELDS if field not in profile]
    nulls = [str(field) for field, value in profile.items() if value is None]
    if missing or nulls:
        raise ConfigError(
            f"Radio profile {name!r} in {source} is incomplete: "
            f"missing fields={missing}; null fields={nulls}. "
            "TODO: fill from LunarRF run metadata for LunarRF profiles; "
            "otherwise inspect the declared source. Run refused."
        )
    values = {}
    for field in RADIO_FIELDS:
        value = profile[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ConfigError(f"Radio profile {name!r}: {field} must be a finite number in {source}")
        values[field] = float(value)
    for field in ("frequency_ghz", "max_range_m"):
        if values[field] <= 0:
            raise ConfigError(f"Radio profile {name!r}: {field} must be positive")
    for field in ("fixed_loss_db", "required_margin_db"):
        if values[field] < 0:
            raise ConfigError(f"Radio profile {name!r}: {field} must be nonnegative")
    return values


def load_experiment(path: str | Path, *, profiles_path: str | Path | None = None) -> dict[str, Any]:
    """Load a run from training/configs by name, or from an explicit absolute path.

    A validated profile is attached as ``radio``. Experiment fields cannot fill
    null profile values, including max_range_m: source metadata is required.
    """
    source = Path(path)
    if not source.is_absolute():
        source = configs_dir() / source
    experiment = _load_mapping(source)
    name = experiment.get("radio_profile")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"Experiment {source} requires a radio_profile name")
    experiment["radio"] = load_radio_profile(name, profiles_path)
    return experiment
