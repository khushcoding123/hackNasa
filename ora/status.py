"""What this checkout actually has: code revision, data, trained models and libraries.

    python -m ora preflight            readable inventory, exit code 1 when required inputs are missing
    python -m ora preflight --json     the same as JSON
    python -m ora preflight --no-hash  skip SHA-256 of the large LOLA products (several hundred MB each)

The model card (``ora/model_card.json``) records what was trained once. It is not evidence that a model is
loaded now. ``models()`` is the single place the app loads trained models, and ``model_status()`` reports
what was actually loaded, from which file (hash), with which library version, or why it was not.
Model loading is keyed on each file's size and modification time, so replacing an artifact on disk is
picked up on the next request instead of being hidden by a stale cache.
"""
import hashlib
import json
import subprocess
import sys
import warnings
from datetime import UTC, datetime
from functools import lru_cache
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = Path(__file__).with_name("model_card.json")
LARGE = 50_000_000  # bytes; files above this are hashed only when asked
# Trained artifacts, the model-card section that describes each, and what the app does without it.
MODELS = {
    # The app ranks and selects relays by computed line of sight; these models are research and benchmark artifacts.
    "ora_siting_prior": dict(card="siting_prior", fallback="not used by the app; ora.evaluate and ora.stress cannot score it"),
    "ora_coverage_surrogate": dict(card="coverage_surrogate", fallback="not used by the app; ora.benchmark and ora.stress cannot score it"),
    "ora_gain_model": dict(card="marginal_gain", fallback="not used by the app; ora.benchmark and ora.stress cannot score it"),
    "ora_view_estimator": dict(card="view_estimator", fallback="not used by the app", train="python -m ora accuracy"),
    "ora_gain_selector": dict(card="gain_selector", fallback="not used by the app", train="python -m ora accuracy"),
    # Research / experimental thermal hazard model trained on Diviner data; the dashboard shows it beside the physics, never instead of it.
    "ora_hazard_thermal": dict(card="hazard_thermal", fallback="the Hazard response view reports the model as unavailable; mission results are unchanged",
                               train="python training/scripts/18_build_hazard_dataset.py then 19_train_hazard.py (docs/HAZARD_RESPONSE.md; `python -m ora preflight` shows the state)"),
}
LIBRARIES = ("numpy", "scipy", "spiceypy", "fastapi", "uvicorn", "networkx", "scikit-learn", "PyYAML", "joblib", "pydantic", "pytest", "httpx")


def sha256(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while block := file.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def _stamp(seconds):
    return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="seconds")


def file_entry(path, purpose, required, hash_large=True, size=None):
    path = Path(path)
    entry = dict(path=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path), purpose=purpose,
                 required=required, present=path.is_file())
    if not entry["present"]:
        entry["problem"] = "missing"
        return entry
    stat = path.stat()
    entry.update(bytes=stat.st_size, modified=_stamp(stat.st_mtime))
    if size is not None and stat.st_size != size:
        entry["problem"] = f"expected {size:,} bytes, found {stat.st_size:,} (truncated or wrong product)"
    if hash_large or stat.st_size <= LARGE:
        entry["sha256"] = _hash_cached(str(path), stat.st_size, stat.st_mtime_ns)
    return entry


@lru_cache(maxsize=512)
def _hash_cached(path, size, mtime_ns):
    return sha256(path)


# ---------------------------------------------------------------------------------------------- models

@lru_cache(maxsize=16)
def _load(path, size, mtime_ns):
    """Load one artifact. Cached per (path, size, mtime), so a changed file is reloaded."""
    import joblib
    record = dict(loaded=False, error=None, warnings=[])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            model = joblib.load(path)
        except Exception as error:  # an unreadable or incompatible artifact is reported, never silently ignored
            model = None
            record["error"] = f"{type(error).__name__}: {error}"
    record["warnings"] = sorted({f"{w.category.__name__}: {w.message}" for w in caught})
    # scikit-learn drops the saved version on load and warns only when it differs from the running one.
    saved = next((getattr(w.message, "original_sklearn_version", None) for w in caught
                  if w.category.__name__ == "InconsistentVersionWarning"), None)
    if model is not None:
        from importlib import metadata
        running = metadata.version("scikit-learn") if "sklearn" in type(model).__module__ else None
        record.update(loaded=True, estimator=type(model).__name__, n_features_in=getattr(model, "n_features_in_", None),
                      saved_with_sklearn=saved or running,
                      version_check="mismatch" if saved else "no mismatch warning on load" if running else "not a scikit-learn model")
        if not hasattr(model, "predict"):
            record.update(loaded=False, error=f"{type(model).__name__} has no predict method")
            model = None
    return model, record


