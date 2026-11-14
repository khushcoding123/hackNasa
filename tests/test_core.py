"""The 13 core checks from ORA_Agent_Build_Spec.md section 7.

Assertions are the spec's. They are wrapped in a TestCase so the project's single command,
``python -m unittest discover -s tests``, runs them; pytest collects them too. The targeted-attack
test uses 120 sites instead of 200 to keep the pure-Python betweenness recalculation quick.
"""
import unittest

import networkx as nx
import numpy as np

from ora.blast import G_EARTH, G_MOON, ballistic_range, blast_effects
from ora.breadcrumb import should_drop
from ora.configure import SiteFeatures, configure
from ora.dust import node_hazard, survival_prob, update_dust
from ora.earth_vis import earth_elevation_deg
from ora.graph import build_los_graph
from ora.los import MOON_R, los_clear, radio_horizon_m
from ora.resilience import (attack_curve, disk_failure, giant_frac, pick_betweenness, pick_random, rewired_null, schneider_R,
                            structural_report)
from ora.thermal import step_temp
from ora.topologies import make, p_connected

PX = 50.0


def flat(n=400):
    return np.zeros((n, n), dtype=np.float64)


class CoreSpecTests(unittest.TestCase):
    def test_los_flat_within_horizon(self):
        self.assertTrue(los_clear(flat(), PX, 10., 10., 90., 10., 3., 3.))  # 4 km apart, 3 m masts

    def test_los_curvature_blocks_far_flat(self):
        self.assertFalse(los_clear(flat(), PX, 10., 10., 300., 10., 3., 3.))  # 14.5 km: beyond radio horizon

    def test_los_wall_blocks(self):
        d = flat()
        d[:, 148:152] = 200.0
        self.assertFalse(los_clear(d, PX, 100., 10., 200., 10., 3., 3.))
        self.assertTrue(los_clear(d, PX, 10., 10., 100., 10., 3., 3.))

    def test_graph_wall_has_no_crossing_edges(self):
        rng = np.random.default_rng(0)
        d = flat()
        d[:, 148:152] = 200.0
        sites = rng.uniform(5, 395, size=(300, 2))
        G = build_los_graph(d, PX, sites, max_range_m=6000)
        self.assertGreater(G.number_of_edges(), 0)
        self.assertTrue(all((sites[i, 0] < 150) == (sites[j, 0] < 150) for i, j in G.edges))

    def test_resilience_targeted_worse_than_random(self):
        rng = np.random.default_rng(1)
        sites = rng.uniform(5, 395, size=(120, 2))
        G = build_los_graph(flat(), PX, sites, max_range_m=6000)
        r = schneider_R(attack_curve(G, pick_random, seed=1))
        t = schneider_R(attack_curve(G, pick_betweenness, seed=1))
        self.assertLess(t, r)
        self.assertEqual(rewired_null(G).number_of_edges(), G.number_of_edges())
        self.assertEqual(giant_frac(G, G.number_of_nodes()), structural_report(G, connectivity=False)["giant_fraction"])

    def test_disk_failure(self):
        G = nx.path_graph(5)
        pos = {i: (i * 10.0, 0.0) for i in G}
        self.assertEqual(set(disk_failure(G, pos, (0, 0), 15).nodes), {2, 3, 4})

    def test_topologies(self):
        chain = make()["chain"][0]
        self.assertEqual((len(list(nx.articulation_points(chain))), len(list(nx.bridges(chain)))), (18, 19))
        G, a, b = make()["ring"]
        self.assertEqual(len(list(nx.articulation_points(G))), 0)
        G, a, b = make()["chain+skip"]
        self.assertGreater(p_connected(G, a, b, 0.1, trials=3000), p_connected(*make()["chain"], 0.1, trials=3000))

    def test_earth_visibility(self):
        self.assertGreater(earth_elevation_deg(0, 0, 0, 0), 89.9)
        self.assertTrue(29.9 < earth_elevation_deg(-60, 0, 0, 0) < 30.1)
        self.assertLess(earth_elevation_deg(0, 180, 0, 0), 0)  # far side

    def test_dust_and_hazard(self):
        self.assertEqual(update_dust(0.9, 1.0, 0.5), 1.0)
        self.assertEqual(update_dust(0.1, 1.0, 0.0, cleaning_rate=0.5), 0.0)
        self.assertGreater(node_hazard(1e-4, 0.5), node_hazard(1e-4, 0.0))
        self.assertTrue(0 < survival_prob(1e-3, 100) < 1)

    def test_configurator_acceptance(self):
        dark = configure(SiteFeatures(0.1, 0.2, 25, 0.8, 0.7, True, "mesh")).modules
        for m in ("power:laser_receiver", "anchor:rock_bolt", "dust:shield", "shock:isolated_mount"):
            self.assertIn(m, dark)
        lit = configure(SiteFeatures(0.9, 0.8, 5, 0.1, 0.1, False, "mesh")).modules
        self.assertEqual(lit, ["core", "power:solar+battery", "thermal:insulation", "antenna:omni", "anchor:regolith_spike"])

    def test_blast_and_ejecta(self):
        self.assertLess(abs(ballistic_range(10, G_MOON) - 61.7), 0.1)
        self.assertLess(abs(G_EARTH / G_MOON - 6.06), 0.01)
        dead, hurt = blast_effects(None, {1: (0, 0), 2: (30, 0), 3: (100, 0)}, (0, 0), 10, 60)
        self.assertEqual((dead, hurt), ([1], [2]))

    def test_thermal_and_breadcrumb(self):
        T = step_temp(250.0, 0.0, 2.0, 60.0, C=500.0, eps=0.8, A=0.05, k_loss=0.05, T_env=100.0)
        self.assertTrue(0 < T < 400)
        self.assertTrue(should_drop(5.0))
        self.assertFalse(should_drop(12.0))
        self.assertFalse(should_drop(5.0, dropped_recently=True))

    def test_radio_horizon_numbers(self):
        self.assertLess(abs(np.sqrt(2 * MOON_R * 3) - 3228), 5)
        self.assertAlmostEqual(radio_horizon_m(3.0), np.sqrt(2 * MOON_R * 3))
        # Two equal masts over smooth ground: 1 m 3.7 km, 3 m 6.5 km, 10 m 11.8 km, 30 m 20.4 km.
        for height, km in ((1, 3.7), (3, 6.5), (10, 11.8), (30, 20.4)):
            self.assertAlmostEqual(2 * radio_horizon_m(height) / 1000, km, delta=0.06)


if __name__ == "__main__":
    unittest.main()
