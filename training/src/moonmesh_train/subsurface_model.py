"""Surrogate model for the underground-to-surface link. Labels are SIMULATED (see subsurface.py).

Inputs are raw scenario parameters only: the physics margin, absorption and other label terms are never
features. The learned model is checked against the deterministic physics, which stays the reference.
"""
import json
import subprocess
from time import perf_counter

import lightgbm as lgb
import numpy as np
import pandas as pd
import yaml

from . import paths
from .config import configs_dir
from .subsurface import FEATURES, link_budget, regolith_properties

MARGIN_FLOOR_DB = -60.0
FORBIDDEN = {"link_margin_db", "margin_target_db", "absorption_db", "interface_db", "spreading_db", "slant_m", "link_ok", "best_prx", "best_id"}


def load_config(name="subsurface.yaml"):
    return yaml.safe_load((configs_dir() / name).read_text())


def sample_scenarios(cfg, n, seed, basalt_range=None):
    rng = np.random.default_rng(seed)
    s, m = cfg["sampling"], cfg["materials"]
    u = lambda lo_hi: rng.uniform(lo_hi[0], lo_hi[1], n)
    basalt = u(basalt_range or s["basalt_thickness_m"])
    if basalt_range is None:
        basalt[rng.random(n) < 0.1] = 0.0  # some tubes sit directly under regolith
    return pd.DataFrame(dict(
        frequency_ghz=rng.choice(s["frequency_ghz"], n),
        regolith_thickness_m=u(s["regolith_thickness_m"]),
        basalt_thickness_m=basalt,
        horizontal_offset_m=u(s["horizontal_offset_m"]),
        regolith_density_g_cc=u(m["regolith"]["density_g_cc"]),
        feo_tio2_wt_pct=u(m["regolith"]["feo_tio2_wt_pct"]),
        basalt_eps_real=u(m["basalt"]["eps_real"]),
        basalt_tan_delta=np.exp(rng.uniform(np.log(m["basalt"]["tan_delta"][0]), np.log(m["basalt"]["tan_delta"][1]), n)),
    ))


def label(df, radio):
    terms = link_budget(**{c: df[c].to_numpy() for c in FEATURES}, radio=radio)
    out = df.copy()
    out["link_margin_db"] = terms["link_margin_db"]
    # Margins far below the requirement are all "dead"; the model trains on a floor so a few thousand-dB
    # outliers (thick rock at 5.8 GHz) don't dominate. The unclipped physics value is kept in link_margin_db.
    out["margin_target_db"] = out.link_margin_db.clip(lower=MARGIN_FLOOR_DB)
    out["link_ok"] = out.link_margin_db >= radio["required_margin_db"]
    out["label_source"] = "simulated"
    return out


def check_features(names):
    if list(names) != FEATURES or FORBIDDEN & set(names):
        raise ValueError("Model inputs must be exactly the raw scenario parameters")


def _metrics(truth, pred, required):
    err = pred - truth
    dead = truth < required
    return dict(n=int(len(truth)), mae_db=float(np.mean(np.abs(err))), bias_db=float(np.mean(err)),
                rmse_db=float(np.sqrt(np.mean(err ** 2))),
                false_covered_rate=float(np.mean(pred[dead] >= required)) if dead.any() else None,
                dead_recall=float(np.mean(pred[dead] < required)) if dead.any() else None,
                dead_fraction=float(dead.mean()))


def run(cfg=None, n=None):
    cfg = cfg or load_config()
    radio, seed = cfg["radio"], cfg["seed"]
    n = n or cfg["sampling"]["n_train"]
    df = label(sample_scenarios(cfg, n, seed), radio)
    # Split on rows; each scenario is independent random draws, so there is no site/transmitter grouping to protect.
    rng = np.random.default_rng(seed + 1)
    r = rng.random(len(df)); f = cfg["split_fractions"]
    df["split"] = np.where(r < f["train"], "train", np.where(r < f["train"] + f["val"], "val", "test"))
    check_features(FEATURES)
    tr, va, te = (df[df.split == k] for k in ("train", "val", "test"))
    p = cfg["model"]
    reg = lgb.LGBMRegressor(n_estimators=p["n_estimators"], learning_rate=p["learning_rate"], num_leaves=p["num_leaves"],
                            random_state=seed, verbose=-1)
    reg.fit(tr[FEATURES], tr.margin_target_db, eval_set=[(va[FEATURES], va.margin_target_db)],
            callbacks=[lgb.early_stopping(50, verbose=False)])
    t0 = perf_counter(); pred = reg.predict(te[FEATURES]); infer = perf_counter() - t0
    ext = label(sample_scenarios(cfg, 2000, seed + 2, cfg["sampling"]["extrapolation_basalt_thickness_m"]), radio)
    report = dict(
        labels="simulated", seed=seed, n_scenarios=len(df),
        git_commit=subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.repo_root(), text=True, capture_output=True).stdout.strip() or None,
        material_status={k: v["status"] for k, v in cfg["materials"].items()},
        required_margin_db=radio["required_margin_db"], margin_floor_db=MARGIN_FLOOR_DB,
        in_distribution_test=_metrics(te.margin_target_db.to_numpy(), pred, radio["required_margin_db"]),
        # Extrapolation is reported, never tuned on: thicker rock than training. Expect worse.
        extrapolation_test=_metrics(ext.margin_target_db.to_numpy(), reg.predict(ext[FEATURES]), radio["required_margin_db"]),
        inference_seconds_for_test=infer, best_iteration=int(reg.best_iteration_ or 0))
    out = paths.reports_dir() / "subsurface"
    out.mkdir(parents=True, exist_ok=True)
    (out / "subsurface_model_report.json").write_text(json.dumps(report, indent=2))
    reg.booster_.save_model(str(paths.models_dir() / "subsurface_margin.txt"))
    return report