def _card():
    try:
        return json.loads(CARD.read_text())
    except (OSError, ValueError):
        return None


def models():
    """{name: (model or None, status)} for every trained artifact, loading changed files afresh."""
    from moonmesh.data import DATA
    card = _card() or {}
    try:
        running = metadata.version("scikit-learn")
    except metadata.PackageNotFoundError:
        running = None
    out = {}
    for name, info in MODELS.items():
        path = DATA / "models" / f"{name}.joblib"
        status = dict(name=name, path=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                      present=path.is_file(), loaded=False, fallback=info["fallback"], running_sklearn=running)
        model = None
        if status["present"]:
            stat = path.stat()
            model, record = _load(str(path), stat.st_size, stat.st_mtime_ns)
            status.update(record, bytes=stat.st_size, modified=_stamp(stat.st_mtime),
                          sha256=_hash_cached(str(path), stat.st_size, stat.st_mtime_ns))
            if status.get("version_check") == "mismatch":
                status["warnings"] = status["warnings"] + [f"saved with scikit-learn {status['saved_with_sklearn']}, running {running}"]
        else:
            status["error"] = f"file not found; run {info.get('train', 'python -m ora.train')}"
        section = card.get(info["card"])
        recorded = (card.get("artifacts") or {}).get(name, {})
        status["model_card"] = dict(section_present=section is not None, trained=card.get("trained"),
                                    recorded_sha256=recorded.get("sha256"))
        if recorded.get("sha256") and status.get("sha256"):
            status["model_card"]["matches_file"] = recorded["sha256"] == status["sha256"]
        else:
            status["model_card"]["matches_file"] = None  # older cards did not record artifact hashes
        out[name] = (model, status)
    return out


def model_status():
    """JSON-safe status of every trained artifact (no model objects)."""
    return [status for _, status in models().values()]


def loaded(name):
    """The loaded model, or None."""
    return models()[name][0]


# --------------------------------------------------------------------------------------------- inventory

