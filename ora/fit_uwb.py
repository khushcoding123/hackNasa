"""Fit the tunnel waveguide loss to the public UWB mine-tunnel dataset (Zenodo 10.5281/zenodo.18835598, CC BY 4.0).

``python -m ora.fit_uwb path/to/dataset_UWB_Measurements_3D_Model_Mine_Tunnel.zip``

Measured part: UWB_Measurements/RX_n/*.csv, one folder per distance n = 1..40 m from the anchor
in a straight 60 m tunnel, 200 repeats per file, columns include the receiver-reported RSS (dBm)
and an NLOS flag. Channel 4 is centred at 3.9936 GHz and channel 7 at 6.4896 GHz.

The transmit power and antenna gains are not in the dataset, so the absolute level is a fitted
constant P0 and only the distance dependence is taken from the data. Model, as in ora/tunnel.py:

    rss(d) = P0 - FSPL(d, f) - alpha * d / 100

The simulated part (RT_Simulations/.../PathLoss.csv, CloudRT ray tracing in a mesh of the same
tunnel) is fitted the same way and is always labelled simulated, never mixed with measurements.
"""
import io
import json
import math
import sys
import zipfile

import numpy as np

CENTRE_GHZ = {"CH4": 3.9936, "CH7": 6.4896}


def fspl_db(distance_m, ghz):
    return 20 * np.log10(4 * np.pi * np.asarray(distance_m, float) / (0.299792458 / ghz))


def fit_alpha(distance, level, ghz, sign=-1):
    """Least squares for level = c + sign * (FSPL + alpha * d / 100). Returns (alpha, c)."""
    d = np.asarray(distance, float)
    y = np.asarray(level, float) - sign * fspl_db(d, ghz)  # remove the free-space term
    A = np.column_stack([np.ones_like(d), sign * d / 100.0])
    c, alpha = np.linalg.lstsq(A, y, rcond=None)[0]
    return float(alpha), float(c)


def bootstrap(distance, level, ghz, sign, draws=500, seed=0):
    rng = np.random.default_rng(seed)
    n = len(distance)
    alphas = [fit_alpha(distance[i], level[i], ghz, sign)[0] for i in (rng.integers(0, n, n) for _ in range(draws))]
    return [float(np.percentile(alphas, 2.5)), float(np.percentile(alphas, 97.5))]


def summarise(distance, level, ghz, sign):
    distance, level = np.asarray(distance, float), np.asarray(level, float)
    alpha, c = fit_alpha(distance, level, ghz, sign)
    free = np.polyfit(np.log10(distance), level, 1)  # level = a + b log10 d: b is -10 n for received power
    predicted = c + sign * (fspl_db(distance, ghz) + alpha * distance / 100)
    return dict(points=len(distance), alpha_db_per_100m=alpha, alpha_ci95=bootstrap(distance, level, ghz, sign),
                offset_db=c, rms_residual_db=float(np.sqrt(np.mean((level - predicted) ** 2))),
                free_space_alpha_zero_rms_db=float(np.sqrt(np.mean((level - (fit_alpha(distance, level, ghz, sign)[1]
                                                                             + sign * fspl_db(distance, ghz))) ** 2))),
                path_loss_exponent=float(abs(free[0]) / 10 if sign < 0 else free[0] / 10))


def measured(archive):
    """{channel: (distances, median RSS)} for line-of-sight positions, and the NLOS rows seen."""
    rows = {"CH4": {}, "CH7": {}}
    nlos = 0
    for name in archive.namelist():
        if "/UWB_Measurements/RX" not in name or not name.endswith(".csv") or "/RX_N" in name:
            continue
        channel = "CH4" if "_CH4_" in name else "CH7"
        with archive.open(name) as file:
            lines = io.TextIOWrapper(file, encoding="utf-8")
            next(lines)
            for line in lines:
                p = line.split(";", 9)
                if p[6] == "True":
                    nlos += 1
                    continue
                distance = round(float(p[1]) - float(p[3]))
                rows[channel].setdefault(distance, []).append(float(p[7]))
    out = {}
    for channel, by_distance in rows.items():
        d = np.array(sorted(by_distance))
        out[channel] = (d, np.array([np.median(by_distance[k]) for k in d]), np.array([len(by_distance[k]) for k in d]))
    return out, nlos


def simulated(archive):
    out = {}
    for channel in ("CH4", "CH7"):
        name = next(n for n in archive.namelist() if n.endswith(f"AllRay_PropData_{channel}/PathLoss.csv"))
        data = np.loadtxt(io.StringIO(archive.read(name).decode()), delimiter=",", usecols=(1, 4))
        out[channel] = (data[:, 0], data[:, 1])
    return out


def main(path):
    with zipfile.ZipFile(path) as archive:
        meas, nlos = measured(archive)
        sim = simulated(archive)
    report = dict(source="doi:10.5281/zenodo.18835598 (CC BY 4.0)", nlos_rows_skipped=nlos, measured={}, simulated={})
    for channel, ghz in CENTRE_GHZ.items():
        d, rss, count = meas[channel]
        # skip the first 2 m: the receiver is in the near field of the entrance
        keep = d >= 3
        report["measured"][channel] = dict(ghz=ghz, distances_m=[int(d[keep].min()), int(d[keep].max())],
                                           repeats_per_position=int(np.median(count)), **summarise(d[keep], rss[keep], ghz, -1))
        sd, loss = sim[channel]
        keep = sd >= 3
        report["simulated"][channel] = dict(ghz=ghz, label="simulated (CloudRT ray tracing)", **summarise(sd[keep], loss[keep], ghz, +1))
    print(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    main(sys.argv[1])
