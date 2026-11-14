"""HTTP endpoints for the ORA lab. Mounted under /api/ora by moonmesh.server."""
import json
from dataclasses import asdict, replace
from datetime import date
from functools import lru_cache
from pathlib import Path

import networkx as nx
import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import breadcrumb, resilience, twin
from .data import ALL_SITES
from .graph import degree_matched_disk, unit_disk_graph
from .links import Optical, PowerBeam, beam_budget, optical_margin_db, rf_margin_db
from .los import visible_cells
from .scenario import Scenario, available, build, load
from .thermal import LUNAR_NIGHT_H, NodeDesign, night_budget
from .tunnel import TunnelRadio, drift_mine, room_and_pillar

router = APIRouter(prefix="/api/ora")
CARD = Path(__file__).with_name("model_card.json")


class NetworkBody(BaseModel):
    scenario: str = "shackleton_rim_to_floor"
    site: str | None = None
    candidates: int | None = Field(default=None, ge=40, le=600)
    max_range_km: float | None = Field(default=None, ge=1, le=20)
    relay_mast_m: float | None = Field(default=None, ge=1, le=100)
    mining_sites: int | None = Field(default=None, ge=1, le=6)
    extra_relays: int | None = Field(default=None, ge=0, le=8)
    start: date | None = None
    seed: int | None = Field(default=None, ge=0, le=9999)

    def resolve(self):
        try:
            base = load(self.scenario)
        except FileNotFoundError as error:
            raise HTTPException(404, f"Unknown scenario {self.scenario}.") from error
        fields = set(NetworkBody.model_fields) - {"scenario"}
        changes = {k: getattr(self, k) for k in fields if getattr(self, k) is not None}
        return replace(base, **changes)


class Design(BaseModel):
    battery_wh: float = Field(default=600, ge=1, le=100_000)
    solar_w: float = Field(default=40, ge=0, le=5000)
    active_w: float = Field(default=6, ge=0.1, le=500)
    survival_w: float = Field(default=0.5, ge=0, le=100)
    heater_w: float = Field(default=4, ge=0, le=500)
    rhu_w: float = Field(default=0, ge=0, le=50)
    hibernate: bool = False
    duty: float = Field(default=1.0, ge=0, le=1)

    def node(self):
        return NodeDesign(**self.model_dump())


class Kill(BaseModel):
    node: int
    hour: float = Field(ge=0)


class TwinBody(NetworkBody):
    design: Design = Design()
    kill: list[Kill] = []
    disk_radius_m: float | None = Field(default=None, ge=50, le=10_000)
    disk_node: int | None = None
    disk_hour: float = Field(default=0, ge=0)
    night_start_h: float | None = Field(default=None, ge=0)
    night_hours: float = Field(default=LUNAR_NIGHT_H, ge=1, le=720)
    beam: bool = False
    beam_electrical_w: float = Field(default=500, ge=1, le=100_000)
    optical: bool = False
    dust_db: float = Field(default=0, ge=0, le=80)
    rf_backup: bool = True
    blast_node: int | None = None
    blast_hour: float = Field(default=0, ge=0)
    blast_kill_m: float = Field(default=150, ge=0, le=10_000)
    blast_damage_m: float = Field(default=1500, ge=0, le=20_000)
    dust_rate_per_day: float = Field(default=0, ge=0, le=1)
    dust_hazard_per_h: float = Field(default=0, ge=0, le=0.1)
    dust_seed: int = Field(default=0, ge=0, le=9999)

    def options(self, network):
        disk = None
        if self.disk_radius_m is not None and self.disk_node is not None and 0 <= self.disk_node < len(network["sites"]):
            x, y = network["sites"][self.disk_node]
            disk = (float(x), float(y), self.disk_radius_m, self.disk_hour)
        blast = None
        if self.blast_node is not None and 0 <= self.blast_node < len(network["sites"]):
            blast = (self.blast_node, self.blast_hour, self.blast_kill_m, max(self.blast_damage_m, self.blast_kill_m))
        dust = None
        if self.dust_rate_per_day or self.dust_hazard_per_h:
            dust = dict(rate_per_day=self.dust_rate_per_day, base_hazard_per_h=self.dust_hazard_per_h, seed=self.dust_seed)
        return dict(kill=[(k.node, k.hour) for k in self.kill], disk=disk, blast=blast, dust=dust,
                    night=None if self.night_start_h is None else (self.night_start_h, self.night_hours),
                    beam=self.beam, beam_design=PowerBeam(electrical_in_w=self.beam_electrical_w), optical=self.optical,
                    dust_db=self.dust_db, rf_backup=self.rf_backup)


class ResilienceBody(NetworkBody):
    seeds: int = Field(default=100, ge=5, le=400)
    disk_radius_m: float = Field(default=1500, ge=100, le=10_000)


class TunnelBody(BaseModel):
    layout: str = "drift"
    seed: int = Field(default=0, ge=0, le=9999)
    loops: bool = True
    branches: int = Field(default=4, ge=1, le=8)
    rows: int = Field(default=4, ge=2, le=6)
    cols: int = Field(default=6, ge=2, le=8)
    ghz: float = Field(default=2.4, gt=0.05, le=60)
    alpha_db_per_100m: float = Field(default=4, ge=0, le=60)
    bend_db_per_90deg: float = Field(default=14, ge=0, le=60)


