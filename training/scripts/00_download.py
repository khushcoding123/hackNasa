"""Acquire terrain and simulated LunarRF benchmark data (nothing runs on import)."""

import argparse
from pathlib import Path
import random
import subprocess

import yaml

from moonmesh_train import paths
from moonmesh_train.downloads import (
    Downloader, atomic_json, extract_archives, extract_metadata, lunarrf_sites, lunarrf_url, pgda90_window,
    read_checksums, register_lola, safe_name, utc_now,
)


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--config", type=Path, default=paths.repo_root() / "training/configs/downloads.yaml")
    commands = cli.add_subparsers(dest="command", required=True)
    meta = commands.add_parser("lunarrf-meta", help="Fetch MANIFEST.txt and SHA256SUMS (simulated labels)")
    meta.add_argument("--yes", action="store_true", help="Allow a response over 2 GB or with unknown size")
    lunar = commands.add_parser("lunarrf", help="Fetch selected simulated LunarRF archives")
    lunar.add_argument("--parts", nargs="+", choices=["context", "1", "2", "3", "mosaics2", "candidates", "validation"], required=True)
    lunar.add_argument("--yes", action="store_true", help="Allow downloads over 2 GB or with unknown size")
    extract = commands.add_parser("extract", help="List tar contents, then extract selected simulated labels/metadata")
    extract.add_argument("--site", required=True, type=safe_name)
    extract.add_argument("--band", default="1p800GHz", type=safe_name)
    commands.add_parser("extract-meta", help="Extract metadata and validation figures from all downloaded LunarRF tars")
    commands.add_parser("sites", help="List exact site names and region archive file counts from local MANIFEST.txt")
    pgda = commands.add_parser("pgda78", help="Fetch 5 m site DEM and explicitly named companions")
    pgda.add_argument("--site", required=True, choices=["haworth", "malapert", "nobile-rim-1"])
    pgda.add_argument("--files", nargs="+", default=[], type=safe_name)
    pgda.add_argument("--yes", action="store_true", help="Allow downloads over 2 GB or with unknown size")
    window = commands.add_parser("pgda90-window", help="Read only a buffered remote raster window")
    window.add_argument("--bounds-from", type=Path, required=True)
    window.add_argument("--buffer-m", type=float, default=10000)
    commands.add_parser("lola-pds", help="Register cached PDS paths; use the app downloader only for missing files")
    return cli


def run(args, downloader):
    config = downloader.config
    if args.command in {"lunarrf-meta", "lunarrf"}:
        root = paths.raw_dir("lunarrf")
        if args.command == "lunarrf-meta":
            names = ["SHA256SUMS", "MANIFEST.txt"]
        else:
            if not (root / "SHA256SUMS").is_file():
                raise ValueError("Run lunarrf-meta first so archives can be checked against SHA256SUMS")
            names = [config["lunarrf"]["archives"][part] for part in dict.fromkeys(args.parts)]
        for name in names:
            checksums = read_checksums(root / "SHA256SUMS")
            downloader.download(lunarrf_url(config, name), root / name, "lunarrf",
                                expected=checksums.get(name), yes=args.yes)
    elif args.command == "sites":
        sites = lunarrf_sites(paths.raw_dir("lunarrf", create=False) / "MANIFEST.txt")
        for site, counts in sites.items():
            downloader.log(f"{site}:")
            for archive, count in counts.items():
                downloader.log(f"  {archive}: {count} files")
        return {"sites": sites, "label_provenance": "simulated"}
    elif args.command in {"extract", "extract-meta"}:
        root = paths.raw_dir("lunarrf", create=False)
        archives = (sorted(path for path in root.glob("lunarrf*.tar") if path.is_file())
                    if args.command == "extract-meta" else
                    [root / name for name in config["lunarrf"]["archives"].values() if (root / name).is_file()])
        if not archives:
            raise ValueError("No LunarRF archives found; run lunarrf first")
        if args.command == "extract-meta":
            outputs = extract_metadata(archives, paths.interim_dir() / "lunarrf" / "meta",
                                       config["lunarrf"]["archives"]["validation"], log=downloader.log)
            return {"outputs": outputs, "label_provenance": "simulated"}
        outputs = extract_archives(archives, paths.interim_dir() / "lunarrf" / args.site,
                                   args.site, args.band, config["lunarrf"]["archives"]["context"],
                                   log=downloader.log)
        return {"outputs": outputs, "label_provenance": "simulated"}
    elif args.command == "pgda78":
        folder = config["pgda78"]["sites"][args.site]
        url = config["pgda78"]["base_url"] + folder + "/"
        downloader.log(f"Companion slope/uncertainty/count files: inspect {url}")
        names = [f"{folder}_final_adj_5mpp_surf.tif", *args.files]
        from urllib.parse import quote
        for name in dict.fromkeys(names):
            downloader.download(url + quote(name), paths.raw_dir("pgda78") / args.site / name,
                                "pgda78", yes=args.yes)
    elif args.command == "pgda90-window":
        return pgda90_window(downloader, args.bounds_from, args.buffer_m)
    elif args.command == "lola-pds":
        register_lola(downloader)


def main(argv=None):
    args = parser().parse_args(argv)
    messages = []

    def log(message):
        messages.append(str(message))
        print(message, flush=True)

    report = {"command": args.command, "started_utc": utc_now(), "messages": messages}
    code = 0
    try:
        config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
        random.seed(config["seed"])
        report.update(config=str(args.config.resolve()), seed=config["seed"])
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.repo_root(),
                                capture_output=True, text=True, check=False, timeout=10)
        report["git_commit"] = commit.stdout.strip() if commit.returncode == 0 else None
        log(f"Git commit: {report['git_commit']}; seed: {config['seed']}")
        if args.command in {"lunarrf-meta", "lunarrf", "extract", "extract-meta", "sites"}:
            report["label_provenance"] = "simulated"
            log("LunarRF labels are simulated")
        downloader = Downloader(config, paths.repo_root() / "training/data_manifest.json", log=log)
        try:
            report["result"] = run(args, downloader)
        finally:
            downloader.session.close()
        report["status"] = "complete"
    except Exception as exc:
        log(f"ERROR: {exc}")
        report.update(status="failed", error=str(exc))
        code = 1
    finally:
        report["finished_utc"] = utc_now()
        stamp = report["started_utc"].replace(":", "").replace("+", "_")
        output = paths.reports_dir() / f"download_{args.command}_{stamp}.json"
        atomic_json(output, report)
        print(f"Report: {output}", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
