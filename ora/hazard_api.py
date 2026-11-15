"""HTTP interface for the thermal hazard model (research / experimental)."""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import hazard
from .status import CARD, models

router = APIRouter(prefix="/api/hazard")
NAME = "ora_hazard_thermal"
LABEL = "research / experimental"


class Point(BaseModel):
    id: str = Field(max_length=32)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-360, le=360)
    sunlight_pct: float | None = Field(default=None, ge=0, le=100)


class ThermalRequest(BaseModel):
    points: list[Point] = Field(min_length=1, max_length=200)


def _card():
    try:
        return json.loads(CARD.read_text()).get("hazard_thermal")
    except (OSError, ValueError):
        return None


def _model():
    model, status = models()[NAME]
    return model, status


@router.get("/model")
def model_info():
    """What the model is, what it was trained on, how it did on held-out blocks, and whether it is loaded now."""
    _, status = _model()
    return dict(label=LABEL, status=status, card=_card(), features=list(hazard.FEATURES), domain="south polar cap, 80-90 S")


@router.post("/thermal")
def thermal(body: ThermalRequest):
    model, status = _model()
    if model is None:
        raise HTTPException(503, f"Thermal hazard model is not available ({status.get('error')}). "
                                 "Train it with training/scripts/18_build_hazard_dataset.py and 19_train_hazard.py.")
    try:
        result = hazard.score_points([p.model_dump() for p in body.points], model, status)
    except FileNotFoundError as error:
        raise HTTPException(503, str(error)) from error
    card = _card() or {}
    return dict(label=LABEL, **result, model=dict(sha256=status.get("sha256"), trained=card.get("git_commit"),
                                                   evaluation=(card.get("evaluation") or {}), limits=card.get("limits")))
