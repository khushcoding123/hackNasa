"""Build or verify the self-contained simulated Haworth demo without network IO."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import socket
from unittest.mock import patch

import numpy as np
from moonmesh_train import coverage as cov
from moonmesh_train.config import configs_dir, _load_mapping
from moonmesh_train.downloads import atomic_json, sha256_file
from moonmesh_train.paths import data_root, reports_dir
from moonmesh_train.sampling import commit


@contextmanager
def offline():
    def denied(*args, **kwargs):
        raise AssertionError('Network access forbidden during offline demo build/verify')
    with patch.object(socket.socket, 'connect', denied), patch.object(socket, 'create_connection', denied), \
            patch.object(socket, 'getaddrinfo', denied):
        yield


def verify(output):
    manifest = json.loads((output/'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        assert sha256_file(output/name) == expected, name
    page = (output/'index.html').read_text(encoding='utf-8')
    assert 'connect-src \'none\'' in page
    payload = page.split('<script id="cache" type="application/json">')[1].split('</script>')[0]
    data = json.loads(payload)
    low, high = data['scenarios']
    assert low['inspection']['los'] == 0
    assert low['inspection']['state'] == 'below_margin'
    assert high['inspection']['state'] == 'covered'
    assert low['inspection']['min_clearance_m'] < 0
    assert low['inspection']['profile'] and high['inspection']['profile']
    assert data['changed_native_pixels'] > 0
    assert low['response']['metadata']['label_origin'] == 'simulated'
    return dict(status='pass', network='socket connect and DNS forbidden',
                code_commit=commit(), seed=data['seed'], label_origin='simulated',
                changed_native_pixels=data['changed_native_pixels'], receiver=data['receiver'],
                scenarios=[dict(height=s['request']['h_tx'], state=s['inspection']['state'],
                                margin_db=s['inspection']['margin_db'],
                                clearance_m=s['inspection']['min_clearance_m']) for s in data['scenarios']],
                files_verified=len(manifest['files']))


def build(cfg, output):
    output.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).parents[1]/'demo.html'
    # Reuse only a fully verified bundle from identical source, config and terrain.
    implementation = {p.name: sha256_file(p) for p in
                      (Path(__file__), template, Path(cov.__file__), Path(cov.__file__).with_name('rf.py'),
                       Path(cov.__file__).with_name('los.py'), configs_dir()/'radio_profiles.yaml',
                       configs_dir()/cfg['coverage_config'])}
    requests = [cov.CoverageRequest(site=cfg['site'], h_tx=h, h_rx=cfg['rx_height_m'],
                                   display_size=cfg['display_size']) for h in cfg['tx_heights_m']]
    keys = [cov.prepare(body)[1]['cache_key'] for body in requests]
    identity = dict(config=cfg, implementation=implementation, keys=keys, code_commit=commit())
    if (output/'manifest.json').exists():
        if json.loads((output/'manifest.json').read_text())['identity'] == identity:
            verify(output)
            print('Reusing verified completed bundle', flush=True)
            return
    responses, entries, scenarios = [], [], []
    for body in requests:
        entry, _ = cov.calculate(body)
        entries.append(entry)
        responses.append(cov.coverage(body))
    low, high = [entry['arrays'] for entry in entries]
    stride = responses[0]['metadata']['display_stride']
    threshold = responses[0]['metadata']['threshold_db']
    # Choose a displayed receiver: known terrain outage at 5 m, recovered at 50 m.
    eligible = ((low['valid_mask'] > 0) & (high['valid_mask'] > 0) & (low['los'] == 0)
                & (low['link_margin_db'] < threshold) & (high['link_margin_db'] >= threshold)
                & (low['free_space_margin_db'] >= threshold))[::stride, ::stride]
    candidates = np.argwhere(eligible)
    if not len(candidates):
        raise RuntimeError('No terrain-caused RF outage recovers at the configured height')
    margins = high['link_margin_db'][::stride, ::stride]-low['link_margin_db'][::stride, ::stride]
    row, col = map(int, candidates[np.argmax(margins[eligible])])
    x, y = entries[0]['transform']*(col*stride+.5, row*stride+.5)
    files = []
    for body, response in zip(requests, responses):
        inspection = cov.inspect_route(cov.InspectRequest(**body.model_dump(), rx_x_m=x, rx_y_m=y))
        name = f'haworth_tx{body.h_tx:g}m.zip'
        (output/name).write_bytes(cov.export_route(body).body)
        files.append(name)
        scenarios.append(dict(request=body.model_dump(), response=response, inspection=inspection))
    changed = int(np.count_nonzero((low['valid_mask'] > 0) & (high['valid_mask'] > 0)
                                  & ((low['link_margin_db'] >= threshold) != (high['link_margin_db'] >= threshold))))
    data = dict(seed=cfg['seed'], code_commit=commit(), label_origin='simulated',
                receiver=dict(x_m=x, y_m=y, row=row, col=col), changed_native_pixels=changed, scenarios=scenarios)
    payload = json.dumps(data, allow_nan=False).replace('</', '<\\/')
    (output/'index.html').write_text(template.read_text(encoding='utf-8').replace('__CACHE__', payload), encoding='utf-8')
    files.append('index.html')
    atomic_json(output/'manifest.json', dict(identity=identity, label_origin='simulated',
                                           files={name: sha256_file(output/name) for name in files}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='demo.yaml')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    path = Path(args.config)
    cfg = _load_mapping(path if path.is_absolute() else configs_dir()/path)
    output = (data_root()/cfg['output']).resolve()
    if not output.is_relative_to(data_root()) or output.is_relative_to(data_root()/'raw'):
        raise ValueError('Demo output must be under data root and outside raw')
    print(f'Git commit {commit()}; seed {cfg["seed"]}; simulated; network forbidden', flush=True)
    with offline():
        if not args.verify_only:
            build(cfg, output)
        result = verify(output)
    report = reports_dir()/'demo'
    report.mkdir(parents=True, exist_ok=True)
    atomic_json(report/'offline_verification.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
