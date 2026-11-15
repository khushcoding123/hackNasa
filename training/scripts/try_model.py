"""Try the trained LightGBM coverage model at any transmitter and compare it with the physics map.

All results are simulated. The physics map (fast LOS tracer + FSPL + P.526 knife edge) is the
reference; the learned model predicts the same map from terrain features. The model was trained at
Haworth with TX heights 5-50 m, RX 2 m, 2.4 GHz and an 8 km range; outside that it extrapolates.

Examples (from the repo root, training venv active):
  python training/scripts/try_model.py
  python training/scripts/try_model.py --lat -86.75 --lon 335.9 --h-tx 20
"""
import argparse
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import numba
import yaml
from matplotlib.colors import ListedColormap

from lunar_core.terrain import polar_stereographic_south
from moonmesh_train import paths, rf
from moonmesh_train.config import configs_dir
from moonmesh_train.evaluation import Context


def coverage_image(covered, valid):
    """0 = unknown, 1 = covered, 2 = dead (below margin)."""
    return np.where(~valid, 0, np.where(covered, 1, 2))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--lat', type=float, default=-86.8, help='TX latitude in degrees (default: Haworth research point)')
    p.add_argument('--lon', type=float, default=337.2, help='TX east longitude in degrees')
    p.add_argument('--h-tx', type=float, default=5.0, help='TX antenna height above ground, m')
    p.add_argument('--h-rx', type=float, default=2.0, help='RX antenna height above ground, m')
    p.add_argument('--out', type=Path, help='output PNG (default: <data root>/reports/try_model/...)')
    p.add_argument('--config', type=Path, default=configs_dir()/'eval_haworth.yaml')
    p.add_argument('--no-open', action='store_true', help='do not open the PNG when done')
    a = p.parse_args(argv)

    cfg = yaml.safe_load(a.config.read_text())
    numba.set_num_threads(min(int(cfg.get('numba_threads', 8)), numba.config.NUMBA_NUM_THREADS))
    print('Loading terrain, features and the three LightGBM models...', flush=True)
    ctx = Context(cfg)
    radio = ctx.radio
    x, y = (float(v) for v in polar_stereographic_south(a.lat, a.lon))

    warnings = []
    if not 5 <= a.h_tx <= 50:
        warnings.append(f'TX height {a.h_tx} m is outside the trained 5-50 m range.')
    if a.h_rx != 2:
        warnings.append(f'RX height {a.h_rx} m differs from the trained 2 m.')

    print('Computing the physics map...', flush=True)
    t0 = time.perf_counter()
    truth, tr, _ = rf.label_arrays(np.array([x, y]), a.h_tx, a.h_rx, ctx.dem, ctx.tr, ctx.crs, radio)
    physics_s = time.perf_counter()-t0
    shape = truth['los'].shape

    print('Running your model on every pixel (this takes about a minute or two)...', flush=True)
    row = SimpleNamespace(transmitter_x_m=x, transmitter_y_m=y, tx_height_agl_m=a.h_tx, rx_height_agl_m=a.h_rx,
                          frequency_ghz=radio['frequency_ghz'], max_range_m=radio['max_range_m'],
                          required_margin_db=radio['required_margin_db'])
    t0 = time.perf_counter()
    pred = ctx.predict(row, tr, shape, 'lightgbm')
    model_s = time.perf_counter()-t0

    valid = truth['valid_mask'].astype(bool) & pred['valid']
    n = int(valid.sum())
    if n == 0:
        raise SystemExit('No valid pixels here: the transmitter is outside the prepared Haworth terrain.')
    t_los, p_los = truth['los'] == 1, pred['los'].astype(bool)
    t_cov, p_cov = truth['rf_covered'].astype(bool), pred['covered'].astype(bool)
    dead = valid & ~t_cov
    px_km2 = abs(tr.a*tr.e)/1e6
    margin_err = np.abs(pred['margin']-truth['link_margin_db'])[valid]
    near = valid & (np.abs(truth['link_margin_db']-radio['required_margin_db']) <= 3)
    result = dict(
        label_origin='simulated',
        transmitter=dict(latitude=a.lat, longitude_east=a.lon, x_m=x, y_m=y, h_tx_m=a.h_tx, h_rx_m=a.h_rx),
        radio_profile=dict(frequency_ghz=radio['frequency_ghz'], max_range_m=radio['max_range_m'],
                           required_margin_db=radio['required_margin_db']),
        valid_pixels=n,
        los_agreement=float((t_los == p_los)[valid].mean()),
        coverage_agreement=float((t_cov == p_cov)[valid].mean()),
        physics_covered_km2=float((valid & t_cov).sum()*px_km2),
        model_covered_km2=float((valid & p_cov).sum()*px_km2),
        dead_zones_caught=float((dead & ~p_cov).sum()/dead.sum()) if dead.any() else None,
        false_covered_fraction=float((dead & p_cov).sum()/dead.sum()) if dead.any() else None,
        false_covered_km2=float((dead & p_cov).sum()*px_km2),
        margin_mae_db=float(margin_err.mean()),
        near_threshold_margin_mae_db=float(np.abs(pred['margin']-truth['link_margin_db'])[near].mean()) if near.any() else None,
        physics_seconds=round(physics_s, 2),
        model_seconds=round(model_s, 2),
        warnings=warnings,
    )

    out = a.out or paths.reports_dir()/'try_model'/f'model_vs_physics_lat{a.lat:g}_lon{a.lon:g}_tx{a.h_tx:g}_rx{a.h_rx:g}.png'
    out.parent.mkdir(parents=True, exist_ok=True)
    km = lambda v, o: (v-o)/1000
    extent = [km(tr.c, x), km(tr.c+shape[1]*tr.a, x), km(tr.f+shape[0]*tr.e, y), km(tr.f, y)]
    cover_map = ListedColormap(['#d9d9d9', '#2ca25f', '#f28e2b'])
    diff = np.where(~valid, 0, np.where(t_cov == p_cov, 1, np.where(p_cov, 2, 3)))
    diff_map = ListedColormap(['#ffffff', '#d9d9d9', '#d62728', '#1f77b4'])
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.6), constrained_layout=True)
    panels = [(coverage_image(t_cov, valid), cover_map, f'Physics map ({physics_s:.1f} s)'),
              (coverage_image(p_cov, valid), cover_map, f'Your LightGBM model ({model_s:.1f} s)'),
              (diff, diff_map, f'Disagreement ({100*(1-result["coverage_agreement"]):.2f}% of pixels)')]
    for ax, (img, cmap, title) in zip(axes, panels):
        ax.imshow(img, cmap=cmap, vmin=0, vmax=3 if cmap is diff_map else 2, extent=extent, interpolation='nearest')
        ax.plot(0, 0, marker='+', color='black', markersize=14, mew=2)
        ax.set_title(title)
        ax.set_xlabel('km east of TX (grid)')
    axes[0].set_ylabel('km north of TX (grid)')
    fig.suptitle(f'Simulated coverage, TX {a.lat:g}°, {a.lon:g}° E, {a.h_tx:g} m mast, {a.h_rx:g} m receiver, '
                 f'{radio["frequency_ghz"]:g} GHz. Green covered, orange dead, gray unknown.\n'
                 'Right: red = model says covered but it is dead, blue = model says dead but it is covered.', fontsize=9)
    fig.savefig(out, dpi=130)
    out.with_suffix('.json').write_text(json.dumps(result, indent=2))

    print()
    print(f"Coverage agreement with physics:  {100*result['coverage_agreement']:.2f}%")
    print(f"Line-of-sight agreement:          {100*result['los_agreement']:.2f}%")
    if result['dead_zones_caught'] is None:
        print('Real dead zones the model caught: none in range (everything is covered here)')
    else:
        print(f"Real dead zones the model caught: {100*result['dead_zones_caught']:.2f}%")
        print(f"Dead area the model calls covered: {result['false_covered_km2']:.3f} km2 "
              f"({100*result['false_covered_fraction']:.2f}% of the dead area)")
    print(f"Average signal-margin error:      {result['margin_mae_db']:.2f} dB")
    print(f"Time: physics {physics_s:.1f} s, model {model_s:.1f} s")
    for w in warnings:
        print('Warning:', w)
    print(f'Picture: {out}')
    if not a.no_open and hasattr(os, 'startfile'):
        os.startfile(out)


if __name__ == '__main__':
    main()
