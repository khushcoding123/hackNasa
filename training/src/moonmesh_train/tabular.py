"""First simulated terrain surrogates. Only train and uniform val are opened."""
import json
import shutil
from pathlib import Path
from time import perf_counter

import lightgbm as lgb
import numpy as np
import pandas as pd
import yaml

from . import paths, rf
from .dataset import FEATURES, check_features, config_hash
from .downloads import atomic_json, sha256_file
from .los import R
from .sampling import commit

TARGETS = {'los': 'geometric_los', 'clearance': 'min_clearance_m', 'margin': 'link_margin_db'}


def safe_features(names):
    check_features(names)
    if list(names) != FEATURES or len(set(names)) != len(names):
        raise ValueError('Inputs must exactly match the step 09 feature list')


def ratio(a, b):
    return float(a/b) if b else None


def metrics(truth, los, clearance, margin, covered, required):
    """Unweighted metrics; false-covered denominator is all true dead pixels."""
    y = truth.geometric_los.to_numpy().astype(bool)
    los = np.asarray(los, dtype=bool)
    dead = truth.link_margin_db.to_numpy() < required
    pred_dead = ~np.asarray(covered, dtype=bool)
    near = np.abs(truth.link_margin_db.to_numpy()-required) <= 3
    err = np.asarray(margin)-truth.link_margin_db.to_numpy()
    return dict(los_accuracy=float(np.mean(los == y)),
                los_f1=ratio(2*np.sum(los & y), np.sum(los)+np.sum(y)),
                dead_zone_precision=ratio(np.sum(pred_dead & dead), np.sum(pred_dead)),
                dead_zone_recall=ratio(np.sum(pred_dead & dead), np.sum(dead)),
                false_covered_rate=ratio(np.sum(~pred_dead & dead), np.sum(dead)),
                clearance_mae_m=None if clearance is None else float(np.mean(np.abs(np.asarray(clearance)-truth.min_clearance_m))),
                margin_mae_db=float(np.mean(np.abs(err))), margin_bias_db=float(np.mean(err)),
                margin_near_threshold_mae_db=float(np.mean(np.abs(err[near]))) if near.any() else None,
                n=len(truth), near_threshold_n=int(near.sum()))


def distance_baseline(frame, radio):
    horizon = np.sqrt(2*R*frame.h_tx.to_numpy())+np.sqrt(2*R*frame.h_rx.to_numpy())
    los = frame.distance_m.to_numpy() <= horizon
    margin = rf.received_power_dbm(rf.fspl_db(frame.distance_m.to_numpy(), radio['frequency_ghz']*1e9), radio)-radio['receiver_sensitivity_dbm']
    return los, margin, los & (margin >= radio['required_margin_db'])


def advance_state(state, scores, patience, limit):
    """Update per-round early stopping across resumptions, minimizing val loss."""
    used = 0
    for score in scores:
        state['iteration'] += 1
        used += 1
        if score < state['best_score']:
            state['best_score'] = float(score)
            state['best_iteration'] = state['iteration']
        if state['iteration']-state['best_iteration'] >= patience or state['iteration'] >= limit:
            state['done'] = True
            break
    return used


