"""Build the thermal hazard dataset (Diviner PRP labels + LOLA 240 m terrain features).

Run from the repository root with the training Python:
    PYTHONPATH=.;training/src  C:\\moonmesh-data\\venv\\Scripts\\python.exe training/scripts/18_build_hazard_dataset.py

--download fetches the PRP table (604,800,210 bytes, NASA PDS) into <data root>/raw/diviner_prp/ if it is absent.
Existing raw files are never overwritten.
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "training" / "src")]   # works without the editable install

from moonmesh_train import hazard_dataset as hd  # noqa: E402


def download(config):
    target_dir = hd.raw_prp_dir(config)
    for key in ("prp_label_url", "prp_xml_url", "prp_south_url"):
        url = config["source"][key]
        target = target_dir / url.rsplit("/", 1)[1]
        if target.exists() and (key != "prp_south_url" or target.stat().st_size == config["source"]["prp_bytes"]):
            continue
        print(f"downloading {url} -> {target}", flush=True)
        partial = target.with_suffix(target.suffix + ".partial")
        with urlopen(url, timeout=90) as source, partial.open("wb") as file:
            while block := source.read(1 << 20):
                file.write(block)
        partial.replace(target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=None, help="default: training/configs/hazard_thermal.yaml")
    parser.add_argument("--download", action="store_true", help="fetch the PRP files from NASA PDS if missing")
    parser.add_argument("--out", type=Path, default=None, help="default: <data root>/derived/datasets/<config output.dataset>")
    args = parser.parse_args(argv)
    config = hd.load_config(args.config)
    if args.download:
        download(config)
    out, manifest = hd.build(config, args.out)
    print(f"wrote {out}\ncommit {manifest['git_commit']}\n" + json.dumps(manifest["rows"], indent=1))
    print(json.dumps(manifest["registration_check"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
