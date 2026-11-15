"""Inventory local terrain and simulated LunarRF products without changing inputs."""

import argparse
from pathlib import Path

from moonmesh_train import paths
from moonmesh_train.inventory import run_inventory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=paths.repo_root() / "training/configs/exp_a_lunarrf_haworth.yaml",
                        help="Read site coordinates and seed only; no radio defaults are inferred")
    args = parser.parse_args(argv)
    return run_inventory(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
