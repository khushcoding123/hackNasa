"""Validate safety, coverage definitions and resumable early stopping."""
import numpy as np
import pandas as pd
import pytest

from moonmesh_train.dataset import FEATURES
from moonmesh_train.tabular import advance_state, distance_baseline, metrics, safe_features, train_chunk


def test_safe_features_exact_and_forbidden():
    safe_features(FEATURES)
    for bad in ('min_clearance_m', 'split', 'sample_weight', 'transmitter_id', 'best_prx', 'count'):
        with pytest.raises(ValueError):
            safe_features(FEATURES+[bad])
    with pytest.raises(ValueError):
        safe_features(FEATURES[:-1])


def test_early_stopping_crosses_checkpoint_and_discards_extra():
    state = dict(iteration=0, best_iteration=0, best_score=1e300, done=False)
    assert advance_state(state, [5, 4, 3], 2, 20) == 3
    assert advance_state(state, [4, 5, 1], 2, 20) == 2
    assert state == dict(iteration=5, best_iteration=3, best_score=3., done=True)


def test_metrics_false_covered_denominator_and_bias():
    truth = pd.DataFrame(dict(geometric_los=[1, 0, 0, 1], min_clearance_m=[1, -1, -2, 2], link_margin_db=[4, 2, -5, 10]))
    m = metrics(truth, [1, 1, 0, 1], [2, 0, -1, 3], [5, 3, -4, 11], [1, 1, 0, 1], 3)
    assert m['false_covered_rate'] == .5
    assert m['dead_zone_recall'] == .5
    assert m['dead_zone_precision'] == 1
    assert m['margin_bias_db'] == 1
    assert m['margin_near_threshold_mae_db'] == 1
    assert m['clearance_mae_m'] == 1


def test_distance_horizon_gates_even_high_free_space_margin():
    radio = dict(frequency_ghz=2.4, tx_power_dbm=100, tx_gain_dbi=0, rx_gain_dbi=0,
                 fixed_loss_db=0, receiver_sensitivity_dbm=-100, required_margin_db=3)
    f = pd.DataFrame(dict(distance_m=[100., 10000.], h_tx=[2., 2.], h_rx=[2., 2.]))
    los, margin, covered = distance_baseline(f, radio)
    np.testing.assert_array_equal(los, [True, False])
    assert (margin > 3).all()
    np.testing.assert_array_equal(covered, [True, False])


@pytest.mark.parametrize('name', ['los', 'clearance', 'margin'])
def test_resumed_booster_matches_continuous_training(tmp_path, name):
    import lightgbm as lgb
    rng = np.random.default_rng(42)
    frame = pd.DataFrame(rng.normal(size=(180, len(FEATURES))), columns=FEATURES)
    frame['geometric_los'] = (frame.distance_m > 0).astype(int)
    frame['min_clearance_m'] = frame.distance_m*3
    frame['link_margin_db'] = frame.distance_m*2
    frame['sample_weight'] = 1.
    cfg = dict(huber_alpha=.9, num_leaves=7, learning_rate=.05, num_threads=1,
               seed=42, checkpoint_rounds=4, max_rounds=8, early_stopping_rounds=50)
    resumed, continuous, live = tmp_path/'resumed', tmp_path/'continuous', tmp_path/'live'
    resumed.mkdir()
    continuous.mkdir()
    live.mkdir()
    train_chunk(name, frame[:120], frame[120:], FEATURES, cfg, resumed, 'same')
    state = train_chunk(name, frame[:120], frame[120:], FEATURES, cfg, resumed, 'same')
    runtime = {}
    train_chunk(name, frame[:120], frame[120:], FEATURES, cfg, live, 'same', runtime)
    train_chunk(name, frame[:120], frame[120:], FEATURES, cfg, live, 'same', runtime)
    cfg['checkpoint_rounds'] = 8
    train_chunk(name, frame[:120], frame[120:], FEATURES, cfg, continuous, 'same')
    a = lgb.Booster(model_file=str(resumed/f'{name}.txt'))
    b = lgb.Booster(model_file=str(continuous/f'{name}.txt'))
    c = lgb.Booster(model_file=str(live/f'{name}.txt'))
    np.testing.assert_allclose(a.predict(frame[FEATURES]), b.predict(frame[FEATURES]), atol=1e-12)
    np.testing.assert_allclose(c.predict(frame[FEATURES]), b.predict(frame[FEATURES]), atol=1e-12)
    assert state['done'] and state['iteration'] == 8
    with pytest.raises(ValueError, match='hash differs'):
        train_chunk(name, frame[:120], frame[120:], FEATURES, cfg, resumed, 'changed')
