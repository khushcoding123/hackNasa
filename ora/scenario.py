"""A scenario is one YAML file: site, candidates, ranges, mining sites and seeds.

``build`` turns it into a network: candidate sites on real terrain, the line-of-sight graph,
gateways with an Earth view, mining demand on the crater floor, the siting scores and the
chosen relay set. Everything is deterministic for a given seed.
"""
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

from lunar_core.geometry import Body, SurfacePoint, horizontal_arrays
from lunar_core.kernels import load_kernels
from lunar_core.terrain import polar_stereographic_south
from moonmesh.data import DATA
from moonmesh.evidence import NOT_MODELLED, assess, check, gap
from moonmesh.science import Observer, mask_for

from .data import ALL_SITES, terrain
from .features import CHEAP, FEATURES, VIEW_RADIUS_M, rasters, view_rings
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
    polygon: tuple = ()  # optional drawn area as (lat, lon east) vertices; relays and mines stay inside it
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
    if "polygon" in raw:
        raw["polygon"] = tuple(tuple(float(v) for v in pair) for pair in raw["polygon"])
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


SITING_METHOD = "computed 8 km viewshed share (120 rays, 20 m steps)"


def scoring_evidence(method):
    """How the siting scores were produced: the method, any trained artifacts used (with hashes), and whether a fallback ran."""
    from .status import model_status
    used = {"lunar coverage surrogate (80%) with Earth-tower prior (20%)": ("ora_coverage_surrogate", "ora_siting_prior"),
            "trained Earth-tower prior": ("ora_siting_prior",)}.get(method, ())
    rows = [dict(name=m["name"], loaded=m["loaded"], used=m["name"] in used, sha256=m.get("sha256"), error=m.get("error"),
                 warnings=m.get("warnings", [])) for m in model_status() if m["name"] != "ora_gain_model"]
    note = ("Candidates are ranked by how much ground each one sees within 8 km, computed by a radial line-of-sight sweep. "
            "Against dense line-of-sight truth this ranks sites with 99.6% rank correlation; the trained surrogate and "
            "Earth-tower prior scored lower and are not used for ranking (docs/ACCURACY.md). Every chosen link is "
            "re-checked with exact line of sight.") if method == SITING_METHOD else         "Siting scores only rank candidates. Every chosen link is re-checked with exact line of sight on the terrain grid."
    return dict(method=method, fallback=method.startswith("terrain heuristic"), models=rows, note=note)


def siting_scores(dem, px_m, cells, layers=None):
    """Siting score in 0..1 for each cell (rank of its computed 8 km viewshed share), the feature rows and the method name.

    The viewshed share is computed, not predicted: a 120-ray sweep with 20 m radial steps, 10 m mast and 2 m receiver.
    It agrees with dense line of sight to R² 98.7% and rank correlation 99.6%, where the old learned blend
    (coverage surrogate 80%, Earth-tower prior 20%) reached far less (docs/ACCURACY.md). ``features`` keeps the
    FEATURES column order, with the computed share in the viewshed column.
    """
    layers = layers or rasters(dem, px_m)
    cells = np.asarray(cells, float)
    c, r = np.rint(cells[:, 0]).astype(int), np.rint(cells[:, 1]).astype(int)
    view = view_rings(dem, px_m, cells, (VIEW_RADIUS_M,), step_px=1)[:, 0]
    features = np.column_stack([layers[name][r, c] for name in CHEAP] + [view]).astype(np.float32)
    rank = np.argsort(np.argsort(view, kind="stable"), kind="stable") / max(len(view) - 1, 1)
    return rank, features, SITING_METHOD


