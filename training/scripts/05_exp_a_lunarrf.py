"""Run resumable chunks of the simulated LunarRF Haworth comparison."""
import argparse
from pathlib import Path
from moonmesh_train.config import configs_dir
from moonmesh_train.exp_a import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=configs_dir()/'exp_a_lunarrf_haworth.yaml')
    parser.add_argument('--max-masts', type=int, default=10, help='Maximum new masts in this chunk')
    parser.add_argument('--seconds', type=float, default=480, help='Stop starting new masts after this time')
    parser.add_argument('--report', action='store_true', help='Write comparisons after all masts finish')
    args = parser.parse_args()
    if args.max_masts < 0 or args.seconds <= 0: parser.error('Require max-masts >= 0 and seconds > 0')
    complete = run(args.config, args.max_masts, args.seconds, args.report)
    print('Complete' if complete else 'Chunk complete; rerun the same command to resume', flush=True)


if __name__ == '__main__':
    main()
