"""Compare our viewshed with the Tall Lunar Towers paper (ASCEND 2023) at Connecting Ridge.

The paper (quoting its Ref. 4) reports, for a user 2 m tall and a region of 20 km radius
(1,256 km2) around the ridge centre: a single 10 m tower covers 14.7% and a 50 m tower 25.9%
(185 and 325 km2 in the earlier study), and an optimally placed tower roughly 40%.
Our numbers come from the 20 m LOLA window, which may not match the paper's site definition,
terrain product or centre point, so a difference is reported, not hidden.
"""
import numpy as np

from .data import terrain
from .los import viewshed_fraction

PAPER = {"radius_km": 20.0, "user_height_m": 2.0, "tower_10m_share": 0.147, "tower_50m_share": 0.259,
         "tower_10m_km2": 185.0, "tower_50m_km2": 325.0, "optimal_share_approx": 0.40}


def reproduce(site="connecting-ridge", scan_radius_m=8_000):
    """Viewshed of 10 m and 50 m towers over the paper's 20 km radius on the 240 m LOLA window.

    The 20 m window is only 20 km from its centre, which would clip the disc, so the 240 m
    product is used. Reports the window centre, the best and median cell within ``scan_radius_m``,
    and the 50 m result at the cell whose 10 m share is closest to the paper's 14.7%.
    """
    grid = terrain(site, "far")
    dem, px = grid.heights, grid.pixel_size_m
    cx, cy = dem.shape[1] // 2, dem.shape[0] // 2
    reach = int(scan_radius_m / px)
    ys, xs = np.mgrid[-reach:reach + 1:2, -reach:reach + 1:2]
    cells = np.column_stack([xs.ravel() + cx, ys.ravel() + cy]).astype(float)
    cells = cells[np.hypot(cells[:, 0] - cx, cells[:, 1] - cy) * px <= scan_radius_m]
    radius, user = PAPER["radius_km"] * 1000, PAPER["user_height_m"]
    centre = np.array([[cx, cy]], float)
    shares = {h: viewshed_fraction(dem, px, cells, h, user, radius, step_m=px)[0] for h in (10.0, 50.0)}
    centre_share = {h: float(viewshed_fraction(dem, px, centre, h, user, radius, step_m=px)[0][0]) for h in (10.0, 50.0)}
    match = int(np.argmin(np.abs(shares[10.0] - PAPER["tower_10m_share"])))
    return dict(site=site, pixel_m=px, scanned_cells=len(cells), paper=PAPER,
                ours=dict(window_centre_10m=centre_share[10.0], window_centre_50m=centre_share[50.0],
                          best_10m=float(shares[10.0].max()), best_50m=float(shares[50.0].max()),
                          median_10m=float(np.median(shares[10.0])), median_50m=float(np.median(shares[50.0])),
                          cell_matching_paper_10m=dict(share_10m=float(shares[10.0][match]), share_50m=float(shares[50.0][match]))))
