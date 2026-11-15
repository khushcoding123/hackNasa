"""Train and evaluate the thermal hazard model on held-out spatial blocks.

Rules this file enforces:
- Fitting and model selection use only the ``train`` and ``val`` blocks. The ``test`` blocks are scored once, after the
  choice is made, and nothing is tuned on them.
- The model must beat simple baselines on the held-out test blocks; the report says plainly whether it does.
- Inputs are the terrain features in ``ora.hazard.FEATURES`` only.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from itertools import product
from pathlib import Path

import joblib
import numpy as np
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.linear_model import Ridge
from sklearn.metrics import average_precision_score, brier_score_loss, mean_absolute_error, r2_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ora import hazard

from . import paths
from .hazard_dataset import SPLITS, git_commit

GRID = dict(max_leaf_nodes=(15, 31, 63), min_samples_leaf=(200,))


def load_dataset(path):
    with np.load(path) as data:
        return {name: data[name] for name in data.files}


def _hgb(config, **override):
    params = dict(config["training"]["hist_gradient_boosting"]) | override
    return HistGradientBoostingRegressor(random_state=config["seed"], early_stopping=False, **params)


def _metrics(y, prediction):
    return dict(mae_k=float(mean_absolute_error(y, prediction)), rmse_k=float(np.sqrt(np.mean((y - prediction) ** 2))),
                r2=float(r2_score(y, prediction)), n=int(len(y)))


def _block_bootstrap(blocks, errors, seed, repeats=300):
    """95% interval of the mean absolute error, resampling whole blocks (neighbouring points are not independent)."""
    ids = np.unique(blocks)
    sums = {b: (np.abs(errors[blocks == b]).sum(), (blocks == b).sum()) for b in ids}
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(repeats):
        pick = rng.choice(ids, size=len(ids), replace=True)
        total, count = np.sum([sums[b] for b in pick], axis=0)
        out.append(total / count)
    return [float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))]


def _coverage(y, low, high):
    return float(np.mean((y >= low) & (y <= high)))


def run(config, dataset_path=None, model_path=None, report_dir=None, card_path=None):
    seed = config["seed"]
    dataset_path = Path(dataset_path) if dataset_path else paths.derived_dir("datasets") / config["output"]["dataset"]
    data = load_dataset(dataset_path)
    features = list(data["features"])
    if features != list(hazard.FEATURES):
        raise ValueError("dataset features differ from ora.hazard.FEATURES; rebuild the dataset")
    X, split, block = data["X"], data["split"], data["block"]
    rng = np.random.default_rng(seed)
    train_rows = np.flatnonzero(split == SPLITS["train"])
    train_rows = np.sort(rng.choice(train_rows, size=min(config["training"]["train_subsample"], len(train_rows)), replace=False))
    val_rows, test_rows = np.flatnonzero(split == SPLITS["val"]), np.flatnonzero(split == SPLITS["test"])
    pole = [features.index("r_pole_km")]

    # ---- model selection: validation blocks only
    # 1) which inputs: an ablation against a model that merely memorizes where it is (elevation + distance from the pole).
    set_scores = {}
    for name, members in hazard.FEATURE_SETS.items():
        cols = [features.index(f) for f in members]
        set_scores[name] = float(sum(
            mean_absolute_error(data[t][val_rows], _hgb(config).fit(X[train_rows][:, cols], data[t][train_rows]).predict(X[val_rows][:, cols]))
            for t in hazard.TARGETS))
    feature_set = min(set_scores, key=set_scores.get)
    use = [features.index(f) for f in hazard.FEATURE_SETS[feature_set]]
    Xu = X[:, use]
    # 2) hyperparameters, per target, with the chosen inputs.
    selection = {}
    for target in hazard.TARGETS:
        scores = {}
        for leaves, leaf_min in product(GRID["max_leaf_nodes"], GRID["min_samples_leaf"]):
            fit = _hgb(config, max_leaf_nodes=leaves, min_samples_leaf=leaf_min).fit(Xu[train_rows], data[target][train_rows])
            scores[f"leaves={leaves},min_leaf={leaf_min}"] = float(mean_absolute_error(data[target][val_rows], fit.predict(Xu[val_rows])))
        best = min(scores, key=scores.get)
        selection[target] = dict(validation_mae_k=scores, chosen=best)
    selection["feature_set"] = dict(validation_mae_sum_k=set_scores, chosen=feature_set, columns=list(hazard.FEATURE_SETS[feature_set]))

    def params_for(target):
        chosen = dict(item.split("=") for item in selection[target]["chosen"].split(","))
        return dict(max_leaf_nodes=int(chosen["leaves"]), min_samples_leaf=int(chosen["min_leaf"]))

    models, intervals, results, baselines, interval_scale = {}, {}, {}, {}, {}
    low_q, high_q = config["training"]["interval_quantiles"]
    for target in hazard.TARGETS:
        y = data[target]
        params = params_for(target)
        models[target] = _hgb(config, **params).fit(Xu[train_rows], y[train_rows])
        intervals[target] = tuple(_hgb(config, loss="quantile", quantile=q, **params).fit(Xu[train_rows], y[train_rows]) for q in (low_q, high_q))
        # The quantile models under-cover on new blocks. Widen the range by the smallest factor that reaches the nominal share on the
        # validation blocks (test blocks are not involved); the test coverage reported below is then an honest check of it.
        vp, vl, vh = models[target].predict(Xu[val_rows]), intervals[target][0].predict(Xu[val_rows]), intervals[target][1].predict(Xu[val_rows])
        factor = next((f for f in np.arange(1.0, 6.01, 0.05)
                       if _coverage(y[val_rows], vp - f * np.maximum(vp - vl, 0), vp + f * np.maximum(vh - vp, 0)) >= high_q - low_q), 6.0)
        interval_scale[target] = float(factor)
        # baselines, all fitted on the same training rows
        constant = DummyRegressor(strategy="mean").fit(X[train_rows], y[train_rows])
        pole_only = _hgb(config, max_leaf_nodes=15).fit(X[train_rows][:, pole], y[train_rows])
        linear = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(np.nan_to_num(X[train_rows]), y[train_rows])
        for name, predict in (("constant_train_mean", lambda rows: constant.predict(X[rows])),
                              ("distance_from_pole_only", lambda rows: pole_only.predict(X[rows][:, pole])),
                              ("linear_all_features", lambda rows: linear.predict(np.nan_to_num(X[rows])))):
            baselines.setdefault(target, {})[name] = {s: _metrics(y[rows], predict(rows)) for s, rows in (("val", val_rows), ("test", test_rows))}
        point = {s: models[target].predict(Xu[rows]) for s, rows in (("val", val_rows), ("test", test_rows))}
        scale = interval_scale[target]
        lo = {s: point[s] - scale * np.maximum(point[s] - intervals[target][0].predict(Xu[rows]), 0) for s, rows in (("val", val_rows), ("test", test_rows))}
        hi = {s: point[s] + scale * np.maximum(intervals[target][1].predict(Xu[rows]) - point[s], 0) for s, rows in (("val", val_rows), ("test", test_rows))}
        by = {"val": val_rows, "test": test_rows}
        entry = {s: _metrics(y[by[s]], point[s]) | dict(interval_coverage=_coverage(y[by[s]], lo[s], hi[s]),
                                                         interval_nominal=high_q - low_q, interval_mean_width_k=float(np.mean(hi[s] - lo[s])))
                 for s in by}
        entry["test"]["mae_k_ci95_block_bootstrap"] = _block_bootstrap(block[test_rows], y[test_rows] - point["test"], seed)
        best_baseline = min(baselines[target], key=lambda n: baselines[target][n]["test"]["mae_k"])
        entry["best_baseline"] = best_baseline
        entry["interval_widening_factor_fitted_on_validation"] = scale
        entry["beats_every_baseline_on_test_mae"] = bool(entry["test"]["mae_k"] < min(b["test"]["mae_k"] for b in baselines[target].values()))
        entry["breakdown_test"] = _breakdown(X[test_rows], y[test_rows] - point["test"], features)
        entry["per_block_test_mae_k"] = _per_block(block[test_rows], y[test_rows] - point["test"])
        results[target] = entry

    # ---- cold trap classifier
    y = data["cold_trap"]
    classifier_params = params_for("temp_max_k")
    classifier = HistGradientBoostingClassifier(random_state=seed, early_stopping=False, **config["training"]["hist_gradient_boosting"] | classifier_params,
                                                class_weight=None).fit(Xu[train_rows], y[train_rows])
    prior = DummyClassifier(strategy="prior").fit(X[train_rows], y[train_rows])
    pole_clf = HistGradientBoostingClassifier(random_state=seed, early_stopping=False, max_leaf_nodes=15).fit(X[train_rows][:, pole], y[train_rows])
    cold = {}
    for name, rows in (("val", val_rows), ("test", test_rows)):
        score = classifier.predict_proba(Xu[rows])[:, 1]
        cold[name] = dict(prevalence=float(y[rows].mean()), roc_auc=float(roc_auc_score(y[rows], score)),
                          average_precision=float(average_precision_score(y[rows], score)), brier=float(brier_score_loss(y[rows], score)),
                          baseline_prior_brier=float(brier_score_loss(y[rows], prior.predict_proba(X[rows])[:, 1])),
                          baseline_pole_only_average_precision=float(average_precision_score(y[rows], pole_clf.predict_proba(X[rows][:, pole])[:, 1])),
                          baseline_prior_average_precision=float(y[rows].mean()),
                          n=int(len(rows)), positives=int(y[rows].sum()))
    cold["beats_baselines_on_test"] = bool(cold["test"]["average_precision"] > max(cold["test"]["baseline_pole_only_average_precision"], cold["test"]["prevalence"])
                                           and cold["test"]["brier"] < cold["test"]["baseline_prior_brier"])

    # ---- what the model relies on (validation blocks, permutation importance on a seeded subsample)
    sub = np.sort(rng.choice(val_rows, size=min(30_000, len(val_rows)), replace=False))
    importance = permutation_importance(models["temp_max_k"], Xu[sub], data["temp_max_k"][sub], n_repeats=3, random_state=seed,
                                        scoring="neg_mean_absolute_error")
    ranking = sorted(zip(hazard.FEATURE_SETS[feature_set], importance.importances_mean), key=lambda item: -item[1])

    reference = {t: [float(v) for v in np.percentile(data[t][split == SPLITS["train"]], range(0, 101, 5))] for t in hazard.TARGETS}
    model = hazard.ThermalHazardModel(features, models, intervals, classifier, reference, feature_set, interval_scale)
    model_path = Path(model_path) if model_path else _model_dir() / config["output"]["model"]
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)

    manifest = json.loads(Path(dataset_path).with_suffix(".json").read_text())
    report = dict(
        generated=datetime.now(UTC).isoformat(timespec="seconds"), git_commit=git_commit(), seed=seed, status="research / experimental",
        labels="real: LRO Diviner polar resource product (thermal-model fit), not simulated",
        dataset=dict(path=str(dataset_path), rows=manifest["rows"], registration_check=manifest["registration_check"],
                     sources=manifest["sources"]),
        split=dict(config["split"], train_rows_used=int(len(train_rows)), val_rows=int(len(val_rows)), test_rows=int(len(test_rows)),
                   test_blocks=int(len(np.unique(block[test_rows]))), val_blocks=int(len(np.unique(block[val_rows])))),
        model_selection_on_validation_blocks=selection, regression=results, baselines=baselines, cold_trap=cold,
        permutation_importance_temp_max_mae_k=[dict(feature=f, increase_k=float(v)) for f, v in ranking],
        sklearn=__import__("sklearn").__version__, model_file=str(model_path), model_sha256=_sha(model_path),
        limits=_limits() + [f"No held-out test facet lies within {float(X[test_rows][:, features.index('r_pole_km')].min()):.0f} km of the pole; "
                            "accuracy closer in was not measured (labels there are on steep sun-facing slopes and are physically plausible but unchecked)."],
    )
    out = Path(report_dir) if report_dir else paths.reports_dir() / config["output"]["report"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1))
    (out / "report.md").write_text(render(report))
    if card_path is not None:
        update_card(card_path, report, features)
    return report


def _model_dir():
    from moonmesh.data import DATA
    return DATA / "models"


def _sha(path):
    from .hazard_dataset import sha256
    return sha256(path)


def _breakdown(X, error, features):
    """Test MAE by terrain regime, so a good average cannot hide a bad regime."""
    out = {}
    for name, edges in (("slope_deg", (0, 5, 10, 20, 90)), ("horizon_mean_deg", (-90, 0, 2, 5, 90)), ("r_pole_km", (0, 50, 100, 200, 400))):
        values = X[:, features.index(name)]
        out[name] = [dict(low=lo, high=hi, mae_k=float(np.mean(np.abs(error[(values >= lo) & (values < hi)]))), n=int(((values >= lo) & (values < hi)).sum()))
                     for lo, hi in zip(edges[:-1], edges[1:]) if ((values >= lo) & (values < hi)).sum() > 50]
    return out


def _per_block(blocks, error):
    ids = np.unique(blocks)
    values = np.array([np.mean(np.abs(error[blocks == b])) for b in ids])
    return dict(blocks=int(len(ids)), min=float(values.min()), median=float(np.median(values)), max=float(values.max()))


def _limits():
    return [
        "Target is the Diviner Polar Resource Product: a thermal-model fit to the first year of Diviner observations on ~500 m mesh facets, "
        "not a raw measurement and not a night-time minimum.",
        "Facet scale (~500 m) is much coarser than a relay (metres). The model says nothing about metre-scale shadows or boulders.",
        "Inputs come from the LOLA 240 m DEM only; no illumination time series, no mission dates, no hardware.",
        "Trained only on the 80-90 S polar cap. It must not be applied outside that cap.",
        "Interval coverage is measured on held-out blocks of this cap and is not guaranteed elsewhere.",
        "The PRP is itself a model result built on the Kaguya DEM; errors in that product are inherited.",
    ]


def render(report):
    rows = []
    for target, entry in report["regression"].items():
        t, v = entry["test"], entry["val"]
        rows.append(f"| {target} | {v['mae_k']:.2f} | {t['mae_k']:.2f} ({t['mae_k_ci95_block_bootstrap'][0]:.2f}-{t['mae_k_ci95_block_bootstrap'][1]:.2f}) | {t['rmse_k']:.2f} | {t['r2']:.3f} "
                    f"| {t['interval_coverage']:.2f} | {entry['best_baseline']} {report['baselines'][target][entry['best_baseline']]['test']['mae_k']:.2f} | {entry['beats_every_baseline_on_test_mae']} |")
    cold = report["cold_trap"]["test"]
    lines = ["# Thermal hazard model: held-out evaluation (research / experimental)", "",
             f"Generated {report['generated']} at commit `{report['git_commit']}`, seed {report['seed']}. Labels are real (Diviner PRP), not simulated.", "",
             f"Test = {report['split']['test_blocks']} whole {report['split']['block_km']} km blocks never used for fitting or selection "
             f"({report['split']['test_rows']:,} facets); {report['split']['buffer_km']} km buffer removed from training.", "",
             f"Inputs used: `{report['model_selection_on_validation_blocks']['feature_set']['chosen']}` "
             f"(chosen on validation blocks; ablation sums {report['model_selection_on_validation_blocks']['feature_set']['validation_mae_sum_k']}).", "",
             "| target | val MAE K | test MAE K (95% block bootstrap) | test RMSE K | test R2 | 80% interval coverage | best baseline (test MAE K) | beats all baselines |",
             "|---|---|---|---|---|---|---|---|", *rows, "",
             f"Cold trap (ice stable at the surface), test: prevalence {cold['prevalence']:.4f}, ROC-AUC {cold['roc_auc']:.3f}, "
             f"average precision {cold['average_precision']:.3f} (distance-from-pole-only baseline {cold['baseline_pole_only_average_precision']:.3f}, prior {cold['baseline_prior_average_precision']:.4f}), "
             f"Brier {cold['brier']:.5f} (prior {cold['baseline_prior_brier']:.5f}). Beats baselines: {report['cold_trap']['beats_baselines_on_test']}.", "",
             "## Limits", *[f"- {item}" for item in report["limits"]]]
    return "\n".join(lines) + "\n"


def update_card(card_path, report, features):
    card_path = Path(card_path)
    card = json.loads(card_path.read_text())
    test = {t: report["regression"][t]["test"] for t in report["regression"]}
    card["hazard_thermal"] = dict(
        task="annual mean and maximum surface temperature, and surface cold-trap probability, of ~500 m south polar facets, from LOLA terrain",
        status="research / experimental: not used by the mission ranking; shown beside the physics results",
        labels="real: LRO Diviner polar resource product LRO-L-DLRE-5-PRP-V2.0 (thermal-model fit, not a raw measurement)",
        terrain="NASA LOLA LDEM_75S_240M", features=list(features), feature_set=report["model_selection_on_validation_blocks"]["feature_set"]["chosen"], seed=report["seed"], git_commit=report["git_commit"],
        evaluation=dict(split=f"{report['split']['block_km']} km spatial blocks, {report['split']['buffer_km']} km buffer, test scored once",
                        temp_avg_k=test["temp_avg_k"], temp_max_k=test["temp_max_k"], cold_trap=report["cold_trap"]["test"],
                        beats_baselines=dict(temp_avg_k=report["regression"]["temp_avg_k"]["beats_every_baseline_on_test_mae"],
                                             temp_max_k=report["regression"]["temp_max_k"]["beats_every_baseline_on_test_mae"],
                                             cold_trap=report["cold_trap"]["beats_baselines_on_test"])),
        limits=report["limits"], sklearn=report["sklearn"], report=f"data/reports/{Path(report['model_file']).stem}")
    card.setdefault("artifacts", {})["ora_hazard_thermal"] = dict(sha256=report["model_sha256"], bytes=Path(report["model_file"]).stat().st_size)
    card_path.write_text(json.dumps(card, indent=1))
