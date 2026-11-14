from datetime import date
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


class Mission(BaseModel):
    site: str = "haworth"
    start: date = date(2026, 11, 1)
    end: date = date(2026, 12, 1)
    minutes: Literal[5, 15, 30, 60] = 30
    availability: float = Field(default=95, ge=0, le=100)
    blackout_hours: float = Field(default=24, ge=0, le=1440)
    darkness_hours: float = Field(default=72, ge=0, le=1440)
    max_relays: int = Field(default=2, ge=0, le=4)
    deployment_km: float = Field(default=8, ge=1, le=8)
    slope_limit: float = Field(default=15, gt=0, le=45)
    observer_m: float = Field(default=2, ge=0.1, le=100)
    mast_m: float = Field(default=5, ge=0.1, le=100)
    clearance: float = Field(default=0.1, ge=0, le=10)
    battery_hours: float = Field(default=48, ge=0, le=720)
    initial_charge_pct: float = Field(default=0, ge=0, le=100)
    charge_ratio: float = Field(default=2, gt=0, le=20)
    resilient: bool = False
    latitude: float | None = Field(default=None, ge=-90, le=-85)
    longitude: float | None = Field(default=None, ge=-180, le=360)
    dem_bias_m: float = Field(default=0, ge=-30, le=30)
    earth_radio: Radio = Radio(ghz=8.4, watts=20, tx_gain=30, rx_gain=68, sensitivity=-115, loss=3, margin=3)
    surface_radio: Radio = Radio(ghz=2.4, watts=2, tx_gain=4, rx_gain=4, sensitivity=-105, loss=3, margin=3, rate_kbps=128)

    @model_validator(mode="after")
    def validate_mission(self):
        if not 1 <= (self.end - self.start).days <= 60:
            raise ValueError("Choose a mission lasting between 1 and 60 days.")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Enter both custom coordinates or leave both blank.")
        return self
