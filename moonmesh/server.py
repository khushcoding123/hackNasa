from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from spiceypy.utils.exceptions import SpiceyError

from .data import SITES
from .models import Mission
from .science import scenario

app = FastAPI(title="MOONMESH", description="Lunar landing-site and communications planning prototype")


def compute(mission, **kwargs):
    try:
        return scenario(mission, **kwargs)
    except FileNotFoundError as error:
        raise HTTPException(503, str(error)) from error
    except SpiceyError as error:
        raise HTTPException(422, "SPICE could not resolve this period or kernel set. Check the date range and run the data setup.") from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/config")
def config():
    return dict(defaults=Mission(), sites=SITES)


@app.post("/api/mission")
def mission(body: Mission):
    return compute(body)


@app.post("/api/compare")
def compare(body: Mission):
    output = []
    for site in SITES:
        result = compute(body.model_copy(update=dict(site=site, latitude=None, longitude=None)), include_map=False)
        output.append({key: result[key] for key in ("site", "direct", "solar", "network", "worst", "recommended", "feasible")})
    return output


@app.post("/api/sensitivity")
def sensitivity(body: Mission):
    variants = [
        ("Baseline", {}),
        ("Lander mast +3 m", dict(observer_m=min(body.observer_m+3, 100))),
        ("Relay mast +3 m", dict(mast_m=min(body.mast_m+3, 100))),
        ("Clearance +0.25°", dict(clearance=min(body.clearance+0.25, 10))),
        ("Terrain rises by up to 5 m along horizon rays", dict(dem_bias_m=min(body.dem_bias_m+5, 30))),
        ("Battery endurance +24 h", dict(battery_hours=min(body.battery_hours+24, 720))),
        ("Time step " + ("15 min" if body.minutes >= 30 else "5 min" if body.minutes == 15 else "30 min"),
         dict(minutes=15 if body.minutes >= 30 else 5 if body.minutes == 15 else 30)),
    ]
    site = SITES.get(body.site)
    if site:
        lat = body.latitude if body.latitude is not None else site[1]
        lon = body.longitude if body.longitude is not None else site[2]
        lat = lat + 0.0066 if lat <= -85.0066 else lat - 0.0066
        variants.append(("Landing point shifted ~200 m", dict(latitude=lat, longitude=lon)))
    rows = []
    for label, changes in variants:
        try:
            result = compute(body.model_copy(update=changes), include_map=False)
            rows.append(dict(label=label, direct=result["direct"], network=result["network"],
                             feasible=result["feasible"], count=len(result["recommended"]), eligible=result["eligible"]))
        except HTTPException as error:
            if not changes:
                raise
            rows.append(dict(label=label, error=error.detail))
    return rows


app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[1] / "web", html=True), name="web")
