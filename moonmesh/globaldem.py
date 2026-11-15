"""Global LOLA elevation (LDEM_64, 64 pixels per degree, about 474 m at the equator), sampled in a local projection.

The file is read-only and optional: without it only the polar caps (south of 75 S) can be analysed."""
import numpy as np
from scipy.ndimage import map_coordinates

from lunar_core.terrain import MOON_RADIUS_M, polar_stereographic_south_inverse
from .data import DATA

PATH = DATA / "lola64" / "ldem_64.img"
ROWS, COLS, PER_DEG = 11520, 23040, 64
PIXEL_M = MOON_RADIUS_M * np.pi / 180 / PER_DEG   # ground size of one pixel along a meridian


def available() -> bool:
    return PATH.is_file() and PATH.stat().st_size == ROWS * COLS * 2


def sample(lat, lon):
    """Bilinear elevation (m above the 1,737,400 m sphere) at arbitrary points; shape preserved."""
    if not available():
        raise FileNotFoundError("Global LOLA terrain (ldem_64.img) is missing. See README: python -m moonmesh.globaldem.")
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    image = np.memmap(PATH, dtype="<i2", mode="r", shape=(ROWS, COLS))
    row = np.clip((90 - lat) * PER_DEG - 0.5, 0, ROWS - 1)
    r0, r1 = int(np.floor(row.min())), int(np.ceil(row.max())) + 2
    centre = float(np.angle(np.exp(1j * np.radians(lon)).mean(), deg=True)) % 360
    rel = ((lon - centre + 180) % 360 - 180) * PER_DEG          # pixels east of the centre column
    c_lo, c_hi = int(np.floor(rel.min())) - 1, int(np.ceil(rel.max())) + 2
    rows = image[r0:min(r1, ROWS)]
    if c_hi - c_lo >= COLS - 4:                                   # the shape wraps the whole globe (polar caps)
        window = np.concatenate([rows[:, -1:], rows, rows[:, :1]], axis=1)
        col = ((lon * PER_DEG - 0.5) % COLS) + 1
    else:
        base = int(round(centre * PER_DEG - 0.5))
        window = rows[:, (np.arange(c_lo, c_hi) + base) % COLS]
        col = rel - c_lo + (centre * PER_DEG - 0.5 - base)
    window = np.asarray(window, np.float32) * 0.5
    out = map_coordinates(window, [row - r0, col], order=1, mode="nearest")
    return out.astype(np.float32)


def crop(extent_m, pixel_m):
    """Square crop in the active local projection (set with lunar_core.terrain.set_frame), centred on the frame centre."""
    n = int(np.ceil(2 * extent_m / pixel_m))
    xs = -extent_m + (np.arange(n) + 0.5) * pixel_m
    ys = extent_m - (np.arange(n) + 0.5) * pixel_m
    xx, yy = np.meshgrid(xs, ys)
    lat, lon = polar_stereographic_south_inverse(xx, yy)
    return dict(heights=sample(lat, lon), x_left=-extent_m, y_top=extent_m, pixel_size_m=float(pixel_m))


URL = "https://pds-geosciences.wustl.edu/lro/lro-l-lola-3-rdr-v1/lrolol_1xxx/data/lola_gdr/cylindrical/img/ldem_64.img"


def main():
    """Download ldem_64.img (530,841,600 bytes, about 506 MB) from NASA's PDS Geosciences Node."""
    from .data import download
    download(URL, PATH, size=ROWS * COLS * 2)


if __name__ == "__main__":
    main()
