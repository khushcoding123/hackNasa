"""Train resumable LightGBM surrogates and report simulated validation metrics."""
import argparse
import json
from pathlib import Path

import yaml

from moonmesh_train.config import configs_dir
from moonmesh_train.paths import data_root
from moonmesh_train.tabular import run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=configs_dir()/'tabular_haworth.yaml')
    parser.add_argument('--max-chunks', type=int, default=1,
                        help='Bounded checkpoint chunks per invocation; rerun until complete')
    args = parser.parse_args(argv)
    if args.max_chunks < 1:
        parser.error('--max-chunks must be positive')
    cfg = yaml.safe_load(args.config.read_text())
    states = [json.loads(p.read_text()) for p in (data_root()/cfg['output']).glob('*_checkpoint.json')]
    large_resume = any(not s['done'] and s['iteration'] >= 1600 for s in states)
    chunks = min(args.max_chunks, 4) if large_resume else args.max_chunks
    if chunks < args.max_chunks:
        print('Large ensemble: bounding this run to 100 rounds for reload/evaluation headroom.', flush=True)
    run(args.config, chunks)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
