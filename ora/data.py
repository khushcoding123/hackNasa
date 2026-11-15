"""Prepare ORA terrain windows and the Earth tower training data.

Run ``python -m ora.data`` after ``python -m moonmesh.data``. Add ``--earth`` to download the
FCC Antenna Structure Registration and the open SRTM-derived elevation tiles used to train
the placement prior. Everything lands in the git-ignored ``.moonmesh-data`` directory.
"""
import gzip
import io
import math
import sys
import zipfile

import numpy as np

from lunar_core.terrain import TerrainGrid
from moonmesh.data import DATA, SITES, crop, download

# Mining scenario sites inside the 85-90 S LOLA 20 m product. Coordinates are approximate
# research points, as for the MOONMESH sites.
ORA_SITES = {
    "shackleton": ("Shackleton crater (rim to floor)", -89.67, 129.78),
    "connecting-ridge": ("Connecting Ridge", -89.45, 222.7),
}
ALL_SITES = {**SITES, **ORA_SITES}

FCC_URL = "https://data.fcc.gov/download/pub/uls/complete/r_tower.zip"
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/skadi/{band}/{name}.hgt.gz"
# 1 degree tiles named by their south-west corner: mountainous, mostly low-population terrain.
EARTH_TILES = ("N47W122", "N44W122", "N35W116", "N35W118", "N39W107", "N38W108",
               "N45W114", "N46W112", "N40W111", "N37W119", "N48W114", "N36W112",
               "N44W116", "N46W116", "N42W122", "N48W120", "N38W120", "N40W109", "N33W111", "N45W118")
EARTH = DATA / "earth"
EARTH_RADIUS_M = 6_371_000


def prepare_sites():
    for key, site in ORA_SITES.items():
        for suffix, product, extent in (("near20km", "LDEM_85S_20M", 20_000), ("far", "LDEM_75S_240M", 180_000)):
            path = DATA / "terrain" / f"{key}_{suffix}.npz"
            if path.is_file():
                continue
            with path.with_suffix(".partial").open("wb") as file:
                np.savez_compressed(file, **crop(site, product, extent))
            path.with_suffix(".partial").replace(path)
        print(f"Prepared {site[0]}", flush=True)


def terrain(site, suffix="near20km"):
    path = DATA / "terrain" / f"{site}_{suffix}.npz"
    if not path.is_file():
        raise FileNotFoundError(f"Terrain for {site} is missing. Run python -m moonmesh.data, then python -m ora.data.")
    return TerrainGrid.load(path)


def download_earth(tiles=EARTH_TILES):
    download(FCC_URL, EARTH / "r_tower.zip")
    for name in tiles:
        download(TILE_URL.format(band=name[:3], name=name), EARTH / f"{name}.hgt.gz")


def earth_tile(name, pixel_m=30.0):
    """Load a 1 arc-second tile as a metric grid with roughly square ``pixel_m`` cells.

    Returns (heights, lat_north, lon_west, row_deg, col_deg): heights in metres with NaN voids,
    and the angular size of one output row and column for mapping coordinates to cells.
    """
    path = EARTH / f"{name}.hgt.gz"
    if not path.is_file():
        raise FileNotFoundError(f"{path.name} is missing. Run python -m ora.data --earth.")
    with gzip.open(path, "rb") as file:
        raw = np.frombuffer(file.read(), dtype=">i2").reshape(3601, 3601).astype(np.float32)
    raw[raw < -1000] = np.nan
    lat0 = int(name[1:3]) * (1 if name[0] == "N" else -1)
    lon0 = int(name[4:7]) * (1 if name[3] == "E" else -1)
    metres_lat = math.radians(1) * EARTH_RADIUS_M
    metres_lon = metres_lat * math.cos(math.radians(lat0 + 0.5))
    rows, cols = int(metres_lat / pixel_m), int(metres_lon / pixel_m)
    ri = np.minimum((np.arange(rows) + 0.5) * 3601 / rows, 3600).astype(int)
    ci = np.minimum((np.arange(cols) + 0.5) * 3601 / cols, 3600).astype(int)
    return raw[np.ix_(ri, ci)], lat0 + 1.0, float(lon0), 1.0 / rows, 1.0 / cols


def fcc_towers(tiles=EARTH_TILES):
    """Registered antenna structures inside the given tiles: {tile: [(lat, lon, height_m), ...]}."""
    path = EARTH / "r_tower.zip"
    if not path.is_file():
        raise FileNotFoundError("r_tower.zip is missing. Run python -m ora.data --earth.")
    wanted = {(int(n[1:3]) * (1 if n[0] == "N" else -1), int(n[4:7]) * (1 if n[3] == "E" else -1)): n for n in tiles}
    out = {name: [] for name in tiles}
    with zipfile.ZipFile(path) as archive:
        heights = {}
        with archive.open("RA.dat") as file:
            for line in io.TextIOWrapper(file, encoding="latin-1"):
                parts = line.split("|")
                try:
                    heights[parts[4]] = float(parts[30])  # overall height above ground, metres
                except (ValueError, IndexError):
                    continue
        with archive.open("CO.dat") as file:
            for line in io.TextIOWrapper(file, encoding="latin-1"):
                parts = line.split("|")
                try:
                    if parts[5] != "T":
                        continue
                    lat = (int(parts[6]) + int(parts[7]) / 60 + float(parts[8]) / 3600) * (-1 if parts[9] == "S" else 1)
                    lon = (int(parts[11]) + int(parts[12]) / 60 + float(parts[13]) / 3600) * (-1 if parts[14] == "W" else 1)
                except (ValueError, IndexError):
                    continue
                name = wanted.get((math.floor(lat), math.floor(lon)))
                if name:
                    out[name].append((lat, lon, heights.get(parts[4], float("nan"))))
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    prepare_sites()
    if "--earth" in argv:
        download_earth()
        print({name: len(rows) for name, rows in fcc_towers().items()})


if __name__ == "__main__":
    main()
