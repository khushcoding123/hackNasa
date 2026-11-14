import unittest

from lunar_core.geometry import Body
from lunar_core.kernels import load_kernels
from moonmesh.data import DATA, SITES
from moonmesh.validation import QUANTITIES, REFERENCES, TOLERANCES, compare, load


class HorizonsReferenceTests(unittest.TestCase):
    """Saved JPL Horizons observer tables for every site, both bodies and two mission months."""

    def test_saved_references_cover_every_site(self):
        paths = sorted(REFERENCES.glob("*.json"))
        self.assertTrue(paths, "Run python -m moonmesh.validation to download the references.")
        covered = {(path.stem.split("_")[0], load(path).body) for path in paths}
        self.assertEqual(covered, {(site, body) for site in SITES for body in (Body.EARTH, Body.SUN)})
        for path in paths:
            reference = load(path)
            with self.subTest(reference=reference.name):
                self.assertGreater(len(reference.times), 100)
                self.assertEqual(len(reference.times), len(reference.elevation))

    @unittest.skipUnless((DATA/"kernels/de440s.bsp").exists(), "NAIF kernels are not prepared")
    def test_spice_geometry_matches_horizons(self):
        # Horizons is apparent DE441; MOONMESH is geometric DE440. The tolerances
        # allow for stellar aberration (Sun azimuth) and light time (Earth range).
        load_kernels(DATA/"kernels")
        for path in sorted(REFERENCES.glob("*.json")):
            reference = load(path)
            gaps = compare(reference)
            for quantity, limit in zip(QUANTITIES, TOLERANCES[reference.body]):
                with self.subTest(reference=reference.name, quantity=quantity):
                    self.assertLess(gaps[quantity], limit)


if __name__ == "__main__":
    unittest.main()
