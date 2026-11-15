"""Generate cropped, simulated RF labels and optional Haworth diagnostic reports."""
import argparse
import json
from pathlib import Path

from moonmesh_train import paths
from moonmesh_train.config import configs_dir, load_experiment
from moonmesh_train.downloads import sha256_file
import moonmesh_train.rf as rf_module
from moonmesh_train.rf import label_scenario
from moonmesh_train.rf_report import make_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=configs_dir()/'rf_haworth.yaml')
    parser.add_argument('--output', type=Path, help='Scenario GeoTIFF output path')
    parser.add_argument('--report', action='store_true', help='Write research-point plots, areas and gates')
    parser.add_argument('--resume', action='store_true', help='Reuse a completed output/sidecar without retracing (requires --output)')
    args = parser.parse_args(argv)
    if args.resume and not args.output:
        parser.error('--resume requires --output')
    if args.resume and args.output and args.output.is_file() and args.output.with_suffix('.json').is_file():
        saved = json.loads(args.output.with_suffix('.json').read_text())
        cfg = load_experiment(args.config)
        hashes = {name: sha256_file(Path(rf_module.__file__).with_name(name)) for name in ('rf.py', 'los.py')}
        if (cfg != saved['scenario'] or hashes != saved.get('implementation_sha256')
                or sha256_file(paths.data_root()/cfg['dem']) != saved['dem_sha256']):
            raise ValueError('Scenario, implementation or DEM changed; rerun without --resume')
        if not Path(saved['class_geotiff']).is_file():
            raise ValueError('Missing native class companion; rerun without --resume')
        target = args.output
        print(f'Resuming completed simulated scenario {target}; git commit {saved["code_commit"]}', flush=True)
    else:
        target, _ = label_scenario(args.config, args.output)
    if args.report:
        result = make_report(target)
        print(json.dumps(dict(gates=result['gates'], counts=result['counts']), indent=2), flush=True)
        return 0 if all(g['passed'] for g in result['gates']) else 1
    print(target, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
