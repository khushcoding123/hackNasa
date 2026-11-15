"""Build resumable simulated Haworth scenarios and receiver Parquet tables."""
import argparse
from pathlib import Path

from moonmesh_train.config import configs_dir
from moonmesh_train.dataset import run
from moonmesh_train.paths import repo_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=configs_dir()/'dataset_haworth.yaml')
    parser.add_argument('--splits',type=Path,default=repo_root()/'training/splits.json')
    parser.add_argument('--workers',type=int,default=1,help='Concurrent scenarios; each uses configured Numba threads')
    parser.add_argument('--max-scenarios',type=int,help='Build at most this many unfinished scenarios, then checkpoint')
    parser.add_argument('--yes',action='store_true',help='Compatibility flag; unattended step always continues after the five-scenario estimate')
    args=parser.parse_args(argv)
    gates=run(args.config,args.splits,args.workers,args.max_scenarios)
    return 0 if gates is None or all(g['passed'] for g in gates) else 1


if __name__=='__main__':
    raise SystemExit(main())
