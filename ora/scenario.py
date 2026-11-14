"""A scenario is one YAML file: site, candidates, ranges, mining sites and seeds.

``build`` turns it into a network: candidate sites on real terrain, the line-of-sight graph,
gateways with an Earth view, mining demand on the crater floor, the siting scores and the
chosen relay set. Everything is deterministic for a given seed.
"""
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import yaml

from lunar_core.geometry import Body, SurfacePoint, horizontal_arrays
from lunar_core.kernels import load_kernels
from lunar_core.terrain import polar_stereographic_south
from moonmesh.data import DATA
from moonmesh.science import Observer, mask_for

from .data import ALL_SITES, terrain
from .features import CHEAP, FEATURES, prior_features, rasters, surrogate_features
from .graph import build_los_graph, candidate_sites, to_latlon
from .links import SURFACE_RADIO
from .optimize import place

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
SUN_SEMIDIAMETER_DEG = 0.266
EARTH_CLEARANCE_DEG = 0.1


@dataclass(frozen=True)
class Scenario:
    name: str = "shackleton_rim_to_floor"
    site: str = "shackleton"
    candidates: int = 220
    radius_km: float = 14.0
    max_range_km: float = 12.0
    relay_mast_m: float = 10.0
    demand_mast_m: float = 2.0
    mining_sites: int = 3  # picked on the lowest gentle ground near the centre, unless ``demand`` is given
    demand: tuple = ()  # optional explicit (lat, lon east) pairs
    gateways: int = 2
    extra_relays: int = 3
    slope_limit: float = 20.0
    start: date = date(2026, 11, 1)
    days: int = 30
    step_h: float = 2.0
    seed: int = 0
    use_rf_margin: bool = True
    notes: str = ""

    def key(self):
        return tuple(sorted((k, str(v)) for k, v in asdict(self).items()))


def load(name_or_path):
    path = Path(name_or_path)
    if not path.is_file():
        path = SCENARIOS / f"{name_or_path}.yaml"
    raw = yaml.safe_load(path.read_text()) or {}
    raw.setdefault("name", path.stem)
    if "demand" in raw:
        raw["demand"] = tuple(tuple(float(v) for v in pair) for pair in raw["demand"])
    if isinstance(raw.get("start"), str):
        raw["start"] = date.fromisoformat(raw["start"])
    return Scenario(**{k: v for k, v in raw.items() if k in Scenario.__dataclass_fields__})


def available():
    """Surface scenarios. Files with ``kind: tunnel`` or ``kind: earth`` are run from the command line."""
    return sorted(path.stem for path in SCENARIOS.glob("*.yaml")
                  if (yaml.safe_load(path.read_text()) or {}).get("kind", "surface") == "surface")


def times_for(scenario):
    start = datetime.combine(scenario.start, time(), UTC)
    return tuple(start + timedelta(hours=scenario.step_h * i) for i in range(int(scenario.days * 24 / scenario.step_h)))


def sky(site, lat, lon, ground, mast, times):
    """Sun and Earth as seen from one antenna: lit fraction of the solar disc and Earth visibility per step."""
    load_kernels(DATA / "kernels")
    horizon = mask_for(site, Observer(float(lat), float(lon), float(ground), float(mast)), 0.0)
    point = SurfacePoint(float(lat), float(lon), float(ground) + float(mast))
    sun_az, sun_el, _ = horizontal_arrays(Body.SUN, point, times)
    earth_az, earth_el, _ = horizontal_arrays(Body.EARTH, point, times)
    sun_limit, earth_limit = horizon.at(sun_az), horizon.at(earth_az)
    lit = np.clip((sun_el - sun_limit + SUN_SEMIDIAMETER_DEG) / (2 * SUN_SEMIDIAMETER_DEG), 0, 1)
    earth = (earth_el - earth_limit - EARTH_CLEARANCE_DEG) > 0
    return np.nan_to_num(lit, nan=0.0), np.where(np.isfinite(earth_limit), earth, False)


@lru_cache(maxsize=2)
def _models():
    out = {}
    for name in ("ora_siting_prior", "ora_coverage_surrogate"):
        path = DATA / "models" / f"{name}.joblib"
        if path.is_file():
            try:
                out[name] = joblib.load(path)
            except Exception:  # a model saved by another library version is ignored, not fatal
                pass
    return out


def siting_scores(dem, px_m, cells, layers=None):
    """Siting score in 0..1 for each cell and the name of the method that produced it.

    Uses the trained Earth-tower prior when it is available. Otherwise falls back to a
    transparent terrain heuristic (prominence plus view), so the app still runs untrained.
    """
    full = prior_features(dem, px_m, cells, layers)
    features = full[:, :len(FEATURES)]
    models = _models()
    model, surrogate = models.get("ora_siting_prior"), models.get("ora_coverage_surrogate")
    rank = lambda v: np.argsort(np.argsort(v)) / max(len(v) - 1, 1)
    if surrogate is not None and model is not None:
        # The Earth prior ranks lunar cells poorly (Spearman 0.18 with true visibility), so the lunar coverage
        # surrogate leads and the prior only breaks ties.
        wide, _ = surrogate_features(dem, px_m, cells, layers)
        seen = surrogate.predict(wide[:, :surrogate.n_features_in_])
        tower = model.predict_proba(full[:, :model.n_features_in_])[:, 1]
        return 0.8 * rank(seen) + 0.2 * rank(tower), features, "lunar coverage surrogate (80%) with Earth-tower prior (20%)"
    if model is not None:
        return model.predict_proba(full[:, :model.n_features_in_])[:, 1], features, "trained Earth-tower prior"
    rank = lambda v: np.argsort(np.argsort(v)) / max(len(v) - 1, 1)
    return 0.5 * rank(features[:, CHEAP.index("tpi_1000")]) + 0.5 * rank(features[:, -1]), features, "terrain heuristic (untrained)"


