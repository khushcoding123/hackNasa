"""Checkpointed full-map simulated evaluation; test requires --final."""
import argparse
from pathlib import Path
from moonmesh_train.config import configs_dir
from moonmesh_train.evaluation import run


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=configs_dir()/'eval_haworth.yaml')
    p.add_argument('--split', choices=['val', 'test'], default='val')
    p.add_argument('--final', action='store_true')
    p.add_argument('--scenarios', nargs='+', help='Explicit scenario IDs, or a subset count (default 24 on val)')
    p.add_argument('--max-scenarios', type=int, default=1, help='Bounded resumable work per invocation')
    p.add_argument('--phase', choices=['maps', 'runtime', 'cross-source', 'report'], default='maps')
    a = p.parse_args(argv)
    if a.split == 'test' and not a.final:
        p.error('--split test requires --final')
    if a.max_scenarios < 1:
        p.error('--max-scenarios must be positive')
    run(a)


if __name__ == '__main__':
    main()
