"""Align Haworth terrain and simulated LunarRF layers; fail on registration drift."""

import argparse
from pathlib import Path

from moonmesh_train import paths
from moonmesh_train.alignment import run_alignment


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", choices=["haworth"], default="haworth")
    parser.add_argument("--config", type=Path, default=paths.repo_root()/"training/configs/alignment.yaml")
    args = parser.parse_args(argv)
    return run_alignment(args.config, args.site)


if __name__ == "__main__":
    raise SystemExit(main())
