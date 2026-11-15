from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Radio(BaseModel):
    ghz: float = Field(gt=0, le=100)
    watts: float = Field(gt=0, le=1e5)
    tx_gain: float = Field(ge=-30, le=120)
    rx_gain: float = Field(ge=-30, le=120)
    sensitivity: float = Field(ge=-200, le=0)
    loss: float = Field(ge=0, le=100)
    margin: float = Field(ge=0, le=60)
    rate_kbps: float = Field(default=128, gt=0, le=1e7)


class Satellite(BaseModel):
    """Simulated orbiting relay. Two-body orbit in selenocentric J2000 axes; epoch defaults to mission start (00:00 UTC)."""
    model_config = {"allow_inf_nan": False}

    preset: Literal["polar_500km", "frozen_elliptical_south", "custom"] = "polar_500km"
    semi_major_axis_km: float | None = Field(default=None, gt=1737.4, le=60000)
    eccentricity: float | None = Field(default=None, ge=0, lt=1)
    inclination_deg: float | None = Field(default=None, ge=0, le=180)
    raan_deg: float | None = Field(default=None, ge=0, lt=360)
    arg_periapsis_deg: float | None = Field(default=None, ge=0, lt=360)
    mean_anomaly_deg: float | None = Field(default=None, ge=0, lt=360)
    epoch: datetime | None = None
    min_elevation_deg: float = Field(default=5, ge=0, le=60)
    # TODO: no verified satellite radio values exist in the repository. When a radio is None its hop is geometry-only
    # (access and occultation, no link margin) and results are labelled that way.
    surface_to_sat_radio: Radio | None = None
    sat_to_earth_radio: Radio | None = None

    @model_validator(mode="after")
    def validate_custom(self):
        if self.preset == "custom" and any(getattr(self, k) is None for k in (
                "semi_major_axis_km", "eccentricity", "inclination_deg", "raan_deg", "arg_periapsis_deg", "mean_anomaly_deg")):
            raise ValueError("A custom satellite orbit needs all six orbital elements.")
        from lunar_core.orbit import PRESETS, Orbit
        elements = dict(PRESETS.get(self.preset, {}))
        for key in ("semi_major_axis_km", "eccentricity", "inclination_deg", "raan_deg", "arg_periapsis_deg", "mean_anomaly_deg"):
            if getattr(self, key) is not None:
                elements[key] = getattr(self, key)
        Orbit(**elements, epoch=self.epoch)
        if self.epoch is not None and self.epoch.utcoffset() is None:
            raise ValueError("Satellite epoch must include a UTC offset.")
        return self


class Mission(BaseModel):
    site: str = "haworth"
    start: date = date(2026, 11, 1)
    end: date = date(2026, 12, 1)
    minutes: Literal[5, 15, 30, 60] = 30
    availability: float = Field(default=95, ge=0, le=100)
    blackout_hours: float = Field(default=24, ge=0, le=1440)
    darkness_hours: float = Field(default=72, ge=0, le=1440)
    max_relays: int = Field(default=2, ge=0, le=4)
    budget_usd: float | None = Field(default=None, ge=0, le=1e12, allow_inf_nan=False)
    relay_cost_usd: float | None = Field(default=None, ge=0, le=1e12, allow_inf_nan=False)
    deployment_km: float = Field(default=8, ge=1, le=8)
    slope_limit: float = Field(default=15, gt=0, le=45)
    observer_m: float = Field(default=2, ge=0.1, le=100)
    mast_m: float = Field(default=5, ge=0.1, le=100)
    clearance: float = Field(default=0.1, ge=0, le=10)
    battery_hours: float = Field(default=48, ge=0, le=720)
    initial_charge_pct: float = Field(default=0, ge=0, le=100)
    charge_ratio: float = Field(default=2, gt=0, le=20)
    # Explicit planning envelopes; these are assumed effective sinks, not measured air temperatures.
    dark_sink_k: float = Field(default=100, ge=20, le=350)
    cold_sink_k: float = Field(default=70, ge=20, le=350)
    lit_sink_k: float = Field(default=250, ge=20, le=400)
    node_load_w: float = Field(default=6, gt=0, le=500)
    heater_w: float = Field(default=4, ge=0, le=500)
    heat_capacity: float = Field(default=3000, ge=100, le=1e7)
    emissivity: float = Field(default=0.03, ge=0.001, le=1)
    radiating_area_m2: float = Field(default=0.15, ge=0.001, le=100)
    conductance_w_k: float = Field(default=0.004, ge=0, le=10)
    heater_on_k: float = Field(default=243.15, ge=100, le=350)
    freeze_k: float = Field(default=213.15, ge=50, le=300)
    lander_battery_hours: float = Field(default=720, ge=0, le=1440)
    lander_initial_charge_pct: float = Field(default=100, ge=0, le=100)
    dust_start_h: float = Field(default=48, ge=0, le=1440)
    dust_duration_h: float = Field(default=24, gt=0, le=1440)
    dust_radius_km: float = Field(default=8, ge=0, le=20)
    dust_deposition_per_day: float = Field(default=0.5, ge=0, le=1)
    dust_cleaning_per_day: float = Field(default=0, ge=0, le=1)
    dust_solar_loss: float = Field(default=0.8, ge=0, le=1)
    resilient: bool = False
    latitude: float | None = Field(default=None, ge=-90, le=-85)
    longitude: float | None = Field(default=None, ge=-180, le=360)
    dem_bias_m: float = Field(default=0, ge=-30, le=30)
    earth_radio: Radio = Radio(ghz=8.4, watts=20, tx_gain=30, rx_gain=68, sensitivity=-115, loss=3, margin=3)
    satellite: Satellite | None = None
    surface_radio: Radio = Radio(ghz=2.4, watts=2, tx_gain=4, rx_gain=4, sensitivity=-105, loss=3, margin=3, rate_kbps=128)

    @model_validator(mode="after")
    def validate_mission(self):
        if self.freeze_k >= self.heater_on_k:
            raise ValueError("Heater set point must be above the irreversible freeze limit.")
        if self.cold_sink_k > self.dark_sink_k:
            raise ValueError("Cold-case sink must be no warmer than the nominal dark sink.")
        if not 1 <= (self.end - self.start).days <= 60:
            raise ValueError("Choose a mission lasting between 1 and 60 days.")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Enter both custom coordinates or leave both blank.")
        return self
