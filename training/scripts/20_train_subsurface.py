"""Build simulated underground-to-surface link labels and train a LightGBM surrogate (see docs/SUBSURFACE_LINK.md)."""
import argparse
import json

from moonmesh_train.subsurface_model import load_config, run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="subsurface.yaml", help="name under training/configs/")
    ap.add_argument("--n", type=int, default=None, help="override sampling.n_train")
    a = ap.parse_args(argv)
    print(json.dumps(run(load_config(a.config), a.n), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