def git_state():
    """Commit, branch and a hash of uncommitted changes (tracked diff plus untracked files), so a report can be tied to code."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout
    try:
        commit = git("rev-parse", "HEAD").decode().strip()
        branch = git("rev-parse", "--abbrev-ref", "HEAD").decode().strip()
        diff = git("diff", "HEAD", "--binary")
        untracked = git("ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
    except (OSError, subprocess.CalledProcessError) as error:
        return dict(commit=None, error=f"git unavailable: {error}")
    digest = hashlib.sha256(diff)
    for name in sorted(n for n in untracked if n):
        digest.update(name.encode())
        try:
            digest.update(sha256(ROOT / name).encode())
        except OSError:
            pass
    changed = bool(diff) or any(untracked)
    return dict(commit=commit, branch=branch, uncommitted_changes=changed,
                diff_sha256=digest.hexdigest() if changed else None, untracked_files=sum(1 for n in untracked if n))


def libraries():
    from packaging.requirements import Requirement  # ships with pip and setuptools
    wanted, role = {}, {}
    for scope, name in (("app", "requirements.txt"), ("tests", "requirements-dev.txt")):
        for line in (ROOT / name).read_text().splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith("-"):
                requirement = Requirement(line)
                wanted[requirement.name.lower()] = requirement
                role.setdefault(requirement.name.lower(), scope)
    rows = [dict(name="python", version=sys.version.split()[0], required=True, needed_for="app", ok=sys.version_info >= (3, 11))]
    # Every package the requirement files list is checked, not only the ones named above: a missing runtime
    # dependency (for example XlsxWriter, imported by the server at start-up) must not hide behind a READY status.
    listed = {name.lower() for name in LIBRARIES}
    names = list(LIBRARIES) + [r.name for key, r in wanted.items() if key not in listed]
    for name in names:
        requirement = wanted.get(name.lower())
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = None
        scope = role.get(name.lower())
        # Only app requirements decide whether results can be produced; test dependencies are reported separately.
        row = dict(name=name, version=version, required=scope == "app", needed_for=scope or "indirect",
                   specifier=str(requirement.specifier) if requirement else None)
        row["ok"] = version is not None and (requirement is None or requirement.specifier.contains(version, prereleases=True))
        if version is None:
            row["problem"] = "not installed" + {"app": "", "tests": " (needed to run the tests)"}.get(scope, " (optional)")
        elif not row["ok"]:
            row["problem"] = f"{version} does not satisfy {requirement.specifier}"
        rows.append(row)
    return rows


def inventory(hash_large=True):
    """Every input the app and the training pipeline read, with presence, size and checksum."""
    from lunar_core.kernels import KERNELS
    from moonmesh.data import DATA, PRODUCTS, SITES
    from moonmesh.globaldem import COLS, PATH as GLOBAL_DEM, ROWS
    from .data import EARTH, EARTH_TILES, ORA_SITES
    groups = {}
    groups["spice_kernels"] = [file_entry(DATA / "kernels" / name, "Sun and Earth geometry (NAIF generic kernels)", True, hash_large)
                               for name in KERNELS]
    lola = []
    for product, (dimension, _) in PRODUCTS.items():
        lola.append(file_entry(DATA / "terrain" / f"{product}.LBL", f"{product} label", True, hash_large))
        lola.append(file_entry(DATA / "terrain" / f"{product}.IMG", f"LOLA {product} south polar DEM", True, hash_large, size=dimension ** 2 * 2))
    groups["lola_products"] = lola
    groups["site_terrain"] = [file_entry(DATA / "terrain" / f"{key}_{suffix}.npz", f"{name} ({suffix})", True, hash_large)
                              for key, (name, _, _) in {**SITES, **ORA_SITES}.items() for suffix in ("near20km", "far")]
    groups["global_terrain"] = [file_entry(GLOBAL_DEM, "LOLA LDEM_64, drawn areas outside the 85 S cap", False, hash_large, size=ROWS * COLS * 2)]
    groups["earth_training"] = [file_entry(EARTH / "r_tower.zip", "FCC Antenna Structure Registration (siting-prior labels)", False, hash_large)] + \
        [file_entry(EARTH / f"{tile}.hgt.gz", f"SRTM-derived tile {tile} (siting-prior terrain)", False, hash_large) for tile in EARTH_TILES]
    windows = DATA / "ora_cache" / "windows"
    files = sorted(windows.glob("*.npz")) if windows.is_dir() else []
    groups["lunar_windows"] = [dict(path=str(windows.relative_to(ROOT)), purpose="lunar training and sealed test windows (ora.lunar_windows)",
                                    required=False, present=bool(files), base_windows=sum("__" not in f.stem for f in files),
                                    variant_windows=sum("__" in f.stem for f in files), **({} if files else dict(problem="missing")))]
    groups["frontend"] = [file_entry(ROOT / "web" / "app.js", "built mission UI (npm run build)", False, hash_large),
                          file_entry(ROOT / "web" / "ora.js", "built ORA UI (npm run build)", False, hash_large),
                          dict(path="node_modules/cesium", purpose="3D Moon viewer", required=False,
                               present=(ROOT / "node_modules" / "cesium").is_dir(),
                               **({} if (ROOT / "node_modules" / "cesium").is_dir() else dict(problem="missing; run npm ci")))]
    model_rows = model_status()
    libs = libraries()
    required_missing = [e["path"] for group in groups.values() for e in group if e["required"] and e.get("problem")]
    required_missing += [f"{lib['name']} ({lib.get('problem')})" for lib in libs if lib["required"] and not lib["ok"]]
    optional_missing = [e["path"] for group in groups.values() for e in group if not e["required"] and e.get("problem")]
    optional_missing += [lib["name"] for lib in libs if not lib["required"] and not lib["ok"]]
    unloaded = [m["name"] for m in model_rows if not m["loaded"]]
    warned = [m["name"] for m in model_rows if m["loaded"] and m.get("warnings")]
    if required_missing:
        overall = "not_ready"
    elif unloaded or optional_missing or warned:
        overall = "degraded"
    else:
        overall = "ready"
    return dict(generated=datetime.now(UTC).isoformat(timespec="seconds"), status=overall, code=git_state(),
                required_missing=required_missing, optional_missing=optional_missing, models_not_loaded=unloaded,
                models_with_warnings=warned, models=model_rows, libraries=libs, data=groups, large_files_hashed=hash_large,
                meaning={"ready": "every required input is present and every trained model loaded cleanly",
                         "degraded": "the app runs, but a research model failed to load or optional inputs are missing",
                         "not_ready": "required inputs are missing; results that need them cannot be produced"})


def render(report):
    mark = lambda ok: "ok  " if ok else "MISS"
    lines = [f"MOONMESH preflight  {report['generated']}", f"status: {report['status'].upper()}  ({report['meaning'][report['status']]})"]
    code = report["code"]
    lines.append(f"code: {code.get('commit')} on {code.get('branch')}" + (f", uncommitted changes {code['diff_sha256'][:16]}" if code.get("uncommitted_changes") else ", clean"))
    lines.append("\nmodels (what is loaded now, not what the model card says):")
    for m in report["models"]:
        state = "loaded" if m["loaded"] else "NOT LOADED"
        lines.append(f"  {mark(m['loaded'])} {m['name']:<24} {state}" + (f"  {m.get('estimator')}  sha256 {m['sha256'][:12]}" if m.get("sha256") else ""))
        if m.get("error"):
            lines.append(f"       error: {m['error']}")
        for warning in m.get("warnings", []):
            lines.append(f"       warning: {warning}")
        if not m["loaded"]:
            lines.append(f"       fallback: {m['fallback']}")
        if m["model_card"]["matches_file"] is None and m["loaded"]:
            lines.append("       model card has no recorded hash for this file, so the card cannot confirm it describes this artifact")
        elif m["model_card"]["matches_file"] is False:
            lines.append("       model card hash differs from this file: the card describes a different artifact")
    lines.append("\nlibraries:")
    for lib in report["libraries"]:
        lines.append(f"  {mark(lib['ok'])} {lib['name']:<14} {lib['version'] or '-':<12} {lib['needed_for']:<9} {lib.get('specifier') or ''} {lib.get('problem', '')}".rstrip())
    for group, entries in report["data"].items():
        lines.append(f"\n{group}:")
        for e in entries:
            tag = "required" if e["required"] else "optional"
            detail = e.get("problem") or (f"{e['bytes']:,} B" if "bytes" in e else f"{e.get('base_windows', 0)} windows + {e.get('variant_windows', 0)} variants" if "base_windows" in e else "")
            digest = f"  sha256 {e['sha256'][:12]}" if e.get("sha256") else ""
            lines.append(f"  {mark(not e.get('problem'))} {e['path']}  [{tag}]  {detail}{digest}")
    if report["required_missing"]:
        lines.append("\nRequired inputs missing: " + ", ".join(report["required_missing"]))
        lines.append("Run python -m moonmesh.data and python -m ora.data (see README).")
    return "\n".join(lines)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="python -m ora preflight")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-hash", action="store_true", help="skip SHA-256 of files over 50 MB")
    args = parser.parse_args(argv)
    report = inventory(hash_large=not args.no_hash)
    print(json.dumps(report, indent=1) if args.json else render(report))
    return 1 if report["status"] == "not_ready" else 0


if __name__ == "__main__":
    sys.exit(main())


def stamp():
    """Provenance for a saved report: when, which code, which model files."""
    return dict(generated=datetime.now(UTC).isoformat(timespec="seconds"), code=git_state(),
                models={m["name"]: m.get("sha256") for m in model_status()})
