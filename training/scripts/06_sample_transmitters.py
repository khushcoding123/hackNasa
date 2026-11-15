"""Sample separated, terrain-stratified Haworth transmitter placements."""
import argparse
from pathlib import Path
from moonmesh_train.config import configs_dir
from moonmesh_train.sampling import sample

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=configs_dir()/'exp_b_haworth_pilot.yaml')
    sample(parser.parse_args().config)
