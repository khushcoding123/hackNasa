"""Prompt 12: reproduce four published lunar coverage studies with our deterministic viewshed (simulated).

Writes <data root>/reports/benchmarks/configs.md (every value each study states, with pages), results.json and
report.md (ours vs theirs, % difference, likely reasons). Simulator-to-simulator consistency, not validation.
Deterministic: no random sampling; the git commit is logged in results.json.
"""
import argparse

from moonmesh_train.benchmarks import load_config, run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="benchmarks.yaml", help="name under training/configs/")
    ap.add_argument("--case", action="append", help="run only this case id (repeatable); default: all")
    args = ap.parse_args(argv)
    results = run(load_config(args.config), args.case)
    print(f"{len(results['cases'])} case(s) written; commit {results['git_commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
