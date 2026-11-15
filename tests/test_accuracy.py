"""Accuracy references: dense line-of-sight truth, the radial sweep the app uses, and exact relay selection."""
import numpy as np

from ora.accuracy import dense_truth, splits
from ora.features import view_rings
from ora.selector import select_sites

PX = 20.0
SIZE = 1000  # 20 km square, so an 8 km disc around the centre fits


def flat():
    return np.zeros((SIZE, SIZE), np.float32)


def test_flat_ground_is_fully_visible_by_both_methods():
    # On the Moon a 10 m mast and a 2 m receiver see each other to about 8.5 km over a smooth sphere.
    dem, cell = flat(), np.array([[SIZE / 2, SIZE / 2]])
    assert dense_truth(dem, PX, cell, rays=2000)[0] > 0.99
    assert view_rings(dem, PX, cell, (8_000,), step_px=1)[0, 0] > 0.99


def test_one_pixel_ridge_is_caught_by_fine_steps():
    """A 20 m wide, 300 m tall north-south wall 21 px east of the mast hides most of the eastern half."""
    dem = flat()
    centre = SIZE // 2
    dem[:, centre + 21] = 300.0
    cell = np.array([[centre, centre]], float)
    truth = dense_truth(dem, PX, cell, rays=6000)[0]
    fine = view_rings(dem, PX, cell, (8_000,), step_px=1)[0, 0]
    coarse = view_rings(dem, PX, cell, (8_000,), step_px=2)[0, 0]
    assert 0.45 < truth < 0.6
    assert abs(fine - truth) < 0.03
    assert coarse - truth > abs(fine - truth)  # 40 m steps skip the wall on some rays and read high


def test_coverage_selection_is_exact_greedy_over_every_candidate():
    rng = np.random.default_rng(0)
    dem = np.asarray(rng.normal(0, 20, (SIZE, SIZE)), np.float32)
    cells = np.column_stack([rng.uniform(450, 550, 12), rng.uniform(450, 550, 12)])
    out = select_sites(dem, PX, cells, 3, targets=200, range_m=4_000)
    assert out["method"] == "exact greedy over all candidates" and out["shortlist"] == len(cells)
    assert len(set(out["chosen"])) == 3 and out["coverage"] >= out["random_coverage"]


def test_holdout_is_buffered_from_development_windows():
    sp = splits()
    dev, hold, legacy = set(sp["dev"]), set(sp["hold"]), set(sp["legacy"])
    assert len(hold) == 8 and not (dev & hold) and not (hold & legacy) and not (dev & legacy)
    step = 20_000
    for x, y in hold:  # no development window touches a holdout window, diagonals included
        assert not any((x + dx * step, y + dy * step) in dev for dx in (-1, 0, 1) for dy in (-1, 0, 1))
