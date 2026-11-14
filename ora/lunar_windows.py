"""Many lunar training and test windows from the LOLA 20 m south-polar product already on disk.

The 85 S LOLA product spans 151 km either side of the pole. A lattice of 20 km squares, 20 km apart,
covers it. Cells are sampled in the central 20 km square of each window (the crop is larger so the 8 km
viewshed sees real terrain). The lattice is split once, by a fixed seed, into sealed test windows and
training windows; every training window next to a test window (including diagonals) is dropped, so no
training cell shares a viewshed neighbourhood with a test cell. Tuning and cross-validation use training
windows only; the test windows are read once, by ``ora.evaluate``.

Per window the cache holds: sampled cells, their features, the exact 8 km viewshed share (label),
candidate and target cells, and the exact line-of-sight matrix between them for the selection study.
"""
import numpy as np

from lunar_core.terrain import TerrainGrid, polar_stereographic_south_inverse
from moonmesh.data import DATA, PRODUCTS

from .features import RX_HEIGHT_M, TX_HEIGHT_M, horizon_features, interior_cells, rasters, surrogate_features, view_rings, CHEAP, RINGS_M
from .los import los_clear_many

PRODUCT = "LDEM_85S_20M"
EXTENT_M = 18_000
SPACING_M = 20_000
MAX_RADIUS_M = 133_000
CACHE = DATA / "ora_cache" / "windows"
CELLS, CANDIDATES, TARGETS = 600, 120, 500
RANGE_M = 8_000
SPLIT_SEED = 2026
TEST_SHARE = 0.2
MIN_VALID = 0.97


def lattice():
    """All lattice centres (x, y) in metres, in a fixed order."""
    steps = np.arange(-MAX_RADIUS_M + 3_000, MAX_RADIUS_M, SPACING_M)
    return [(float(x), float(y)) for x in steps for y in steps if np.hypot(x, y) <= MAX_RADIUS_M]


def crop_xy(x, y, extent=EXTENT_M):
    dimension, pixel = PRODUCTS[PRODUCT]
    image = np.memmap(DATA / "terrain" / f"{PRODUCT}.IMG", dtype="<i2", mode="r", shape=(dimension, dimension))
    edge = dimension * pixel / 2
    c0, r0 = np.floor([(x + edge - extent) / pixel, (edge - y - extent) / pixel]).astype(int)
    c1, r1 = c0 + int(2 * extent / pixel), r0 + int(2 * extent / pixel)
    if min(c0, r0) < 0 or max(c1, r1) > dimension:
        return None
    raw = image[r0:r1, c0:c1]
    heights = np.where((raw == -32768) | (raw == -32767), np.nan, raw * 0.5).astype(np.float32)
    return TerrainGrid(heights, -edge + c0 * pixel, edge - r0 * pixel, float(pixel))


def split(blocks=4):
    """(train, test): ``blocks`` sealed 2 x 2 blocks of windows are test; the ring around each is dropped."""
    rng = np.random.default_rng(SPLIT_SEED)
    keep = []
    for x, y in lattice():
        grid = crop_xy(x, y)
        if grid is not None and np.isfinite(grid.heights).mean() >= MIN_VALID:
            keep.append((x, y))
    have = set(keep)
    s = SPACING_M
    test, banned = [], set()
    for i in rng.permutation(len(keep)):
        x, y = keep[i]
        block = [(x, y), (x + s, y), (x, y + s), (x + s, y + s)]
        ring = {(x + dx * s, y + dy * s) for dx in range(-1, 3) for dy in range(-1, 3)}
        if all(b in have for b in block) and not (ring & banned) and not (set(block) & banned):
            test += block
            banned |= ring
        if len(test) >= 4 * blocks:
            break
    train = [w for w in keep if w not in banned]
    return train, test


def key(x, y):
    return f"{int(round(x / 1000)):+04d}_{int(round(y / 1000)):+04d}"