def floor_sites(dem, px_m, center, count, search_m=3_500, spacing_m=1_500, slope_limit=10.0):
    """Mining sites: the lowest gentle cells near the centre, spaced apart (the permanently shadowed floor)."""
    rows, cols = dem.shape
    yy, xx = np.ogrid[:rows, :cols]
    dy, dx = np.gradient(np.nan_to_num(dem, nan=np.nanmean(dem)), px_m, px_m)
    ok = (np.hypot(xx - center[0], yy - center[1]) * px_m <= search_m) & np.isfinite(dem) \
        & (np.degrees(np.arctan(np.hypot(dx, dy))) <= slope_limit)
    r, c = np.nonzero(ok)
    chosen = []
    for i in np.argsort(dem[r, c]):
        point = np.array([c[i], r[i]], float)
        if all(np.hypot(*(point - p)) * px_m >= spacing_m for p in chosen):
            chosen.append(point)
            if len(chosen) == count:
                break
    return np.array(chosen).reshape(-1, 2)


@lru_cache(maxsize=4)
def _build(scenario):
    if scenario.site not in ALL_SITES:
        raise ValueError(f"Unknown site {scenario.site}. Choose one of {', '.join(ALL_SITES)}.")
    grid = terrain(scenario.site)
    dem, px = grid.heights, grid.pixel_size_m
    name, lat0, lon0 = ALL_SITES[scenario.site]
    x0, y0 = polar_stereographic_south(lat0, lon0)
    center = (float((x0 - grid.x_left) / px - 0.5), float((grid.y_top - y0) / px - 0.5))
    relays = candidate_sites(dem, px, scenario.candidates, center, scenario.radius_km * 1000, scenario.slope_limit,
                             seed=scenario.seed)
    if scenario.demand:
        dx, dy = polar_stereographic_south(*np.array(scenario.demand).T)
        demand_xy = np.column_stack([(dx - grid.x_left) / px - 0.5, (grid.y_top - dy) / px - 0.5])
    else:
        demand_xy = floor_sites(dem, px, center, scenario.mining_sites)
    sites = np.vstack([relays, demand_xy])
    n_relays = len(relays)
    demand = list(range(n_relays, len(sites)))
    heights = np.r_[np.full(n_relays, scenario.relay_mast_m), np.full(len(demand), scenario.demand_mast_m)]
    G = build_los_graph(dem, px, sites, max_range_m=scenario.max_range_km * 1000, heights=heights,
                        radio=SURFACE_RADIO if scenario.use_rf_margin else None)
    lat, lon = to_latlon(grid, sites)
    ground = dem[np.rint(sites[:, 1]).astype(int), np.rint(sites[:, 0]).astype(int)]
    times = times_for(scenario)
    lit, earth = zip(*(sky(scenario.site, lat[i], lon[i], ground[i], heights[i], times) for i in range(len(sites))))
    lit, earth = np.array(lit), np.array(earth)
    sun_frac, earth_frac = lit.mean(axis=1), earth.mean(axis=1)
    layers = rasters(dem, px)
    score, features, method = siting_scores(dem, px, relays, layers)
    # Gateways: relay candidates with the best Earth view, preferring ones that also get sunlight.
    gateway_rank = np.argsort(-(earth_frac[:n_relays] * (0.5 + 0.5 * sun_frac[:n_relays])))
    gateways = []
    for i in gateway_rank:
        if earth_frac[i] <= 0.05 or len(gateways) >= scenario.gateways:
            break
        if all(np.hypot(*(sites[i] - sites[g])) * px >= 1500 for g in gateways):
            gateways.append(int(i))
    candidates = [i for i in range(n_relays) if i not in gateways]
    prior = {i: float(score[i]) for i in range(n_relays)}
    # A relay that sits in shadow has to be kept alive through it, so chains pay heavily for shade.
    shade = {i: 4.0 * (1.0 - float(sun_frac[i])) for i in range(n_relays)}
    plan = place(G, demand, gateways, candidates, prior, scenario.extra_relays, shade_penalty=shade)
    return dict(scenario=scenario, grid=grid, center=center, sites=sites, heights=heights, lat=lat, lon=lon, ground=ground,
                n_relays=n_relays, demand=demand, gateways=gateways, candidates=candidates, G=G, times=times, lit=lit,
                earth=earth, sun_frac=sun_frac, earth_frac=earth_frac, score=score, features=features,
                score_method=method, plan=plan, site_name=name)


def build(scenario):
    """Network for a scenario (cached). Accepts a Scenario, a scenario name or a YAML path."""
    return _build(scenario if isinstance(scenario, Scenario) else load(scenario))
