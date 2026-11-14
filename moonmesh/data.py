"""Download the NASA source products and prepare local terrain windows."""
from pathlib import Path
from urllib.request import urlopen
import shutil

import numpy as np

from lunar_core.kernels import KERNELS
from lunar_core.terrain import polar_stereographic_south

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".moonmesh-data"
LOLA = "https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/"
PRODUCTS = {"LDEM_85S_20M": (15168, 20), "LDEM_75S_240M": (3812, 240)}
SITES = {
    "haworth": ("Haworth", -86.8, 337.2),
    "nobile-rim-1": ("Nobile Rim 1", -85.4, 37.3),
    "malapert": ("Malapert Massif", -85.964, 357.681),
}


def download(url, path, size=None):
    if path.is_file() and (size is None or path.stat().st_size == size):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    print(f"Downloading {path.name}", flush=True)
    with urlopen(url, timeout=90) as source, partial.open("wb") as target:
        shutil.copyfileobj(source, target)
    if size and partial.stat().st_size != size:
        raise ValueError(f"{path.name} has an unexpected byte count.")
    partial.replace(path)


def crop(site, product, radius):
    path = DATA / "terrain" / f"{product}.IMG"
    dimension, pixel = PRODUCTS[product]
    image = np.memmap(path, dtype="<i2", mode="r", shape=(dimension, dimension))
    x, y = polar_stereographic_south(*site[1:])
    edge = dimension * pixel / 2
    c0, r0 = np.floor([(x + edge - radius)/pixel, (edge - y - radius)/pixel]).astype(int).clip(0, dimension)
    c1, r1 = np.ceil([(x + edge + radius)/pixel, (edge - y + radius)/pixel]).astype(int).clip(0, dimension)
    raw = image[r0:r1, c0:c1]
    heights = np.where((raw == -32768) | (raw == -32767), np.nan, raw * 0.5).astype(np.float32)
    return dict(heights=heights, x_left=-edge + c0*pixel, y_top=edge-r0*pixel, pixel_size_m=pixel)


def main():
    for name, url in KERNELS.items():
        download(url, DATA / "kernels" / name)
    for product, (dimension, _) in PRODUCTS.items():
        download(LOLA + product + ".LBL", DATA / "terrain" / f"{product}.LBL")
        download(LOLA + product + ".IMG", DATA / "terrain" / f"{product}.IMG", dimension**2 * 2)
    for key, site in SITES.items():
        for suffix, product, extent in (("near20km", "LDEM_85S_20M", 20_000), ("far", "LDEM_75S_240M", 180_000)):
            path = DATA / "terrain" / f"{key}_{suffix}.npz"
            with path.with_suffix(".partial").open("wb") as file:
                np.savez_compressed(file, **crop(site, product, extent))
            path.with_suffix(".partial").replace(path)
        print(f"Prepared {site[0]}", flush=True)


if __name__ == "__main__":
    main()
