"""One command that makes this checkout demonstrable: check everything, say exactly what is missing, optionally fix it.

    python -m moonmesh.setup                 check only (nothing is downloaded or installed); exit code 1 if the app cannot run
    python -m moonmesh.setup --apply         also install missing Python packages, fetch NASA terrain/kernels, build the web UI
    python -m moonmesh.setup --apply --train also rebuild missing trained research models (slow; downloads training data)
    python -m moonmesh.setup --json          machine-readable report

Steps, in order: Python and packages (requirements.txt, including the ones the server imports on start-up), SPICE kernels
and LOLA terrain (python -m moonmesh.data), the built web UI, trained research models, and a real end-to-end smoke run of
the mission calculation. The calculation does not need the trained models: relay ranking is the computed viewshed.
It is safe to run again: every fix is skipped when its result is already present.
"""
import argparse
import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Command lines that rebuild each trained model, in dependency order (also printed by `python -m ora preflight`).
TRAIN_STEPS = (
    [sys.executable, "-m", "ora.data", "--earth"],
    [sys.executable, "-m", "ora.lunar_windows"],
    [sys.executable, "-m", "ora.train"],
    [sys.executable, "-m", "ora", "accuracy"],
)


def step(name, ok, detail, fix=None, required=True):
    return dict(name=name, ok=bool(ok), detail=detail, fix=fix, required=required)


def run(command, cwd=ROOT):
    print("  $ " + " ".join(str(part) for part in command), flush=True)
    return subprocess.run([str(part) for part in command], cwd=cwd).returncode == 0


def installer():
    """pip when this interpreter has it (uv-made environments often do not), otherwise uv."""
    if importlib.util.find_spec("pip"):
        return [sys.executable, "-m", "pip", "install"]
    if shutil.which("uv"):
        return ["uv", "pip", "install", "--python", sys.executable]
    return None


def check_packages():
    from ora.status import libraries
    rows = libraries()
    bad = [row for row in rows if row["required"] and not row["ok"]]
    imports_ok, why = True, ""
    try:
        importlib.import_module("moonmesh.server")   # what `uvicorn moonmesh.server:app` does first
    except Exception as error:   # noqa: BLE001 - any import failure means the server will not start
        imports_ok, why = False, f"{type(error).__name__}: {error}"
    detail = ", ".join(f"{r['name']} ({r.get('problem')})" for r in bad) or f"{len(rows)} packages checked"
    if not imports_ok:
        detail += f"; the server cannot be imported ({why})"
    return step("Python packages", not bad and imports_ok, detail,
                fix="pip install -r requirements.txt  (python -m moonmesh.setup --apply does this)")


def check_data():
    from ora.status import inventory
    report = inventory(hash_large=False)
    groups = report["data"]
    missing = [e["path"] for key in ("spice_kernels", "lola_products", "site_terrain") for e in groups[key] if e.get("problem")]
    return step("SPICE kernels and LOLA terrain", not missing,
                 f"{len(missing)} missing: " + ", ".join(missing[:4]) + (" ..." if len(missing) > 4 else "") if missing
                 else "all kernels, LOLA products and site windows present",
                 fix="python -m moonmesh.data  (large download; --apply does this)"), report


def check_frontend():
    built = (ROOT / "web" / "app.js").is_file() and (ROOT / "web" / "cesium").is_dir()
    return step("Web UI build", built, "web/app.js and web/cesium present" if built else "web/app.js or web/cesium missing",
                fix="npm ci && npm run build  (--apply does this when Node is installed)", required=False)


def check_models(report):
    rows = report["models"]
    missing = [m["name"] for m in rows if not m["loaded"]]
    detail = (f"{len(rows) - len(missing)}/{len(rows)} loaded" if not missing else
              f"{len(rows) - len(missing)}/{len(rows)} loaded; not loaded: {', '.join(missing)}. The app still runs; these are research/benchmark models")
    return step("Trained research models", not missing, detail,
                fix="python -m moonmesh.setup --apply --train  (slow, downloads training data)", required=False)


