"""Edge models: RF links, optical links and laser power beaming.

The optical and power-beam numbers are first-order, diffraction-limited estimates with stated
efficiencies. They size the idea; they are not a link design.
"""
from dataclasses import dataclass

import numpy as np

from moonmesh.models import Radio
from moonmesh.network import link_margin

SURFACE_RADIO = Radio(ghz=2.4, watts=2, tx_gain=4, rx_gain=4, sensitivity=-105, loss=3, margin=6, rate_kbps=128)


def fspl_db(distance_m, ghz):
    """Free-space path loss, 20*log10(4*pi*d/lambda)."""
    wavelength = 0.299792458 / ghz
    return 20 * np.log10(4 * np.pi * np.maximum(distance_m, 1e-6) / wavelength)


def rf_margin_db(distance_m, radio=SURFACE_RADIO):
    return link_margin(radio, np.maximum(np.asarray(distance_m, float), 1.0) / 1000)


def rf_capacity_kbps(margin_db, radio=SURFACE_RADIO):
    """Rate the link could carry if all spare margin became data rate (10*log10 scaling)."""
    return radio.rate_kbps * 10 ** (np.maximum(margin_db - radio.margin, 0) / 10)


@dataclass(frozen=True)
class Optical:
    wavelength_nm: float = 1550.0
    tx_aperture_m: float = 0.05
    rx_aperture_m: float = 0.10
    tx_power_w: float = 1.0
    optics_efficiency: float = 0.5
    pointing_loss_db: float = 3.0
    sensitivity_dbm: float = -40.0  # receiver sensitivity at the stated rate
    rate_mbps: float = 1000.0
    required_margin_db: float = 6.0


def divergence_rad(wavelength_m, aperture_m):
    """Diffraction-limited half-angle to the first Airy null: 1.22*lambda/D."""
    return 1.22 * wavelength_m / aperture_m


def spot_radius_m(distance_m, wavelength_m, aperture_m):
    return np.asarray(distance_m, float) * divergence_rad(wavelength_m, aperture_m)


def capture_fraction(distance_m, wavelength_m, tx_aperture_m, rx_aperture_m):
    """Share of the beam landing on the receiver aperture (uniform-spot approximation)."""
    spot = np.maximum(spot_radius_m(distance_m, wavelength_m, tx_aperture_m), tx_aperture_m / 2)
    return np.minimum(1.0, (rx_aperture_m / 2) ** 2 / spot ** 2)


def optical_margin_db(distance_m, link=Optical(), dust_loss_db=0.0):
    """Margin of an optical link over its receiver sensitivity. Needs strict line of sight."""
    capture = capture_fraction(distance_m, link.wavelength_nm * 1e-9, link.tx_aperture_m, link.rx_aperture_m)
    received_w = link.tx_power_w * link.optics_efficiency * capture
    received_dbm = 10 * np.log10(np.maximum(received_w, 1e-30) * 1000) - link.pointing_loss_db - dust_loss_db
    return received_dbm - link.sensitivity_dbm


@dataclass(frozen=True)
class PowerBeam:
    wavelength_nm: float = 1064.0
    tx_aperture_m: float = 0.10
    rx_diameter_m: float = 0.5
    electrical_in_w: float = 500.0  # electrical power into the laser at the sunlit end
    laser_efficiency: float = 0.45  # electricity to light
    optics_efficiency: float = 0.9
    pv_efficiency: float = 0.45  # tuned photovoltaic receiver, light to electricity
    pointing_efficiency: float = 0.9


def beam_budget(distance_m, beam=PowerBeam()):
    """Stage-by-stage power-beam budget from a sunlit transmitter to a dark receiver."""
    wavelength = beam.wavelength_nm * 1e-9
    theta = divergence_rad(wavelength, beam.tx_aperture_m)
    spot = float(spot_radius_m(distance_m, wavelength, beam.tx_aperture_m))
    capture = float(capture_fraction(distance_m, wavelength, beam.tx_aperture_m, beam.rx_diameter_m))
    optical_w = beam.electrical_in_w * beam.laser_efficiency * beam.optics_efficiency
    delivered = optical_w * capture * beam.pointing_efficiency * beam.pv_efficiency
    return dict(divergence_rad=theta, spot_radius_m=spot, capture_fraction=capture, optical_w=optical_w,
                delivered_w=delivered, end_to_end_efficiency=delivered / beam.electrical_in_w,
                irradiance_w_m2=optical_w * beam.pointing_efficiency / (np.pi * max(spot, beam.tx_aperture_m / 2) ** 2))


def add_optical_edges(G, pairs, link=Optical(), dust_loss_db=0.0):
    """Upgrade line-of-sight edges in ``pairs`` to optical trunks when the optical margin allows.

    The RF edge is kept as ``backup`` so resilience tests can ask what happens when optics fail.
    """
    upgraded = []
    for a, b in pairs:
        if not G.has_edge(a, b):
            continue
        margin = float(optical_margin_db(G[a][b]["dist"], link, dust_loss_db))
        if margin >= link.required_margin_db:
            G[a][b].update(kind="optical", optical_margin=margin, capacity_mbps=link.rate_mbps, backup="rf")
            upgraded.append((a, b))
    return upgraded