def floor_sites(dem, px_m, center, count, search_m=3_500, spacing_m=1_500, slope_limit=10.0, mask=None):
    """Mining sites: the lowest gentle cells near the centre, spaced apart (the permanently shadowed floor)."""
    rows, cols = dem.shape
    yy, xx = np.ogrid[:rows, :cols]
    dy, dx = np.gradient(np.nan_to_num(dem, nan=np.nanmean(dem)), px_m, px_m)
    ok = (np.hypot(xx - center[0], yy - center[1]) * px_m <= search_m) & np.isfinite(dem) \
        & (np.degrees(np.arctan(np.hypot(dx, dy))) <= slope_limit)
    if mask is not None:
        ok &= mask
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
def _build(scenario, artifacts=()):
    # ``artifacts`` (size and mtime of each model file) is part of the cache key, so a retrained or replaced model
    # rebuilds the network instead of serving scores from the old one.
    if scenario.site not in ALL_SITES:
        raise ValueError(f"Unknown site {scenario.site}. Choose one of {', '.join(ALL_SITES)}.")
    grid = terrain(scenario.site)
    dem, px = grid.heights, grid.pixel_size_m
    name, lat0, lon0 = ALL_SITES[scenario.site]
    x0, y0 = polar_stereographic_south(lat0, lon0)
    center = (float((x0 - grid.x_left) / px - 0.5), float((grid.y_top - y0) / px - 0.5))
    mask, spacing = None, 300
    if scenario.polygon:
        from .area import polygon_mask
        mask = polygon_mask(grid, scenario.polygon)
        spacing = min(300, max(px, scenario.radius_km * 1000 / 8)) if scenario.radius_km < 3 else max(300, min(2_000, scenario.radius_km * 25))
    relays = candidate_sites(dem, px, scenario.candidates, center, scenario.radius_km * 1000, scenario.slope_limit,
                             seed=scenario.seed, mask=mask, min_spacing_m=spacing)
    inside = len(relays)
    if mask is not None:
        # A drawn area may have no ground that sees Earth. Allow gateway-only sites in a ring just outside it.
        yy, xx = np.ogrid[:dem.shape[0], :dem.shape[1]]
        from .area import ring_for
        reach = scenario.radius_km * 1000 + ring_for(scenario.radius_km * 1000)
        ring = (np.hypot(xx - center[0], yy - center[1]) * px <= reach) & ~mask
        outside = candidate_sites(dem, px, max(30, scenario.candidates // 3), center, reach, scenario.slope_limit,
                                  seed=scenario.seed + 1, mask=ring, min_spacing_m=spacing)
        if len(outside):
            relays = np.vstack([relays, outside])
    if scenario.demand:
        dx, dy = polar_stereographic_south(*np.array(scenario.demand).T)
        demand_xy = np.column_stack([(dx - grid.x_left) / px - 0.5, (grid.y_top - dy) / px - 0.5])
    else:
        demand_xy = floor_sites(dem, px, center, scenario.mining_sites, search_m=scenario.radius_km * 1000 if mask is not None else 3_500,
                                spacing_m=(min(1_500, scenario.radius_km * 400) if scenario.radius_km < 4 else min(6_000, scenario.radius_km * 100)) if mask is not None else 1_500, mask=mask)
    if mask is not None and len(demand_xy) and scenario.radius_km >= 3:
        # In a big area the mines sit on low ground that a thin scatter of candidates can miss entirely,
        # so add sites on the high ground within reach of each one.
        local = [candidate_sites(dem, px, 40, tuple(m), 8_000.0, scenario.slope_limit, seed=scenario.seed + 2 + i, min_spacing_m=spacing)
                 for i, m in enumerate(demand_xy)]
        relays = np.vstack([relays, *[c for c in local if len(c)]])
    outside_flag = np.zeros(len(relays), bool) if mask is None else ~mask[np.rint(relays[:, 1]).astype(int), np.rint(relays[:, 0]).astype(int)]
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
    # Ground just outside a drawn area may be used when nothing inside links up, but is deliberately less attractive.
    prior = {i: float(score[i]) * (0.5 if outside_flag[i] else 1.0) for i in range(n_relays)}
    # A relay that sits in shadow has to be kept alive through it, so chains pay heavily for shade.
    shade = {i: 4.0 * (1.0 - float(sun_frac[i])) for i in range(n_relays)}
    plan = place(G, demand, gateways, candidates, prior, scenario.extra_relays, shade_penalty=shade)
    return dict(scenario=scenario, grid=grid, center=center, sites=sites, heights=heights, lat=lat, lon=lon, ground=ground,
                n_relays=n_relays, demand=demand, gateways=gateways, candidates=candidates, G=G, times=times, lit=lit,
                earth=earth, sun_frac=sun_frac, earth_frac=earth_frac, score=score, features=features,
                score_method=method, score_evidence=scoring_evidence(method), plan=plan, site_name=name, inside=inside, outside=outside_flag)


def _artifacts():
    from .status import MODELS
    stats = ((DATA / "models" / f"{name}.joblib") for name in MODELS)
    return tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) if p.is_file() else (p.name, None, None) for p in stats)