def smoke():
    """A short real mission: 3 days, hourly, one relay. Proves terrain, kernels, geometry and thermal model work together."""
    from moonmesh.models import Mission
    from moonmesh.science import scenario
    start = date(2026, 11, 1)
    try:
        result = scenario(Mission(start=start, end=start + timedelta(days=3), minutes=60, max_relays=1), include_map=False)
    except Exception as error:   # noqa: BLE001
        return step("End-to-end smoke run", False, f"{type(error).__name__}: {error}", fix="fix the failing steps above, then run again")
    ok = bool(result["times"]) and bool(result["candidates"]) and 0 <= result["direct"]["availability"] <= 100
    return step("End-to-end smoke run", ok,
                f"Haworth, 3 days: {len(result['candidates'])} candidate relays, direct Earth availability {result['direct']['availability']:.1f}%, "
                f"recommended {result['recommended'] or 'none'}")


def apply_fixes(args, packages_ok, data_ok, frontend_ok):
    if not packages_ok:
        command = installer()
        print("Installing Python packages")
        if command is None:
            print("  neither pip nor uv is available for this interpreter; install requirements.txt manually")
        else:
            run(command + ["-r", ROOT / "requirements.txt"])
    if not data_ok:
        print("Fetching NASA kernels and LOLA terrain (large download)")
        try:
            from moonmesh import data
            data.main()
            from ora import data as ora_data
            ora_data.prepare_sites()
        except Exception as error:   # noqa: BLE001
            print(f"  failed: {type(error).__name__}: {error}")
    if not frontend_ok:
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if npm is None:
            print("Skipping the web UI build: Node.js/npm is not installed (https://nodejs.org)")
        elif run([npm, "ci"]):
            run([npm, "run", "build"])
    if args.train:
        print("Rebuilding trained research models")
        for command in TRAIN_STEPS:
            if not run(command):
                print("  stopped: a training step failed")
                break


def render(steps):
    lines = []
    for item in steps:
        mark = "ok  " if item["ok"] else ("MISS" if item["required"] else "warn")
        lines.append(f"[{mark}] {item['name']}: {item['detail']}")
        if not item["ok"] and item["fix"]:
            lines.append(f"       fix: {item['fix']}")
    return "\n".join(lines)


def collect(args):
    packages = check_packages()
    steps = [packages]
    data_ready = frontend = None
    if packages["ok"]:
        data_step, report = check_data()
        steps.append(data_step)
        steps.append(check_frontend())
        steps.append(check_models(report))
        data_ready, frontend = data_step["ok"], steps[2]["ok"]
        if data_step["ok"] and not args.no_smoke:
            steps.append(smoke())
        elif not data_step["ok"]:
            steps.append(step("End-to-end smoke run", False, "skipped: terrain or kernels missing", fix=data_step["fix"]))
    else:
        steps.append(step("Everything after this", False, "skipped until the Python packages import cleanly", fix=packages["fix"]))
    return steps, data_ready, frontend


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m moonmesh.setup", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="install, download and build whatever is missing")
    parser.add_argument("--train", action="store_true", help="with --apply: also rebuild missing trained models")
    parser.add_argument("--no-smoke", action="store_true", help="skip the end-to-end mission run")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    steps, data_ready, frontend = collect(args)
    for _ in range(2 if args.apply else 0):   # second round: terrain can only be checked once the packages import
        wanted = [s for s in steps if s["required"] and not s["ok"]]
        wanted += [s for s in steps if not s["required"] and not s["ok"] and (s["name"] == "Web UI build" or args.train)]
        if not wanted:
            break
        apply_fixes(args, steps[0]["ok"], data_ready is not False, frontend is not False)
        importlib.invalidate_caches()
        steps, data_ready, frontend = collect(args)
    ready = all(s["ok"] for s in steps if s["required"])
    if args.json:
        print(json.dumps(dict(ready=ready, steps=steps), indent=1))
    else:
        print(render(steps))
        print("\nREADY: start the app with scripts/start.ps1 (or python -m uvicorn moonmesh.server:app) and open http://127.0.0.1:8765"
              if ready else "\nNOT READY: fix the lines marked MISS (python -m moonmesh.setup --apply does the automatic ones).")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
