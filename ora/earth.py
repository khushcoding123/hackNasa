"""Earth runs of the same engine: terrain-graph validation on real mountains."""
import numpy as np

from .data import earth_tile
from .graph import build_los_graph, candidate_sites, degree_matched_disk, unit_disk_graph
from .los import EARTH_R
from .resilience import monte_carlo, structural_report


def validate(tile="N47W122", window_km=30.0, candidates=300, max_range_km=8.0, mast_m=30.0, seed=0, seeds=50):
    """Terrain graph against unit-disk graphs on an Earth tile: degree, disconnection and robustness."""
    dem, *_ = earth_tile(tile)
    px = 30.0
    half = int(window_km * 1000 / px / 2)
    r0, c0 = dem.shape[0] // 2 - half, dem.shape[1] // 2 - half
    window = dem[r0:r0 + 2 * half, c0:c0 + 2 * half]
    sites = candidate_sites(window, px, candidates, slope_limit=35, seed=seed)
    terrain = build_los_graph(window, px, sites, mast_m, max_range_km * 1000, R=EARTH_R)
    disk = unit_disk_graph(sites, px, max_range_km * 1000)
    matched, matched_range = degree_matched_disk(sites, px, terrain.number_of_edges())
    out = {}
    for label, G in (("terrain", terrain), ("unit_disk_same_range", disk), ("unit_disk_same_degree", matched)):
        report = structural_report(G, connectivity=False)
        out[label] = dict(mean_degree=report["mean_degree"], components=report["components"],
                          giant_fraction=report["giant_fraction"], articulation_points=report["articulation_points"],
                          isolated=int(sum(1 for _, d in G.degree if d == 0)),
                          schneider_r_random=monte_carlo(G, "random", seeds)["schneider_r"],
                          schneider_r_targeted=monte_carlo(G, "betweenness")["schneider_r"])
    return dict(tile=tile, sites=len(sites), window_km=window_km, range_km=max_range_km, mast_m=mast_m,
                relief_m=float(np.nanmax(window) - np.nanmin(window)), matched_range_km=matched_range / 1000, graphs=out,
                terrain_lowers_degree=out["terrain"]["mean_degree"] < out["unit_disk_same_range"]["mean_degree"],
                terrain_disconnects_more=out["terrain"]["components"] >= out["unit_disk_same_range"]["components"])
