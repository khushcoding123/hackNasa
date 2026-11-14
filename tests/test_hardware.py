import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("fit_tunnel_model", Path(__file__).resolve().parents[1] / "hardware" / "fit_tunnel_model.py")
fit_tunnel_model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fit_tunnel_model)


class TunnelFitTests(unittest.TestCase):
    def test_fit_recovers_known_parameters(self):
        p0, alpha, beta = -10.0, 6.0, 12.0
        rows = [(d, bend, p0 - fit_tunnel_model.fspl_db(d, 2.4) - alpha * d / 100 - beta * bend / 90)
                for d in (1, 2, 4, 8, 15) for bend in (0, 90, 180)]
        result = fit_tunnel_model.fit(rows)
        self.assertAlmostEqual(result["alpha_db_per_100m"], alpha, places=6)
        self.assertAlmostEqual(result["bend_db_per_90deg"], beta, places=6)
        self.assertLess(result["rmse_db"], 1e-6)

    def test_fit_rejects_measurements_without_bends(self):
        with self.assertRaises(ValueError):
            fit_tunnel_model.fit([(d, 0, -40 - d) for d in (1, 2, 3, 4, 5)])
        with self.assertRaises(ValueError):
            fit_tunnel_model.fit([(1, 0, -40)])


if __name__ == "__main__":
    unittest.main()
