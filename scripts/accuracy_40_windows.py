"""Per-window accuracy of the ORA coverage estimators on 40 held-out lunar terrain windows.

    python scripts/accuracy_40_windows.py            run everything, write the report
    python scripts/accuracy_40_windows.py --help

A "dataset" here is one 20 km real-LOLA terrain window. Truth is dense line of sight (ora.accuracy.dense_truth):
for each of 150 sampled sites, the share of 6,000 random ground points within 8 km that a 2 m receiver sees from a
10 m mast. The 40 windows are all 8 HOLD and all 16 LEGACY windows plus 16 buffer windows. None of them was used
to train or tune the view estimator (which is trained on DEV windows only). Choice of buffer windows: fixed seed.

Read-only against the shared .moonmesh-data cache. Anything new (the 16 buffer windows' truth) goes to tmp/accuracy40_cache.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OWN = ROOT / "tmp" / "accuracy40_cache"
SEED = 40
BUFFER_WINDOWS = 16


def use_own_cache():
    """Point the ORA window and truth caches at a private folder (also called in each worker process)."""
    from ora import accuracy, lunar_windows
    (OWN / "windows").mkdir(parents=True, exist_ok=True)
    (OWN / "truth").mkdir(parents=True, exist_ok=True)
    lunar_windows.CACHE, accuracy.CACHE = OWN / "windows", OWN / "truth"


def compute(w):
    use_own_cache()
    from ora.accuracy import window_truth
    window_truth(w)
    return w


def choose(sp, shared_windows):
    """HOLD + LEGACY + BUFFER_WINDOWS never-trained windows that already have a labelled-window cache."""
    import numpy as np
    from ora.lunar_windows import MIN_VALID, crop_xy, key, lattice
    keep = [w for w in lattice() if (g := crop_xy(*w)) is not None and np.isfinite(g.heights).mean() >= MIN_VALID]
    seen = set(map(tuple, sp["dev"])) | set(map(tuple, sp["hold"])) | set(map(tuple, sp["legacy"]))
    pool = sorted(w for w in keep if w not in seen and (shared_windows / f"{key(*w)}.npz").is_file())
    rng = np.random.default_rng(SEED)
    buffer = [pool[i] for i in sorted(rng.choice(len(pool), BUFFER_WINDOWS, replace=False))]
    return [("hold", tuple(w)) for w in sp["hold"]] + [("legacy", tuple(w)) for w in sp["legacy"]] + [("buffer", w) for w in buffer], len(pool)


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=6, help="processes for the new truth (default 6)")
    parser.add_argument("--out", default=str(ROOT / "docs" / "ACCURACY_40_WINDOWS.json"))
    args = parser.parse_args()
    t0 = time.time()
    import joblib
    from moonmesh.data import DATA
    from ora import accuracy, lunar_windows
    from ora.accuracy import metrics, splits, table
    from ora.lunar_windows import key
    shared = dict(windows=lunar_windows.CACHE, truth=accuracy.CACHE)
    sp = splits()
    plan, pool_size = choose(sp, shared["windows"])
    assert len(plan) == 40 and len({w for _, w in plan}) == 40

    # Which model files are these? They must be the ones the model card documents.
    model_path = DATA / "models" / "ora_view_estimator.joblib"
    card = json.loads((ROOT / "ora" / "model_card.json").read_text())
    expected = card.get("artifacts", {}).get("ora_view_estimator", {}).get("sha256")
    model_sha = sha256(model_path)
    fast = joblib.load(model_path)

    # 1. dense truth for the buffer windows that the shared cache does not have (own cache; shared stays untouched).
    import shutil
    todo = []
    for name, w in plan:
        if name != "buffer":
            continue
        if not (OWN / "windows" / f"{key(*w)}.npz").is_file():
            (OWN / "windows").mkdir(parents=True, exist_ok=True)
            shutil.copy(shared["windows"] / f"{key(*w)}.npz", OWN / "windows" / f"{key(*w)}.npz")
        if not (OWN / "truth" / f"{key(*w)}.npz").is_file():
            todo.append(w)
    if todo:
        import multiprocessing as mp
        print(f"computing dense truth for {len(todo)} buffer windows", flush=True)
        with mp.Pool(min(args.workers, len(todo)), initializer=use_own_cache) as pool:
            for i, _ in enumerate(pool.imap_unordered(compute, todo), 1):
                print(f"  {i}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)

    # 2. score each window on its own.
    rows = []
    for number, (split_name, w) in enumerate(plan, 1):
        if split_name == "buffer":
            use_own_cache()
        else:
            lunar_windows.CACHE, accuracy.CACHE = shared["windows"], shared["truth"]
        t = table([w])
        row = dict(n=number, window=key(*w), split=split_name, x_m=w[0], y_m=w[1], sites=int(len(t["truth"])),
                   mean_true_coverage_pct=100 * float(np.mean(t["truth"])), std_true_coverage_pct=100 * float(np.std(t["truth"])))
        row["fast_view_estimator"] = metrics(t["truth"], fast.predict(t["fast"]))
        row["app_computed_viewshed"] = metrics(t["truth"], t["sweep120_s1"])
        rows.append(row)
        print(f"{number:2d}/40 {row['window']} {split_name:6s} fast within-5pt {row['fast_view_estimator']['within_5_points_pct']:5.1f}%  "
              f"app {row['app_computed_viewshed']['within_5_points_pct']:5.1f}%", flush=True)

    def pooled(name, predicate=lambda r: True):
        chosen = [r for r in rows if predicate(r)]
        keys = ("r2_pct", "spearman_pct", "mae_points", "within_5_points_pct", "within_10_points_pct")
        return {k: float(np.mean([r[name][k] for r in chosen])) for k in keys} | dict(windows=len(chosen))

    summary = {name: {"all_40": pooled(name), **{s: pooled(name, lambda r, s=s: r["split"] == s) for s in ("hold", "legacy", "buffer")}}
               for name in ("fast_view_estimator", "app_computed_viewshed")}
    report = dict(
        what="Per-window accuracy of coverage estimators against dense line-of-sight truth on 40 held-out real-LOLA windows",
        truth=f"{accuracy.TRUTH_CELLS} sites per window, {accuracy.TRUTH_RAYS} random ground points each within 8 km, 10 m mast, 2 m receiver",
        accuracy_definition="within_5_points_pct = share of sites whose estimate is within 5 percentage points of dense truth",
        windows=rows, summary_mean_of_windows=summary, buffer_pool_with_cache=pool_size, seed=SEED,
        model=dict(path=str(model_path), sha256=model_sha, card_sha256=expected, matches_card=model_sha == expected),
        git_commit=git("rev-parse", "HEAD"), git_dirty=bool(git("status", "--porcelain")), seconds=round(time.time() - t0))
    Path(args.out).write_text(json.dumps(report, indent=1))
    print(f"wrote {args.out} in {report['seconds']} s; model matches card: {report['model']['matches_card']}")


if __name__ == "__main__":
    main()