def build(scenario):
    """Network for a scenario (cached). Accepts a Scenario, a scenario name or a YAML path."""
    return _build(scenario if isinstance(scenario, Scenario) else load(scenario), _artifacts())


def plan_assessment(network):
    """Requirements met / failed / insufficient evidence for a relay plan, with what was and was not evaluated."""
    scenario, plan, px = network["scenario"], network["plan"], network["grid"].pixel_size_m
    gateways = network["gateways"]
    contact = float(network["earth"][gateways].any(axis=0).mean() * 100) if gateways else 0.0
    checks = [check("Every mining site linked to a gateway", "100% of mining sites", round(100 * plan["served"], 1), bool(plan["feasible"]),
                    "Exact line of sight on the terrain grid" + (" and surface RF margin" if scenario.use_rf_margin else "")),
              check("At least one gateway with an Earth view", f"≥ 1 gateway seeing Earth > 5% of {scenario.days} days", len(gateways),
                    len(gateways) > 0, "SPICE Earth position against each site's terrain horizon")]
    gaps = []
    if px > 20.5:
        gaps.append(gap("Coarse terrain", f"Terrain is {px:.0f} m per pixel. Ridges and hollows narrower than that are not resolved, "
                        "so a link can be blocked or open in reality where the grid says otherwise."))
    if not scenario.use_rf_margin:
        gaps.append(gap("Link margin not checked", "Links are line of sight and range only; no RF budget was applied."))
    evidence = network.get("score_evidence") or {}
    if evidence.get("fallback"):
        errors = "; ".join(f"{m['name']}: {m['error']}" for m in evidence.get("models", []) if m.get("error"))
        gaps.append(gap("Siting models not loaded", f"Candidates were ranked with the terrain heuristic ({errors or 'no trained models loaded'}).", False))
    gaps.append(gap("Earth contact over time", f"A gateway sees Earth {contact:.0f}% of the period. The placement does not require continuous "
                    "Earth contact; the twin reports contact over time.", False))
    return assess(
        checks, gaps, kind="calculated window", subject="relay placement (static links; not Earth contact over time)",
        architecture="Surface mesh: mining sites to relays to gateways with an Earth view (direct to Earth). No orbiting relay.",
        evaluated=["Terrain line of sight between every pair of sites", "Surface RF margin" if scenario.use_rf_margin else "Link range only",
                   "Earth and Sun visibility per site from SPICE and the terrain horizon", "Loss of any one relay or link (graph what-if)"],
        not_evaluated=list(NOT_MODELLED),
        assumptions=[f"Relay mast {scenario.relay_mast_m:g} m, mining-site antenna {scenario.demand_mast_m:g} m, link range {scenario.max_range_km:g} km",
                     f"{scenario.candidates} candidate sites within {scenario.radius_km:g} km, slope up to {scenario.slope_limit:g}°, seed {scenario.seed}",
                     f"Period {scenario.start.isoformat()} for {scenario.days} days at {scenario.step_h:g} h steps"],
        metrics=dict(served_pct=round(100 * plan["served"], 1), worst_after_single_failure_pct=round(100 * plan["worst_after_failure"], 1),
                     gateway_earth_contact_pct=round(contact, 1), terrain_m_per_pixel=px))
