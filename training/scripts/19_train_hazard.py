"""Train the thermal hazard model (research / experimental) and evaluate it on held-out spatial blocks.

    C:\\moonmesh-data\\venv\\Scripts\\python.exe training/scripts/19_train_hazard.py

Reads <data root>/derived/datasets/hazard_thermal_v1.npz (make it with 18_build_hazard_dataset.py), writes the model to the
app model directory (.moonmesh-data/models/ora_hazard_thermal.joblib), a report to <data root>/reports/hazard_thermal/
and records the result in ora/model_card.json. The test blocks are scored once, after model selection on the validation blocks.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "training" / "src")]   # works without the editable install

from moonmesh_train import hazard_dataset as hd  # noqa: E402
from moonmesh_train import hazard_model as hm  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=None, help="default: training/configs/hazard_thermal.yaml")
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--model-out", type=Path, default=None, help="default: .moonmesh-data/models/ora_hazard_thermal.joblib")
    parser.add_argument("--no-card", action="store_true", help="do not update ora/model_card.json")
    args = parser.parse_args(argv)
    config = hd.load_config(args.config)
    report = hm.run(config, args.dataset, args.model_out, card_path=None if args.no_card else ROOT / "ora" / "model_card.json")
    print(hm.render(report))
    print(json.dumps(report["model_selection_on_validation_blocks"], indent=1))
    print("commit", report["git_commit"], "model", report["model_file"], report["model_sha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
