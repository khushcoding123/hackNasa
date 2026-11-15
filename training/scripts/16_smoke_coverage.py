"""Smoke-test a running coverage server and save a native offline demo export."""
import argparse
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

from moonmesh_train.config import configs_dir, _load_mapping
from moonmesh_train.downloads import atomic_json, sha256_file
from moonmesh_train.paths import reports_dir
from moonmesh_train.sampling import commit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='surface_coverage.yaml')
    parser.add_argument('--url', default='http://127.0.0.1:8896')
    args = parser.parse_args()
    config = Path(args.config)
    cfg = _load_mapping(config if config.is_absolute() else configs_dir()/config)
    output = reports_dir()/'surface_coverage'
    output.mkdir(parents=True, exist_ok=True)
    code = commit()
    print(f'Git commit {code}; seed {cfg["seed"]}; simulated coverage smoke', flush=True)

    def post(endpoint, body):
        request = Request(args.url+'/api/coverage'+endpoint, json.dumps(body).encode(),
                          {'Content-Type': 'application/json'})
        with urlopen(request, timeout=180) as response:
            return response.read()

    body = dict(site='haworth', h_tx=5, h_rx=2, radio_profile=cfg['radio_profile'])
    result = json.loads(post('', body))
    meta, layers = result['metadata'], result['layers']
    assert meta['valid_pixels'] > 0 and layers['valid'], 'No valid coverage'
    assert all(name in layers for name in ('los', 'margin', 'class', 'valid')), 'Missing layers'
    assert meta['predictor'] == 'deterministic', 'Final decision was not honored'
    assert any(v == 1 for row in layers['valid'] for v in row)
    changed = json.loads(post('', {**body, 'threshold': 200}))
    assert changed['metadata']['cache_hit'] and changed['metadata']['cache_key'] == meta['cache_key']
    assert changed['layers']['margin'] == layers['margin']
    assert changed['metadata']['state'] == 'no_link'
    fallback = json.loads(post('', {**body, 'predictor': 'model'}))
    assert fallback['metadata']['predictor'] == 'deterministic'
    assert fallback['metadata']['fallback_reasons']
    export = output/'haworth_reference.zip'
    export.write_bytes(post('/export', body))
    report = dict(status='pass', label_origin='simulated', code_commit=code, seed=cfg['seed'],
                  metadata=meta, response_layers=list(layers), threshold_cache_verified=True,
                  model_fallback_verified=True, export=str(export), export_sha256=sha256_file(export))
    atomic_json(output/'smoke.json', report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