def train_chunk(name, train, val, features, cfg, out, signature, runtime=None):
    state_path = out/f'{name}_checkpoint.json'
    checkpoint = out/f'{name}_checkpoint.txt'
    state = json.loads(state_path.read_text()) if state_path.exists() else dict(
        config_hash=signature, iteration=0, best_iteration=0, best_score=1e300, done=False,
        training_seconds=0., history=[])
    if state['config_hash'] != signature:
        raise ValueError('Existing checkpoint config/input/implementation hash differs; use a new output directory')
    if state['done']:
        return state
    target = TARGETS[name]
    weights = train.sample_weight.to_numpy(dtype=float).copy()
    weights /= weights.mean()
    class_weights = None
    if name == 'los':
        counts = np.bincount(train[target].to_numpy(dtype=int), minlength=2)
        if not counts.all():
            raise ValueError('Training requires both LOS classes')
        class_weights = len(train)/(2*counts)
        weights *= class_weights[train[target].to_numpy(dtype=int)]
    state['class_weights'] = None if class_weights is None else class_weights.tolist()
    params = dict(objective='binary' if name == 'los' else 'huber',
                  metric='binary_logloss' if name == 'los' else 'l1',
                  alpha=cfg['huber_alpha'], num_leaves=cfg['num_leaves'], learning_rate=cfg['learning_rate'],
                  num_threads=cfg['num_threads'], seed=cfg['seed'], deterministic=True,
                  force_col_wise=True, verbosity=-1, feature_pre_filter=False)
    start = perf_counter()
    rounds = min(cfg['checkpoint_rounds'], cfg['max_rounds']-state['iteration'])
    if runtime is not None and name in runtime:
        booster = runtime[name]
        scores = []
        for _ in range(rounds):
            booster.update()
            scores.append(booster.eval_valid()[0][2])
    else:
        ds = lgb.Dataset(train[features], label=train[target], weight=weights, params=params)
        dv = lgb.Dataset(val[features], label=val[target], reference=ds)
        history = {}
        booster = lgb.train(params, ds, num_boost_round=rounds,
                            valid_sets=[dv], valid_names=['val_uniform'],
                            init_model=str(checkpoint) if checkpoint.exists() else None,
                            keep_training_booster=True, callbacks=[lgb.record_evaluation(history)])
        scores = next(iter(history['val_uniform'].values()))
        if runtime is not None:
            runtime[name] = booster
    old_iteration = state['iteration']
    used = advance_state(state, scores, cfg['early_stopping_rounds'], cfg['max_rounds'])
    state['history'].extend(float(s) for s in scores[:used])
    # Discard any rounds computed beyond the exact stopping point in this chunk.
    booster.save_model(str(checkpoint), num_iteration=old_iteration+used)
    booster.save_model(str(out/f'{name}.txt'), num_iteration=state['best_iteration'])
    state['training_seconds'] += perf_counter()-start
    atomic_json(state_path, state)
    if state['done'] and runtime is not None:
        runtime.pop(name, None)
    print(f'{name}: {state["iteration"]} rounds, best {state["best_iteration"]} '
          f'val loss {state["best_score"]:.6g}, done={state["done"]}, chunk {perf_counter()-start:.1f}s', flush=True)
    return state


