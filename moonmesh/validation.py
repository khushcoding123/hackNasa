"""Compare MOONMESH Sun and Earth geometry with saved JPL Horizons observer ephemerides.

Horizons reports apparent angles and light-time-corrected ranges from DE441, while
MOONMESH computes geometric DE440 positions. Two differences are therefore expected:
about 0.006° in Sun azimuth from stellar aberration, and about 36 km (1e-4) in Earth
range because light-time correction includes Earth's ~30 km/s orbital motion.

Run ``python -m moonmesh.validation`` to download any missing references into
tests/data/horizons and print the comparison. Add ``--refresh`` to download them again.
"""
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import numpy as np

from lunar_core.geometry import Body, SurfacePoint, horizontal_arrays
from lunar_core.kernels import load_kernels

from .data import DATA, LOCAL, ROOT, SITES

API = "https://ssd.jpl.nasa.gov/api/horizons.api"
REFERENCES = ROOT / "tests" / "data" / "horizons"
TARGETS = {Body.EARTH: "399", Body.SUN: "10"}
PERIODS = (("2026-11-01", "2026-12-01"), ("2027-03-01", "2027-03-31"))
STEP = "6 h"
QUANTITIES = ("azimuth", "elevation", "range")
# Largest accepted absolute difference: azimuth (deg), elevation (deg), relative range.
TOLERANCES = {Body.EARTH: (0.001, 0.001, 2e-4), Body.SUN: (0.01, 0.002, 1e-6)}


@dataclass(frozen=True)
class Reference:
    name: str
    body: Body
    lat: float
    lon: float
    height_m: float
    times: tuple
    azimuth: np.ndarray
    elevation: np.ndarray
    range_km: np.ndarray
    version: str


def fetch(site, lat, lon, body, start, stop, directory=REFERENCES, refresh=False):
    """Download one Horizons observer table for a lunar surface site, keeping the raw request and response."""
    path = directory / f"{site}_{body.lower()}_{start}.json"
    if path.exists() and not refresh:
        return path
    params = dict(format="json", COMMAND=f"'{TARGETS[body]}'", OBJ_DATA="'NO'", MAKE_EPHEM="'YES'",
                  EPHEM_TYPE="'OBSERVER'", CENTER="'coord@301'", COORD_TYPE="'GEODETIC'",
                  SITE_COORD=f"'{lon},{lat},0'", START_TIME=f"'{start}'", STOP_TIME=f"'{stop}'",
                  STEP_SIZE=f"'{STEP}'", QUANTITIES="'4,20'", RANGE_UNITS="'KM'", CSV_FORMAT="'YES'",
                  EXTRA_PREC="'YES'", TIME_DIGITS="'MINUTES'")
    url = API + "?" + urlencode(params, quote_via=quote)
    print(f"Downloading {path.name}", flush=True)
    with urlopen(Request(url, headers={"User-Agent": "moonmesh-validation"}), timeout=90) as response:
        payload = json.load(response)
    if "error" in payload:
        raise RuntimeError(f"Horizons rejected {path.name}: {payload['error']}")
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(request=url, response=payload), indent=1))
    time.sleep(1)
    return path


def load(path):
    """Parse a saved Horizons response. Site coordinates come from the response itself."""
    payload = json.loads(Path(path).read_text())["response"]
    header, table = payload["result"].split("$$SOE")
    lines = header.splitlines()
    field = lambda prefix: next(line for line in lines if line.startswith(prefix)).split(":", 1)[1]
    target = field("Target body name")
    body = next(body for body, code in TARGETS.items() if f"({code})" in target)
    lon, lat, alt_km = (float(value) for value in field("Center geodetic").split("{")[0].split(","))
    columns = [cell.strip() for cell in next(line for line in reversed(lines) if line.strip().startswith("Date__")).split(",")]
    index = [next(i for i, name in enumerate(columns) if name.startswith(key)) for key in ("Azimuth", "Elevation", "delta")]
    rows = [[cell.strip() for cell in row.split(",")] for row in table.split("$$EOE")[0].strip().splitlines()]
    values = [np.array([float(row[i]) for row in rows]) for i in index]
    times = tuple(datetime.strptime(row[0], "%Y-%b-%d %H:%M").replace(tzinfo=UTC) for row in rows)
    return Reference(Path(path).stem, body, lat, lon, alt_km * 1000, times, *values, payload["signature"]["version"])


def compare(reference):
    """Largest absolute azimuth and elevation difference (deg) and relative range difference."""
    point = SurfacePoint(reference.lat, reference.lon, reference.height_m)
    azimuth, elevation, distance = horizontal_arrays(reference.body, point, reference.times)
    return dict(azimuth=float(np.abs((azimuth - reference.azimuth + 180) % 360 - 180).max()),
                elevation=float(np.abs(elevation - reference.elevation).max()),
                range=float(np.abs(distance / reference.range_km - 1).max()))


def main(argv=None):
    refresh = "--refresh" in (sys.argv[1:] if argv is None else argv)
    for site, (_, lat, lon) in [i for i in SITES.items() if i[0] not in LOCAL]:
        for body in TARGETS:
            for start, stop in PERIODS:
                fetch(site, lat, lon, body, start, stop, refresh=refresh)
    load_kernels(DATA / "kernels")
    print(f"{'reference':34} {'samples':>7} {'az (deg)':>9} {'el (deg)':>9} {'range':>8}")
    failed = False
    for path in sorted(REFERENCES.glob("*.json")):
        reference = load(path)
        gaps = compare(reference)
        ok = all(gaps[key] < limit for key, limit in zip(QUANTITIES, TOLERANCES[reference.body]))
        failed |= not ok
        print(f"{reference.name:34} {len(reference.times):>7} {gaps['azimuth']:>9.4f} {gaps['elevation']:>9.4f} "
              f"{gaps['range']:>8.1e}{'' if ok else '  OUTSIDE TOLERANCE'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