def build(x, y, seed=0):
    """Compute (or load) one window's cached arrays."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{key(x, y)}.npz"
    if path.is_file():
        with np.load(path) as data:
            return {k: data[k] for k in data.files}
    grid = crop_xy(x, y)
    dem, px = grid.heights, grid.pixel_size_m
    rng = np.random.default_rng(abs(hash((int(x), int(y), seed))) % (2 ** 32))
    cells = interior_cells(dem, px, CELLS, rng, margin_m=RANGE_M)
    layers = rasters(dem, px)
    c, r = np.rint(cells[:, 0]).astype(int), np.rint(cells[:, 1]).astype(int)
    cheap = np.column_stack([layers[name][r, c] for name in CHEAP]).astype(np.float32)
    rings = view_rings(dem, px, cells, RINGS_M + (RANGE_M,))
    context = np.tile(np.array([layers["slope"].mean(), np.nanstd(dem), np.nanpercentile(dem, 90) - np.nanpercentile(dem, 10)], np.float32),
                      (len(cells), 1))
    out = dict(cells=cells, cheap=cheap, horizon=horizon_features(dem, px, cells), rings=rings[:, :-1], label=rings[:, -1],
               context=context)
    # selection study: candidates are a subset of the sampled cells; targets are fresh cells
    cand_idx = np.arange(min(CANDIDATES, len(cells)))
    targets = interior_cells(dem, px, TARGETS, rng, margin_m=RANGE_M)
    a = np.repeat(cells[cand_idx], len(targets), axis=0)
    b = np.tile(targets, (len(cand_idx), 1))
    near = np.hypot(*(a - b).T) * px <= RANGE_M
    clear = np.zeros(len(a), bool)
    if near.any():
        clear[near] = los_clear_many(dem, px, a[near], b[near], TX_HEIGHT_M, RX_HEIGHT_M, step_px=2.0)[0]
    out.update(cand_idx=cand_idx, targets=targets, los=clear.reshape(len(cand_idx), len(targets)))
    np.savez_compressed(path, **out)
    return out


def matrix(window):
    """Feature matrix in SURROGATE_INPUTS order for a built window (or one of its variants)."""
    mast = np.full((len(window["cheap"]), 1), float(window.get("mast", TX_HEIGHT_M)), np.float32)
    return np.hstack([window["cheap"], window["horizon"], window["rings"], mast])


def correlated_noise(shape, sigma_m, length_m, px, seed):
    """Spatially correlated height error with the given standard deviation and correlation length."""
    from scipy.ndimage import gaussian_filter
    field = gaussian_filter(np.random.default_rng(seed).normal(size=shape), length_m / px)
    return (field / field.std() * sigma_m).astype(np.float32)


def variant(x, y, kind, value, length_m=200.0, seed=0, cache=True):
    """A window's cells seen under a different condition, cached next to the base window.

    kind "noise": features come from terrain with correlated height error of ``value`` metres; the label
    stays the clean-terrain viewshed. kind "mast": features and label both use a mast of ``value`` metres.
    """
    path = CACHE / f"{key(x, y)}__{kind}{value:g}.npz"
    if cache and path.is_file():
        with np.load(path) as data:
            return {k: data[k] for k in data.files}
    base = build(x, y)
    grid = crop_xy(x, y)
    dem, px = grid.heights, grid.pixel_size_m
    if kind == "noise":
        dem = dem + correlated_noise(dem.shape, value, length_m, px, seed + int(abs(x) + abs(y)))
        X, _ = surrogate_features(dem, px, base["cells"])
        label, mast = base["label"], TX_HEIGHT_M
    else:
        X, label = surrogate_features(dem, px, base["cells"], h_tx=float(value))
        mast = float(value)
    out = dict(cells=base["cells"], cheap=X[:, :7], horizon=X[:, 7:13], rings=X[:, 13:16], label=np.asarray(label, np.float32),
               mast=np.float32(mast))
    if cache:
        np.savez_compressed(path, **out)
    return out


VARIANTS = (("noise", 2.0), ("noise", 5.0), ("mast", 3.0), ("mast", 20.0))


def _job(w):
    build(*w)
    for kind, value in VARIANTS:
        variant(*w, kind, value)
    return w


def main():
    import multiprocessing as mp
    train, test = split()
    todo = train + test
    print(f"{len(train)} training and {len(test)} sealed test windows", flush=True)
    with mp.Pool(max(1, min(6, mp.cpu_count() - 1))) as pool:
        for i, _ in enumerate(pool.imap_unordered(_job, todo), 1):
            if i % 10 == 0:
                print(f"{i}/{len(todo)} windows built", flush=True)


if __name__ == "__main__":
    main()