def evaluate(root, dataset, manifest, features, cfg, out, signature, provenance):
    val = pd.read_parquet(dataset/'receivers_val.parquet', filters=[('sample_kind', '==', 'uniform')])
    radio = manifest['experiment']['radio']
    required = radio['required_margin_db']
    los, margin, covered = distance_baseline(val, radio)
    rows = {'Distance-only / free-space': metrics(val, los, None, margin, covered, required),
            'Deterministic labeler (cached simulated reference)': metrics(
                val, val.geometric_los, val.min_clearance_m, val.link_margin_db, val.rf_covered, required)}
    models = {n: lgb.Booster(model_file=str(out/f'{n}.txt')) for n in TARGETS}
    start = perf_counter()
    predictions = {n: b.predict(val[features], num_threads=cfg['num_threads']) for n, b in models.items()}
    prediction_seconds = perf_counter()-start
    rows['LightGBM (three heads)'] = metrics(val, predictions['los'] >= cfg['classification_threshold'],
        predictions['clearance'], predictions['margin'], predictions['margin'] >= required, required)
    warnings = ['All labels and reported reference results are simulated FSPL + P.526 single-edge diffraction.',
                'Distance-only has no clearance predictor: clearance MAE is null (TODO: not defined by this baseline).',
                'Deterministic reference reuses step 09 labels; perfect scores measure simulator self-consistency, not observed accuracy.',
                'Inherited PGDA vertical datum is null/TODO; inherited spatial-buffer limitations remain (see step 09).']
    base, learned = rows['Distance-only / free-space'], rows['LightGBM (three heads)']
    if not (learned['los_f1'] > base['los_f1'] and learned['margin_mae_db'] < base['margin_mae_db']):
        warnings.append('LightGBM does not beat distance-only on both LOS F1 and margin MAE.')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(19, 9), layout='constrained')
    importance = {}
    for ax, (name, model) in zip(axes, models.items()):
        gains = model.feature_importance(importance_type='gain')
        fraction = gains/gains.sum() if gains.sum() else gains
        importance[name] = dict(zip(features, map(float, fraction)))
        if fraction[features.index('distance_m')] >= cfg['distance_dominance_gain_fraction']:
            warnings.append(f'Distance alone dominates {name}: {fraction[features.index("distance_m")]:.1%} of gain.')
        order = np.argsort(fraction)[-15:]
        ax.barh(np.array(features)[order], fraction[order])
        ax.set_title(name+' (simulated)')
        ax.set_xlabel('Fraction of training split gain')
    fig.savefig(out/'feature_importance.png', dpi=140)
    plt.close(fig)
    # Preserve every val scenario's already measured full-label generation runtime.
    scenarios = pd.read_parquet(dataset/'scenarios.parquet', filters=[('split', '==', 'val')])
    runtimes = []
    for sid in scenarios.scenario_id:
        saved = json.loads((dataset/'scenarios'/sid/'dataset.json').read_text())
        runtimes.append(dict(scenario_id=sid, label_seconds=saved['label_seconds'],
                             feature_timings_seconds=saved['feature_timings'], label_origin='simulated'))
    report_dir = paths.reports_dir()/'models'
    report_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(report_dir/'tabular_val.json', dict(metrics=rows, warnings=warnings, label_origin='simulated',
        provenance=provenance, config_hash=signature, runtimes=runtimes, feature_importance=importance,
        three_head_prediction_seconds=prediction_seconds, test_opened=False))
    atomic_json(out/'metadata.json', dict(config_hash=signature, config=cfg, feature_columns=features,
        forbidden_column_assertion_ran=True, label_origin='simulated', provenance=provenance,
        lightgbm_version=lgb.__version__, best_iterations={n: b.current_iteration() for n,b in models.items()},
        feature_importance=importance, validation_sampling='uniform only; no sample weights',
        coverage_rule='predicted margin >= required_margin_db', required_margin_db=required,
        los_threshold=cfg['classification_threshold'], test_opened=False))
    columns = ['los_accuracy','los_f1','dead_zone_precision','dead_zone_recall','false_covered_rate',
               'clearance_mae_m','margin_mae_db','margin_bias_db','margin_near_threshold_mae_db']
    lines = ['# LightGBM validation (simulated)', '', f'Git commit `{provenance["code_commit"]}`; config hash `{signature}`; seed {cfg["seed"]}.',
        f'{len(val):,} unweighted uniform validation rows; {len(scenarios)} held-out scenarios. Test receiver data was never opened.', '',
        '| Baseline / model | '+' | '.join(columns)+' |', '|---|'+'---:|'*len(columns)]
    for name, values in rows.items():
        lines.append('| '+name+' | '+' | '.join('null' if values[c] is None else f'{values[c]:.6f}' for c in columns)+' |')
    lines += ['', 'LOS positive class is clear. Dead zone means true margin below the scenario threshold. '
        'False-covered rate = predicted-covered true-dead / all true-dead. Margin bias = prediction minus simulated target. '
        'Near-threshold MAE uses true margin within 3 dB (inclusive); null means no defined estimate.', '',
        'Distance-only uses the spherical height horizon and free-space margin jointly for coverage; its continuous margin '
        'is the unobstructed free-space value, so horizon gating and margin thresholding are separate for that baseline.', '',
        'LightGBM LOS uses threshold 0.5. Both regressors use Huber objective (alpha 0.9), early stopping on unweighted '
        'uniform validation MAE; LOS stops on validation log loss. Training uses normalized inverse-multiplicity boundary '
        'weights, multiplied by balanced train-only LOS class weights. Missing input features remain NaN for native LightGBM handling.', '',
        f'Three-head prediction time on the uniform val sample (features already cached): {prediction_seconds:.3f} s. '
        'This excludes reads and feature generation and is not an end-to-end acceleration claim.', '',
        '## Deterministic runtime per scenario', '',
        'Reused step 09 measured full-window label generation (10 m fast tracer plus RF), including first-use compilation '
        'where present; no relabeling or new timing claim. Feature generation is separately preserved in tabular_val.json.', '',
        '| Scenario | Label seconds |', '|---|---:|']
    lines += [f'| {r["scenario_id"]} | {r["label_seconds"]:.6f} |' for r in runtimes]
    lines += ['', '## Warnings', '']+[f'- {w}' for w in warnings]
    (report_dir/'tabular_val.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return warnings


def run(config, max_chunks=1):
    cfg = yaml.safe_load(Path(config).read_text())
    root = paths.data_root()
    dataset, out = root/cfg['dataset'], root/cfg['output']
    out.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root.anchor).free < 12*1024**3:
        raise OSError('Insufficient headroom: cache/model writes could leave less than 10 GB')
    manifest_path = dataset/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    features = manifest['feature_columns']
    safe_features(features)
    provenance_path = out/'inputs.json'
    # Rehash only train and val inputs on each invocation; never touch test data.
    hashes = {n: sha256_file(dataset/n) for n in ('manifest.json', 'receivers_train.parquet', 'receivers_val.parquet', 'scenarios.parquet')}
    implementation = sha256_file(Path(__file__))
    algorithm = dict(config=cfg, hashes=hashes, features=features, lightgbm=lgb.__version__)
    signature = config_hash(dict(**algorithm, algorithm_version=1))
    provenance = dict(code_commit=commit(), input_sha256=hashes, implementation_sha256=implementation)
    if provenance_path.exists():
        previous = json.loads(provenance_path.read_text())
        if previous['config_hash'] != signature:
            # One explicit migration from the initial, mathematically identical
            # reload-per-chunk implementation. Reject any input/config change.
            old_hash = previous['provenance']['implementation_sha256']
            legacy_signature = config_hash(dict(**algorithm, implementation=old_hash))
            if previous['config_hash'] != legacy_signature:
                raise ValueError('Saved inputs differ; refusing to overwrite trained artifacts')
            for state_path in out.glob('*_checkpoint.json'):
                saved = json.loads(state_path.read_text())
                if saved['config_hash'] != legacy_signature:
                    raise ValueError('Inconsistent legacy checkpoint')
                saved['config_hash'] = signature
                atomic_json(state_path, saved)
            previous['config_hash'] = signature
            previous['provenance']['implementation_revisions'] = [old_hash, implementation]
            previous['provenance']['implementation_sha256'] = implementation
            previous['algorithm_version'] = 1
            atomic_json(provenance_path, previous)
            print('Migrated compatible checkpoints: reuse live booster within each bounded run.', flush=True)
        elif previous['provenance']['implementation_sha256'] != implementation:
            raise ValueError('Implementation changed after checkpoint migration; review compatibility explicitly')
        provenance = previous['provenance']
    else:
        atomic_json(provenance_path, dict(config_hash=signature, provenance=provenance, label_origin='simulated'))
    print(f'Git commit {provenance["code_commit"]}; seed {cfg["seed"]}; forbidden-column assertion passed ({len(features)} features)', flush=True)
    states = {}
    for name in TARGETS:
        p = out/f'{name}_checkpoint.json'
        states[name] = json.loads(p.read_text()) if p.exists() else {'done': False}
    if not all(s['done'] for s in states.values()):
        columns = features+list(TARGETS.values())+['sample_weight','label_valid','label_origin']
        train = pd.read_parquet(dataset/'receivers_train.parquet', columns=columns)
        val = pd.read_parquet(dataset/'receivers_val.parquet', columns=columns,
                              filters=[('sample_kind', '==', 'uniform')])
        for frame in (train, val):
            if not frame.label_valid.all() or not (frame.label_origin == 'simulated').all() or not np.isfinite(frame[list(TARGETS.values())]).all().all():
                raise ValueError('Invalid/non-simulated targets')
        runtime = {}
        for _ in range(max_chunks):
            name = next((n for n,s in states.items() if not s['done']), None)
            if name is None:
                break
            states[name] = train_chunk(name, train, val, features, cfg, out, signature, runtime)
        del train, val
    if all(s['done'] for s in states.values()):
        warnings = evaluate(root, dataset, manifest, features, cfg, out, signature, provenance)
        gates = [dict(name='Three saved models and forbidden-column assertion', passed=all((out/f'{n}.txt').is_file() for n in TARGETS), value=len(features)),
                 dict(name='Uniform validation metrics for both baselines and three-head model', passed=(paths.reports_dir()/'models/tabular_val.md').is_file(), value=True)]
        atomic_json(paths.reports_dir()/'pipeline/step_10.json', dict(step=10, title='First LightGBM models',
            status='pass' if all(g['passed'] for g in gates) else 'fail',
            summary='Step 10 passed: saved all three LightGBM models and uniform-validation baseline/model metrics; test data was not opened.',
            gates=gates, decisions=['Reuse deterministic simulated labels and their original per-scenario generation runtimes.',
                'Use full training data with normalized boundary weights and balanced LOS class weights; uniform unweighted val only.',
                'Use 25-round checkpoints, 50-round patience, and train-only gain fraction >= 0.5 as the distance dominance warning rule.',
                'Distance-only clearance is undefined (null); continuous margin is free-space and coverage also requires the height horizon.'],
            warnings=warnings, outputs=[str(out), str(paths.reports_dir()/'models/tabular_val.md')],
            tests='Pending final required suites; finalize status after verification.'))
    else:
        print('Checkpoint saved; rerun the same command to continue.', flush=True)