class BeamBody(BaseModel):
    distance_m: float = Field(default=5000, ge=10, le=100_000)
    wavelength_nm: float = Field(default=1064, ge=300, le=11_000)
    tx_aperture_m: float = Field(default=0.1, gt=0.001, le=5)
    rx_diameter_m: float = Field(default=0.5, gt=0.001, le=20)
    electrical_in_w: float = Field(default=500, ge=1, le=1_000_000)
    dust_db: float = Field(default=0, ge=0, le=80)


def network_for(body):
    try:
        return build(body.resolve())
    except FileNotFoundError as error:
        raise HTTPException(503, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def shade(dem, size=180):
    """Small hillshade raster (0..255) for the map background."""
    step = max(1, dem.shape[0] // size)
    coarse = dem[::step, ::step]
    dy, dx = np.gradient(np.nan_to_num(coarse, nan=float(np.nanmean(coarse))))
    light = np.clip(0.5 + (dx - dy) / (4 * (np.abs(dx).mean() + np.abs(dy).mean()) + 1e-9), 0, 1)
    low, high = np.nanmin(coarse), np.nanmax(coarse)
    tone = 0.35 + 0.65 * (np.nan_to_num(coarse, nan=low) - low) / max(high - low, 1)
    return np.round(255 * light * tone).astype(int).tolist(), step


@lru_cache(maxsize=4)
def coverage_map(scenario, stride=10):
    """How many chosen relays each coarse cell can hear (2 m receiver). -1 marks cells outside the study radius."""
    network = build(scenario)
    dem, px = network["grid"].heights, network["grid"].pixel_size_m
    rows, cols = np.mgrid[0:dem.shape[0]:stride, 0:dem.shape[1]:stride]
    cells = np.column_stack([cols.ravel(), rows.ravel()]).astype(float)
    inside = np.hypot(cells[:, 0] - network["center"][0], cells[:, 1] - network["center"][1]) * px <= scenario.radius_km * 1000
    count = np.zeros(len(cells), int)
    for v in list(network["plan"]["chosen"]) + list(network["gateways"]):
        near = inside & (np.hypot(*(cells - network["sites"][v]).T) * px <= scenario.max_range_km * 1000)
        index = np.flatnonzero(near)
        count[index] += visible_cells(dem, px, network["sites"][v], cells[index], scenario.relay_mast_m)
    grid = np.where(inside, count, -1).reshape(rows.shape)
    return grid, float((count[inside] > 0).mean() * 100), float((count[inside] > 1).mean() * 100)


def plan_graph(network):
    members = list(network["plan"]["chosen"]) + list(network["gateways"]) + list(network["demand"])
    return network["G"].subgraph(members), members


@router.get("/config")
def config():
    card = json.loads(CARD.read_text()) if CARD.is_file() else None
    return dict(scenarios={name: {k: (str(v) if isinstance(v, date) else v) for k, v in asdict(load(name)).items()} for name in available()},
                sites={key: value[0] for key, value in ALL_SITES.items()}, design=Design().model_dump(),
                modes=list(resilience.MODES), lunar_night_h=LUNAR_NIGHT_H, model_card=card)


@router.post("/network")
def network(body: NetworkBody):
    net = network_for(body)
    scenario, G, plan = net["scenario"], net["G"], net["plan"]
    H, members = plan_graph(net)
    role = {**{v: "candidate" for v in range(net["n_relays"])}, **{v: "relay" for v in plan["chosen"]},
            **{v: "gateway" for v in net["gateways"]}, **{v: "mine" for v in net["demand"]}}
    relays = list(plan["chosen"]) + list(net["gateways"])
    fragile = []
    for v in relays:
        rest = [w for w in members if w != v]
        fragile.append(dict(id=int(v), role=role[v], served_if_lost=resilience.demand_connected(
            G.subgraph(rest), net["demand"], [g for g in net["gateways"] if g != v])))
    grid, covered, redundant = coverage_map(scenario)
    image, step = shade(net["grid"].heights)
    structure = resilience.structural_report(H, net["demand"], net["gateways"])
    structure["disjoint_paths"] = {str(k): int(v) for k, v in structure.get("disjoint_paths", {}).items()}
    return dict(
        scenario={k: (str(v) if isinstance(v, date) else v) for k, v in asdict(scenario).items()}, site=net["site_name"],
        pixel_m=net["grid"].pixel_size_m, size=list(net["grid"].heights.shape), map=dict(image=image, step=step),
        coverage=dict(grid=grid.tolist(), stride=10, covered_pct=covered, redundant_pct=redundant),
        nodes=[dict(id=int(v), role=role[v], x=float(net["sites"][v][0]), y=float(net["sites"][v][1]), lat=float(net["lat"][v]),
                    lon=float(net["lon"][v]), elevation=float(net["ground"][v]), sun=float(net["sun_frac"][v]),
                    earth=float(net["earth_frac"][v]), degree=int(G.degree(v)),
                    score=float(net["score"][v]) if v < net["n_relays"] else None) for v in G.nodes],
        edges=[dict(a=int(a), b=int(b), distance_m=d["dist"], margin_db=d.get("margin")) for a, b, d in H.edges(data=True)],
        all_edges=G.number_of_edges(), score_method=net["score_method"],
        plan=dict(chosen=[int(v) for v in plan["chosen"]], chain=[int(v) for v in plan["chain"]], added=[int(v) for v in plan["added"]],
                  gateways=[int(v) for v in net["gateways"]], mines=[int(v) for v in net["demand"]],
                  unreachable=[int(v) for v in plan["unreachable"]], served=plan["served"],
                  mean_after_failure=plan["mean_after_failure"], worst_after_failure=plan["worst_after_failure"],
                  feasible=plan["feasible"]),
        configs=twin.node_configs(net), structure=structure, full_structure=resilience.structural_report(G, connectivity=False), fragile=sorted(fragile, key=lambda f: f["served_if_lost"]))


@router.post("/resilience")
def resilience_lab(body: ResilienceBody):
    net = network_for(body)
    scenario, G, sites, px = net["scenario"], net["G"], net["sites"], net["grid"].pixel_size_m
    matched, matched_range = degree_matched_disk(sites, px, G.number_of_edges())
    graphs = {"Real terrain": G, "No terrain, same range": unit_disk_graph(sites, px, scenario.max_range_km * 1000),
              "No terrain, same mean degree": matched, "Rewired, same degrees": resilience.rewired_null(G, scenario.seed)}
    pos = {v: (float(sites[v][0] * px), float(sites[v][1] * px)) for v in G.nodes}
    out = resilience.compare(graphs, seeds=body.seeds, demand=net["demand"], gateways=net["gateways"], pos=pos,
                             disk_radius=body.disk_radius_m)
    return dict(graphs=out, seeds=body.seeds, matched_range_m=matched_range, nodes=G.number_of_nodes(),
                range_m=scenario.max_range_km * 1000, disk_radius_m=body.disk_radius_m)


@router.post("/twin")
def run_twin(body: TwinBody):
    net = network_for(body)
    return twin.run(net, body.design.node(), **body.options(net))


@router.post("/night")
def night(body: TwinBody):
    net = network_for(body)
    design = body.design.node()
    options = body.options(net)
    options.pop("night")
    rows = twin.night_design(net, design, batteries=(100, 200, 400, 700, 1000, 1500, 2200, 3200, 5000), **options)
    return dict(budget=night_budget(design), hibernating=night_budget(replace(design, hibernate=True, duty=min(design.duty, 0.1))),
                design=rows, night_hours=LUNAR_NIGHT_H)


@router.post("/beam")
def beam(body: BeamBody):
    design = PowerBeam(wavelength_nm=body.wavelength_nm, tx_aperture_m=body.tx_aperture_m, rx_diameter_m=body.rx_diameter_m,
                       electrical_in_w=body.electrical_in_w)
    optical = Optical()
    return dict(budget=beam_budget(body.distance_m, design),
                links=dict(rf_margin_db=float(rf_margin_db(body.distance_m)), optical_margin_db=float(optical_margin_db(body.distance_m, optical, body.dust_db)),
                           optical_required_db=optical.required_margin_db, optical_rate_mbps=optical.rate_mbps, rf_rate_kbps=128.0),
                curve=[dict(distance_m=d, delivered_w=beam_budget(d, design)["delivered_w"], spot_radius_m=beam_budget(d, design)["spot_radius_m"])
                       for d in (500, 1000, 2000, 3000, 5000, 7500, 10_000, 15_000, 20_000)])


@router.post("/tunnel")
def tunnel(body: TunnelBody):
    if body.layout == "room":
        mine = room_and_pillar(body.rows, body.cols)
    elif body.layout == "drift":
        mine = drift_mine(branches=body.branches, loops=body.loops, seed=body.seed)
    else:
        raise HTTPException(422, "Layout must be drift or room.")
    radio = TunnelRadio(ghz=body.ghz, alpha_db_per_100m=body.alpha_db_per_100m, bend_db_per_90deg=body.bend_db_per_90deg)
    results = breadcrumb.compare(mine, radio)
    for result in results.values():
        result["timeline"] = result["timeline"][::max(1, len(result["timeline"]) // 240)]
        result["worst_collapse"] = min(result["per_collapse"], key=result["per_collapse"].get) if result["per_collapse"] else None
        result["worst_repeater"] = min(result["per_repeater"], key=result["per_repeater"].get) if result["per_repeater"] else None
    return dict(junctions={str(v): mine.G.nodes[v]["pos"].tolist() for v in mine.G}, segments=[[str(a), str(b)] for a, b in mine.sequence()],
                length_m=mine.total_length(), loops=nx.cycle_basis(mine.G).__len__(), policies=results,
                radio=dict(ghz=radio.ghz, alpha_db_per_100m=radio.alpha_db_per_100m, bend_db_per_90deg=radio.bend_db_per_90deg,
                           required_margin_db=radio.required_margin_db))
