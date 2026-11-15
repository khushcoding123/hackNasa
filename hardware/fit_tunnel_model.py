"""Fit the ORA tunnel loss model to bench measurements.

Input: a CSV with one row per measurement position and the columns

    distance_m,bend_deg,rssi_dbm

where distance_m is the length along the tunnel between the two radios, bend_deg is the total
bend angle along that path, and rssi_dbm is the mean received signal strength there.

Model (ora/tunnel.py):  rssi = P0 - FSPL(distance) - alpha * distance / 100 - beta * bend / 90

Run:  python hardware/fit_tunnel_model.py measurements.csv [frequency_ghz]

Prints alpha (dB per 100 m), beta (dB per 90 degree bend), the fit error, and the values to
pass to the dig twin. A bench model at 2.4 GHz validates the method, not lunar numbers.
"""
import csv
import math
import sys


def fspl_db(distance_m, ghz):
    return 20 * math.log10(4 * math.pi * max(distance_m, 1e-6) / (0.299792458 / ghz))


def solve3(a, b):
    """Solve a 3x3 linear system by Gaussian elimination with partial pivoting."""
    m = [row[:] + [value] for row, value in zip(a, b)]
    for i in range(3):
        pivot = max(range(i, 3), key=lambda r: abs(m[r][i]))
        if abs(m[pivot][i]) < 1e-12:
            raise ValueError("Measurements do not vary enough: use several distances and at least two bend angles.")
        m[i], m[pivot] = m[pivot], m[i]
        for r in range(i + 1, 3):
            factor = m[r][i] / m[i][i]
            m[r] = [x - factor * y for x, y in zip(m[r], m[i])]
    x = [0.0] * 3
    for i in (2, 1, 0):
        x[i] = (m[i][3] - sum(m[i][j] * x[j] for j in range(i + 1, 3))) / m[i][i]
    return x


def fit(rows, ghz=2.4):
    """Least squares for (P0, alpha, beta). rows: iterable of (distance_m, bend_deg, rssi_dbm)."""
    rows = list(rows)
    if len(rows) < 4:
        raise ValueError("Need at least four measurements.")
    # rssi + FSPL = P0 - alpha * d/100 - beta * bend/90
    design = [(1.0, -d / 100, -bend / 90) for d, bend, _ in rows]
    target = [rssi + fspl_db(d, ghz) for d, _, rssi in rows]
    normal = [[sum(r[i] * r[j] for r in design) for j in range(3)] for i in range(3)]
    rhs = [sum(r[i] * t for r, t in zip(design, target)) for i in range(3)]
    p0, alpha, beta = solve3(normal, rhs)
    residual = [t - (p0 * r[0] + alpha * r[1] + beta * r[2]) for r, t in zip(design, target)]
    rmse = math.sqrt(sum(e * e for e in residual) / len(residual))
    return dict(p0_dbm=p0, alpha_db_per_100m=alpha, bend_db_per_90deg=beta, rmse_db=rmse, samples=len(rows))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    ghz = float(argv[2]) if len(argv) > 2 else 2.4
    with open(argv[1], newline="") as file:
        rows = [(float(r["distance_m"]), float(r["bend_deg"]), float(r["rssi_dbm"])) for r in csv.DictReader(file)]
    result = fit(rows, ghz)
    for key, value in result.items():
        print(f"{key}: {value:.3f}" if isinstance(value, float) else f"{key}: {value}")
    print(f"\nUse in the dig twin: alpha_db_per_100m={result['alpha_db_per_100m']:.1f}, bend_db_per_90deg={result['bend_db_per_90deg']:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
