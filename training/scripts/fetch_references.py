"""Download the four Prompt 12 reference PDFs into docs/references/ (about 70 MB; files already present are skipped).

Sources are the table at the top of docs/CODEX_PROMPTS.md. NTRS citation pages are resolved to their PDF through the
public NTRS API. Each download is checked to be a PDF before it is saved. The PDFs are gitignored.
"""
import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEADERS = {"User-Agent": "moonmesh-reference-fetch"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as response:
        return response.read(), response.headers.get("Content-Type", "")


def ntrs_pdf(citation):
    meta, _ = get(f"https://ntrs.nasa.gov/api/citations/{citation}")
    for item in json.loads(meta).get("downloads", []):
        link = item.get("links", {}).get("pdf") or item.get("links", {}).get("original")
        if link:
            return "https://ntrs.nasa.gov" + link if link.startswith("/") else link
    raise RuntimeError(f"NTRS {citation} lists no PDF download")


SOURCES = {
    "tall_lunar_towers_20230012634.pdf": lambda: ntrs_pdf("20230012634"),
    "nasa_viewshed_trade_20230014675.pdf": lambda: "https://ntrs.nasa.gov/api/citations/20230014675/downloads/"
                                                   + urllib.parse.quote("20230014675 Final Document 121423.pdf"),
    "jpl_rapid_low_cost_path.pdf": lambda: "https://www-robotics.jpl.nasa.gov/media/documents/A_Rapid_Low-Cost_Path_to_Lunar_Communication_and_Navigation.pdf",
    "nasa_nokia_surface_network_20240003815.pdf": lambda: ntrs_pdf("20240003815"),
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "references", help="target folder")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name, url_of in SOURCES.items():
        path = args.out / name
        if path.is_file() and path.read_bytes()[:5] == b"%PDF-":
            print(f"present  {name}")
            continue
        try:
            url = url_of()
            data, kind = get(url)
            if data[:5] != b"%PDF-":
                raise RuntimeError(f"{url} returned {kind}, not a PDF")
            path.write_bytes(data)
            print(f"saved    {name} ({len(data):,} bytes)")
        except Exception as error:  # report every failure, then carry on with the others
            failed += 1
            print(f"FAILED   {name}: {type(error).__name__}: {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
