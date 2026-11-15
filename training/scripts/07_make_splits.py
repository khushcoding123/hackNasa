"""Assign whole spatial blocks and buffer training transmitter placements."""
import argparse
from pathlib import Path
from moonmesh_train.config import configs_dir
from moonmesh_train.sampling import make_splits

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=configs_dir()/'exp_b_haworth_pilot.yaml')
    make_splits(parser.parse_args().config)
