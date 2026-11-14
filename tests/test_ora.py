import unittest
from dataclasses import replace

import networkx as nx
import numpy as np

from moonmesh.data import DATA
from ora import breadcrumb, resilience
from ora.graph import build_los_graph, candidate_sites, degree_matched_disk, unit_disk_graph
from ora.links import PowerBeam, beam_budget, divergence_rad, fspl_db, optical_margin_db
from ora.los import los_clear, viewshed_fraction, visible_cells
from ora.optimize import connect, place, survivability
from ora.thermal import NodeDesign, night_budget, simulate, step_temp, survival_energy_wh
from ora.tunnel import Mine, TunnelRadio, drift_mine, margin_db, radio_graph, radio_paths, room_and_pillar

HAS_TERRAIN = (DATA / "terrain" / "shackleton_near20km.npz").exists() and (DATA / "kernels" / "de440s.bsp").exists()


def rough(size=160, seed=3):
    """Synthetic ridged terrain: smooth hills a few hundred metres high."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[:size, :size]
    dem = np.zeros((size, size))
    for _ in range(25):
        cx, cy, width, height = rng.uniform(0, size), rng.uniform(0, size), rng.uniform(6, 18), rng.uniform(80, 400)
        dem += height * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * width ** 2))
    return dem


class LineOfSightTests(unittest.TestCase):
    def test_flat_terrain_gives_complete_graph(self):
        sites = np.array([[20, 100], [60, 100], [140, 100], [180, 100], [100, 20]], float)
        G = build_los_graph(np.zeros((200, 200)), 20, sites, 3.0, 5000)
        self.assertEqual(G.number_of_edges(), 10)

    def test_wall_removes_only_crossing_links(self):
        dem = np.zeros((200, 200))
        dem[:, 100] = 50
        sites = np.array([[20, 100], [60, 100], [140, 100], [180, 100]], float)
        G = build_los_graph(dem, 20, sites, 3.0, 5000)
        self.assertEqual(sorted(G.edges), [(0, 1), (2, 3)])

    def test_curvature_hides_low_masts(self):
        smooth = np.zeros((10, 1100))
        self.assertFalse(los_clear(smooth, 20, 5, 5, 1005, 5, 2, 2))  # 20 km apart, 2 m masts
        self.assertTrue(los_clear(smooth, 20, 5, 5, 1005, 5, 100, 100))
        # Horizon distance for height h is sqrt(2Rh): two 2 m masts see each other to about 5.3 km.
        self.assertTrue(los_clear(smooth, 20, 5, 5, 255, 5, 2, 2))
        self.assertFalse(los_clear(smooth, 20, 5, 5, 305, 5, 2, 2))

    def test_missing_terrain_is_not_clear(self):
        dem = np.zeros((50, 50))
        dem[25, 20:30] = np.nan
        self.assertFalse(los_clear(dem, 20, 5, 25, 45, 25, 3, 3))

    def test_viewshed_matches_exact_rays(self):
        dem = rough()
        fraction, area = viewshed_fraction(dem, 20, [(80, 80)], 10, 2, radius_m=1200, azimuths=180, step_m=20)
        y, x = np.mgrid[20:141:3, 20:141:3]
        cells = np.column_stack([x.ravel(), y.ravel()]).astype(float)
        cells = cells[np.hypot(cells[:, 0] - 80, cells[:, 1] - 80) * 20 <= 1200]
        exact = visible_cells(dem, 20, (80, 80), cells, 10).mean()
        self.assertAlmostEqual(fraction[0], exact, delta=0.08)
        self.assertAlmostEqual(viewshed_fraction(np.zeros((200, 200)), 20, [(100, 100)], 10, 2, 1500)[0][0], 1.0, places=6)
        self.assertGreater(area[0], 0)

    def test_terrain_graph_is_sparser_and_more_broken_than_unit_disk(self):
        # The qualitative TerrainLOS result: terrain lowers degree and disconnects the graph.
        dem = rough()
        sites = candidate_sites(dem, 20, 60, slope_limit=60, min_spacing_m=150, seed=1)
        terrain, disk = build_los_graph(dem, 20, sites, 3.0, 1500), unit_disk_graph(sites, 20, 1500)
        self.assertLess(terrain.number_of_edges(), disk.number_of_edges())
        self.assertGreaterEqual(nx.number_connected_components(terrain), nx.number_connected_components(disk))
        self.assertTrue(set(terrain.edges) <= {tuple(sorted(e)) for e in disk.edges})
        matched, _ = degree_matched_disk(sites, 20, terrain.number_of_edges())
        self.assertAlmostEqual(matched.number_of_edges(), terrain.number_of_edges(), delta=3)

    def test_candidates_are_seeded_and_spaced(self):
        dem = rough()
        a, b = candidate_sites(dem, 20, 40, slope_limit=60, seed=5), candidate_sites(dem, 20, 40, slope_limit=60, seed=5)
        np.testing.assert_array_equal(a, b)
        gaps = [np.hypot(*(a[i] - a[j])) * 20 for i in range(len(a)) for j in range(i)]
        self.assertGreaterEqual(min(gaps), 300)


class ResilienceTests(unittest.TestCase):
    def test_curve_matches_brute_force(self):
        G = nx.gnm_random_graph(40, 70, seed=2)
        demand, gateways = [0, 1, 2, 3], [38, 39]
        order = resilience.removal_order(G, "random", seed=4)
        giant, served = resilience.node_curve(G, order, demand, gateways)
        H = G.copy()
        for step, v in enumerate([None] + order):
            if v is not None:
                H.remove_node(v)
            expected = max((len(c) for c in nx.connected_components(H)), default=0) / 40
            self.assertAlmostEqual(giant[step], expected)
            self.assertAlmostEqual(served[step], resilience.demand_connected(H, demand, gateways))

    def test_path_and_star(self):
        path = nx.path_graph(5)
        giant, _ = resilience.node_curve(path, [2, 0, 1, 3, 4])
        self.assertEqual(giant[:2].tolist(), [1.0, 0.4])
        star = nx.star_graph(6)
        self.assertEqual(resilience.removal_order(star, "degree", recalc_every=1)[0], 0)
        self.assertEqual(resilience.removal_order(star, "articulation")[0], 0)
        self.assertEqual(resilience.structural_report(star)["articulation_points"], 1)
        self.assertEqual(resilience.structural_report(star)["bridges"], 6)

    def test_protected_nodes_fail_last_and_edges_degrade(self):
        G = nx.cycle_graph(8)
        order = resilience.removal_order(G, "random", seed=1, protect=(0, 4))
        self.assertEqual(order[-2:], [0, 4])
        giant, served = resilience.edge_curve(G, resilience.removal_order(G, "edge", seed=1), [0], [4])
        self.assertEqual((giant[0], giant[-1], served[0], served[-1]), (1.0, 1 / 8, 1.0, 0.0))
        self.assertEqual(served[1], 1.0)  # a ring survives any single cut

    def test_robustness_scores(self):
        self.assertAlmostEqual(resilience.schneider_r(np.array([1, 0.5, 0.25, 0])), 0.25)
        self.assertAlmostEqual(resilience.critical_fraction(np.array([1, 0.9, 0.4, 0])), 2 / 3)
        ring, chain = nx.cycle_graph(30), nx.path_graph(30)
        a, b = resilience.monte_carlo(ring, "random", seeds=40), resilience.monte_carlo(chain, "random", seeds=40)
        self.assertGreater(a["schneider_r"], b["schneider_r"])
        self.assertEqual(a["runs"], 40)
        self.assertEqual(len(a["giant"]), len(a["giant_ci"]))
        pos = {i: (i * 100.0, 0.0) for i in chain}
        self.assertEqual(resilience.disk_failure(chain, pos, (1000, 0), 150).number_of_nodes(), 27)
        self.assertEqual(resilience.monte_carlo(chain, "disk", seeds=5, pos=pos, disk_radius=250)["runs"], 5)

    def test_disjoint_paths_and_rewire(self):
        G = nx.cycle_graph(6)
        self.assertEqual(resilience.disjoint_paths(G, [3], [0])[3], 2)
        self.assertEqual(resilience.disjoint_paths(nx.path_graph(6), [5], [0])[5], 1)
        H = resilience.rewired_null(nx.gnm_random_graph(30, 60, seed=1), seed=2)
        self.assertEqual(sorted(d for _, d in H.degree), sorted(d for _, d in nx.gnm_random_graph(30, 60, seed=1).degree))


class PlacementTests(unittest.TestCase):
    def setUp(self):
        # Mine 0 reaches gateway 9 through relay chains 1-2 or 3-4; 5 is a useless spur.
        self.G = nx.Graph([(0, 1), (1, 2), (2, 9), (0, 3), (3, 4), (4, 9), (1, 5)])

    def test_connect_then_add_redundancy(self):
        chain, unreachable = connect(self.G, [0], [9], {1: 0.0, 2: 0.0, 3: 0.5, 4: 0.5, 5: 0.0})
        self.assertEqual((chain, unreachable), ({1, 2}, []))
        self.assertEqual(survivability(self.G, chain, [0], [9])[0], 1.0)
        self.assertEqual(survivability(self.G, chain, [0], [9])[2], 0.0)
        plan = place(self.G, [0], [9], [1, 2, 3, 4, 5], {v: 0.5 for v in range(1, 6)}, extra=2)
        self.assertEqual(set(plan["chosen"]), {1, 2, 3, 4})
        self.assertTrue(plan["feasible"])
        relays_only = [f for f in plan["chosen"]]
        self.assertTrue(all(resilience.demand_connected(self.G.subgraph([v for v in relays_only if v != lost] + [0, 9]), [0], [9]) == 1.0
                            for lost in relays_only))

    def test_unreachable_mine_is_reported(self):
        G = self.G.copy()
        G.add_node(7)
        plan = place(G, [0, 7], [9], [1, 2, 3, 4, 5], {}, extra=0)
        self.assertEqual(plan["unreachable"], [7])
        self.assertFalse(plan["feasible"])


class NightAndLaserTests(unittest.TestCase):
    def test_survival_energy(self):
        self.assertAlmostEqual(survival_energy_wh(2.0), 708.0)  # 2 W for a 354 h night

    def test_box_cools_toward_its_surroundings(self):
        T = 300.0
        for _ in range(2000):
            T = step_temp(T, 0.0, 0.0, 300, 3000, 0.03, 0.15, 0.004, 100.0)
        self.assertLess(T, 150)
        self.assertGreater(T, 100)

    def test_hibernation_cuts_the_night_budget(self):
        awake = night_budget(NodeDesign())
        asleep = night_budget(replace(NodeDesign(), hibernate=True, duty=0.1))
        self.assertFalse(awake["survives"])
        self.assertLess(asleep["energy_wh"], awake["energy_wh"] / 2)
        self.assertTrue(asleep["survives"])
        self.assertTrue(asleep["holds_temperature"])

    def test_simulation_freezes_without_power_and_survives_with_it(self):
        dark = simulate(NodeDesign(), np.zeros(400), 1.0)
        self.assertGreaterEqual(dark["frozen_step"], 0)
        self.assertEqual(dark["mode"][-1], 0)
        sunny = simulate(NodeDesign(), np.ones(400), 1.0)
        self.assertEqual(sunny["frozen_step"], -1)
        self.assertLess(sunny["temp_k"].max(), 323.15)  # the radiator keeps a sunlit box below 50 C
        self.assertTrue((sunny["mode"] == 2).all())
        fed = simulate(NodeDesign(), np.zeros(400), 1.0, beam_w=20.0)
        self.assertEqual(fed["frozen_step"], -1)

    def test_beam_spot_matches_the_worked_example(self):
        # 1064 nm, 0.1 m aperture, 5 km: about 13 microradians and a 6.5 cm spot.
        self.assertAlmostEqual(divergence_rad(1064e-9, 0.1), 1.298e-5, delta=1e-8)
        budget = beam_budget(5000, PowerBeam())
        self.assertAlmostEqual(budget["spot_radius_m"], 0.0649, places=4)
        self.assertEqual(budget["capture_fraction"], 1.0)
        self.assertAlmostEqual(budget["end_to_end_efficiency"], 0.45 * 0.9 * 0.9 * 0.45)
        self.assertLess(beam_budget(50_000, PowerBeam())["delivered_w"], budget["delivered_w"])

    def test_link_formulas(self):
        self.assertAlmostEqual(float(fspl_db(1000, 2.4)), 100.05, delta=0.05)
        self.assertLess(float(optical_margin_db(5000, dust_loss_db=20)), float(optical_margin_db(5000)))


class TunnelTests(unittest.TestCase):
    def l_mine(self):
        mine = Mine()
        for name, xyz in {"portal": (0, 0, 0), "a": (100, 0, 0), "b": (100, 100, 0)}.items():
            mine.junction(name, *xyz)
        mine.dig("portal", "a")
        mine.dig("a", "b")
        return mine

    def test_bends_cost_margin_but_not_at_a_junction_radio(self):
        self.assertGreater(margin_db(100, 0), margin_db(200, 0))
        self.assertAlmostEqual(float(margin_db(100, 0) - margin_db(100, 90)), TunnelRadio().bend_db_per_90deg)
        mine = self.l_mine()
        radios = {"g": (("portal", "a"), 0.0), "corner": (("a", "b"), 0.0), "far": (("a", "b"), 1.0)}
        paths = radio_paths(mine, radios, "g")
        self.assertAlmostEqual(paths["corner"][1], 0.0)  # the corner radio looks straight down the first tunnel
        self.assertAlmostEqual(paths["far"][1], 90.0)
        self.assertAlmostEqual(paths["far"][0], 200.0)
        self.assertAlmostEqual(radio_paths(mine, radios, "corner")["far"][1], 0.0)

    def test_collapse_needs_a_loop(self):
        mine = self.l_mine()
        radios = {"gateway": (("portal", "a"), 0.0), "R1": (("a", "b"), 0.0)}
        self.assertEqual(breadcrumb.coverage(mine, radios), 1.0)
        self.assertLess(breadcrumb.coverage(mine, radios, blocked=[("portal", "a")], dead=["gateway"]), 1.0)
        ring = Mine()
        for name, xyz in {"portal": (0, 0, 0), "a": (60, 0, 0), "b": (60, 60, 0), "c": (0, 60, 0)}.items():
            ring.junction(name, *xyz)
        for a, b in (("portal", "a"), ("a", "b"), ("b", "c"), ("c", "portal")):
            ring.dig(a, b)
        radios = {"gateway": (("portal", "a"), 0.0), "R1": (("a", "b"), 0.0), "R2": (("b", "c"), 0.0), "R3": (("c", "portal"), 0.0)}
        self.assertEqual(breadcrumb.coverage(ring, radios, blocked=[("a", "b")], dead=["R1"]), 1.0)
        self.assertEqual(nx.number_connected_components(radio_graph(ring, radios)), 1)

    def test_drop_rule(self):
        self.assertTrue(breadcrumb.should_drop(7.0))
        self.assertTrue(breadcrumb.should_drop(9.5))  # inside the hysteresis band
        self.assertFalse(breadcrumb.should_drop(12.0))
        self.assertFalse(breadcrumb.should_drop(7.0, dropped_recently=True))

    def test_dig_keeps_the_robot_linked_and_resilient_policy_survives_a_failure(self):
        mine = drift_mine(seed=0)
        self.assertEqual(len(mine.sequence()), mine.G.number_of_edges())
        results = breadcrumb.compare(mine)
        for name, result in results.items():
            with self.subTest(policy=name):
                self.assertEqual(result["connected_fraction"], 1.0)
                self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(results["resilient"]["repeater_failure"]["worst"], 1.0)
        self.assertLess(results["threshold"]["repeater_failure"]["worst"], 1.0)
        self.assertLess(results["threshold"]["repeaters"], results["resilient"]["repeaters"])
        self.assertLess(results["resilient"]["repeaters"], results["fixed"]["repeaters"])

    def test_room_and_pillar_and_surveys(self):
        panel = room_and_pillar(3, 4)
        self.assertEqual(panel.G.number_of_nodes(), 13)
        self.assertGreater(len(nx.cycle_basis(panel.G)), 0)
        survey = Mine.from_survey({"entrance": (0, 0, 0), "s1": (30, 0, -5), "s2": (30, 40, -9)}, [("entrance", "s1"), ("s1", "s2")])
        self.assertIn("portal", survey.G)
        self.assertEqual(len(survey.sequence()), 2)


@unittest.skipUnless(HAS_TERRAIN, "ORA terrain or NAIF kernels are not prepared")
class RealTerrainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ora.scenario import build
        cls.network = build("shackleton_rim_to_floor")

    def test_scenario_links_the_crater_floor_to_the_rim(self):
        net = self.network
        self.assertTrue(net["plan"]["feasible"])
        self.assertEqual(len(net["demand"]), 3)
        self.assertTrue((net["sun_frac"][net["demand"]] == 0).all())  # the floor is permanently shadowed
        self.assertTrue((net["ground"][net["gateways"]] - net["ground"][net["demand"]].max() > 3000).all())
        self.assertTrue((net["earth_frac"][net["gateways"]] > 0.3).all())
        self.assertEqual(resilience.demand_connected(net["G"].subgraph(net["plan"]["chosen"] + net["gateways"] + net["demand"]),
                                                     net["demand"], net["gateways"]), 1.0)

    def test_twin_what_ifs(self):
        from ora import twin
        net = self.network
        base = twin.run(net)
        self.assertEqual(len(base["served_gateway"]), len(net["times"]))
        beamed = twin.run(net, beam=True)
        self.assertGreaterEqual(beamed["summary"]["gateway_availability"], base["summary"]["gateway_availability"])
        killed = twin.run(net, kill=[(g, 0.0) for g in net["gateways"]])
        self.assertEqual(killed["summary"]["gateway_availability"], 0.0)
        night = twin.run(net, night=(48.0, 354.0))
        self.assertLessEqual(night["summary"]["gateway_availability"], base["summary"]["gateway_availability"])


if __name__ == "__main__":
    unittest.main()
