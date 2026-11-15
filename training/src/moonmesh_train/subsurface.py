"""Radio link from an underground station to a surface station through lunar regolith and rock.

Deterministic, SIMULATED physics. Straight-ray model (no refraction), incoherent interface
transmission, bulk dielectric loss in each layer, free-space spreading over the slant length.
It does NOT model scattering from rocks, roughness, tube-wall geometry, or frequency-dependent permittivity.
"""
import math

import numpy as np

C = 299_792_458.0
NP_TO_DB = 20 * math.log10(math.e)  # 8.686 dB per neper (field)

FEATURES = ["frequency_ghz", "regolith_thickness_m", "basalt_thickness_m", "horizontal_offset_m",
            "regolith_density_g_cc", "feo_tio2_wt_pct", "basalt_eps_real", "basalt_tan_delta"]


def regolith_properties(density_g_cc, feo_tio2_wt_pct):
    """(eps_real, tan_delta) from the Carrier et al. 1991 empirical fits (unverified recall, see config)."""
    eps = 1.919 ** np.asarray(density_g_cc, float)
    tan_d = 10 ** (0.038 * np.asarray(feo_tio2_wt_pct, float) + 0.312 * np.asarray(density_g_cc, float) - 3.260)
    return eps, tan_d


def attenuation_db_per_m(frequency_ghz, eps_real, tan_delta):
    """Bulk plane-wave attenuation in a lossy dielectric (exact, not the low-loss approximation)."""
    w = 2 * np.pi * np.asarray(frequency_ghz, float) * 1e9
    eps_real, tan_delta = np.asarray(eps_real, float), np.asarray(tan_delta, float)
    alpha = (w / C) * np.sqrt(eps_real / 2 * (np.sqrt(1 + tan_delta ** 2) - 1))
    return NP_TO_DB * alpha


def interface_loss_db(eps_a, eps_b):
    """Power loss crossing a boundary at normal incidence (real parts only; loss tangents are small)."""
    na, nb = np.sqrt(np.asarray(eps_a, float)), np.sqrt(np.asarray(eps_b, float))
    gamma = (na - nb) / (na + nb)
    return -10 * np.log10(1 - gamma ** 2)


def fspl_db(distance_m, frequency_ghz):
    wavelength = C / (np.asarray(frequency_ghz, float) * 1e9)
    return 20 * np.log10(4 * np.pi * np.maximum(np.asarray(distance_m, float), 1e-6) / wavelength)


def link_budget(frequency_ghz, regolith_thickness_m, basalt_thickness_m, horizontal_offset_m,
                regolith_density_g_cc, feo_tio2_wt_pct, basalt_eps_real, basalt_tan_delta, radio):
    """Link margin (dB) from a tx in an underground void to a surface rx. Vectorised. Returns a dict of terms."""
    reg_eps, reg_tan = regolith_properties(regolith_density_g_cc, feo_tio2_wt_pct)
    vertical = np.asarray(regolith_thickness_m, float) + np.asarray(basalt_thickness_m, float)
    vertical = np.maximum(vertical, 1e-3)
    slant = np.hypot(vertical, np.asarray(horizontal_offset_m, float))
    stretch = slant / vertical  # every layer is crossed along the slanted ray
    absorption = (attenuation_db_per_m(frequency_ghz, reg_eps, reg_tan) * np.asarray(regolith_thickness_m, float)
                  + attenuation_db_per_m(frequency_ghz, basalt_eps_real, basalt_tan_delta)
                  * np.asarray(basalt_thickness_m, float)) * stretch
    has_rock = np.asarray(basalt_thickness_m, float) > 0
    # void -> (basalt ->) regolith -> vacuum; with no rock layer it is void -> regolith -> vacuum.
    boundary = np.where(has_rock,
                        interface_loss_db(1.0, basalt_eps_real) + interface_loss_db(basalt_eps_real, reg_eps),
                        interface_loss_db(1.0, reg_eps)) + interface_loss_db(reg_eps, 1.0)
    spreading = fspl_db(slant, frequency_ghz)
    margin = (radio["tx_power_dbm"] + radio["tx_gain_dbi"] + radio["rx_gain_dbi"]
              - spreading - absorption - boundary - radio["receiver_sensitivity_dbm"])
    return dict(link_margin_db=margin, absorption_db=absorption, interface_db=boundary,
                spreading_db=spreading, slant_m=slant)


def max_depth_m(frequency_ghz, radio, eps_real, tan_delta, required_margin_db=None):
    """Greatest straight-up thickness of one uniform material that still closes the link (bisection)."""
    need = radio["required_margin_db"] if required_margin_db is None else required_margin_db
    lo, hi = 0.0, 2000.0
    def margin(d):
        return radio["tx_power_dbm"] + radio["tx_gain_dbi"] + radio["rx_gain_dbi"] - radio["receiver_sensitivity_dbm"] \
            - float(fspl_db(max(d, 1e-3), frequency_ghz)) - float(attenuation_db_per_m(frequency_ghz, eps_real, tan_delta)) * d \
            - 2 * float(interface_loss_db(1.0, eps_real))
    if margin(lo) < need:
        return 0.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if margin(mid) >= need else (lo, mid)
    return lo
