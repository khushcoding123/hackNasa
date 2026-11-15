"""Drawn areas: terrain for a polygon on the south polar cap, a mask of its cells, and layout comparison.

A user draws a square or a free-form shape on the Moon. ``register`` checks that it lies inside the 20 m
LOLA product (85 S to the pole), crops terrain around it under a generated site key, and adds that key to the
site tables so the mission engine and the relay lab can both use it. ``analyse`` then compares network layouts
(tree, loop, full mesh) over the relays the planner chose inside the area.
"""
import hashlib

import networkx as nx
import numpy as np

from lunar_core.terrain import polar_stereographic_south, polar_stereographic_south_inverse
from moonmesh import data as moon_data

from . import data as ora_data
from . import resilience

PRODUCT_EDGE_M = 15168 * 20 / 2          # half-width of the 20 m LOLA product
MAX_POLE_DISTANCE_M = PRODUCT_EDGE_M - 14_000
MIN_RADIUS_M, MAX_RADIUS_M, MARGIN_M = 1_000.0, 12_000.0, 6_000.0
KEEP_AREAS = 12


class AreaError(ValueError):
    """The drawn shape cannot be analysed; the message says why in plain words."""


def project(polygon):
    pts = np.asarray(polygon, float)
    if pts.ndim != 2 or pts.shape[1] != 2 or not 3 <= len(pts) <= 200 or not np.isfinite(pts).all():
        raise AreaError("Draw a closed shape with at least three corners.")
    if (pts[:, 0] > -85).any() or (pts[:, 0] < -90).any():
        raise AreaError("Draw the area between 85° S and the south pole, where 20 m terrain exists. Use the 'Go to the pole' button.")
    x, y = polar_stereographic_south(pts[:, 0], pts[:, 1])
    return np.column_stack([x, y])


def centroid(xy):
    """Area-weighted centroid of a simple polygon (falls back to the mean for degenerate shapes)."""
    x, y = xy[:, 0], xy[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y1 - x1 * y
    area = cross.sum() / 2
    if abs(area) < 1e-6:
        return xy.mean(axis=0), 0.0
    return np.array([((x + x1) * cross).sum(), ((y + y1) * cross).sum()]) / (6 * area), abs(area)


def info(polygon):
    xy = project(polygon)
    (cx, cy), area = centroid(xy)
    radius = float(np.hypot(xy[:, 0] - cx, xy[:, 1] - cy).max())
    if np.hypot(cx, cy) + radius > MAX_POLE_DISTANCE_M:
        raise AreaError("That shape reaches past the edge of the 20 m terrain (about 140 km from the pole). Draw it closer to the pole.")
    if radius < MIN_RADIUS_M:
        raise AreaError(f"Draw a larger area: at least about {2 * MIN_RADIUS_M / 1000:.0f} km across.")
    if radius > MAX_RADIUS_M:
        raise AreaError(f"Draw a smaller area: at most about {2 * MAX_RADIUS_M / 1000:.0f} km across, so line of sight can be computed exactly.")
    lat, lon = polar_stereographic_south_inverse(cx, cy)
    return dict(xy=xy, center_xy=(float(cx), float(cy)), area_km2=float(area) / 1e6, radius_m=radius, lat=float(lat), lon=float(lon))


def register(polygon):
    """Crop terrain for the shape under a stable site key. Returns (key, display name, info)."""
    meta = info(polygon)
    rounded = np.round(np.asarray(polygon, float), 4).tolist()
    key = "area-" + hashlib.sha1(repr(rounded).encode()).hexdigest()[:10]
    name = f"Drawn area ({meta['lat']:.2f}°, {meta['lon']:.1f}° E)"
    site = (name, meta["lat"], meta["lon"])
    folder = moon_data.DATA / "terrain"
    folder.mkdir(parents=True, exist_ok=True)
    for suffix, product, extent in (("near20km", "LDEM_85S_20M", max(20_000.0, meta["radius_m"] + MARGIN_M)),
                                    ("far", "LDEM_75S_240M", 180_000.0)):
        path = folder / f"{key}_{suffix}.npz"
        if not path.is_file():
            partial = path.with_suffix(".partial")
            with partial.open("wb") as file:
                np.savez_compressed(file, **moon_data.crop(site, product, extent))
            partial.replace(path)
    moon_data.SITES[key] = site
    ora_data.ALL_SITES[key] = site
    prune(keep=key)
    return key, name, meta


def prune(keep):
    folder = moon_data.DATA / "terrain"
    files = sorted(folder.glob("area-*_near20km.npz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP_AREAS:]:
        stem = old.name.removesuffix("_near20km.npz")
        if stem == keep:
            continue
        for suffix in ("near20km", "far"):
            (folder / f"{stem}_{suffix}.npz").unlink(missing_ok=True)
        moon_data.SITES.pop(stem, None)
        ora_data.ALL_SITES.pop(stem, None)


def polygon_mask(grid, polygon):
    """Boolean raster, True for cells whose centre lies inside the polygon (crossing-number test)."""
    xy = project(polygon)
    px = grid.pixel_size_m
    cols = (xy[:, 0] - grid.x_left) / px - 0.5
    rows = (grid.y_top - xy[:, 1]) / px - 0.5
    mask = np.zeros(grid.heights.shape, bool)
    r0, r1 = max(int(np.floor(rows.min())) - 1, 0), min(int(np.ceil(rows.max())) + 2, mask.shape[0])
    c0, c1 = max(int(np.floor(cols.min())) - 1, 0), min(int(np.ceil(cols.max())) + 2, mask.shape[1])
    yy, xx = np.mgrid[r0:r1, c0:c1]
    inside = np.zeros(yy.shape, bool)
    j = len(cols) - 1
    for i in range(len(cols)):
        xi, yi, xj, yj = cols[i], rows[i], cols[j], rows[j]
        crosses = (yi > yy) != (yj > yy)
        with np.errstate(divide="ignore", invalid="ignore"):
            at = (xj - xi) * (yy - yi) / (yj - yi) + xi
        inside ^= crosses & (xx < at)
        j = i
    mask[r0:r1, c0:c1] = inside
    return mask


# ---------- layouts ----------

def members_graph(net):
    plan = net["plan"]
    members = list(plan["chosen"]) + list(net["gateways"]) + list(net["demand"])
    return net["G"].subgraph(members).copy(), members


def _bridges(G):
    return len(list(nx.bridges(G))) if G.number_of_edges() else 0


def loop_layout(H, tree):
    """Spanning tree plus the extra links that remove the most single-path links, one at a time."""
    G = tree.copy()
    spare = [(a, b) for a, b in H.edges if not G.has_edge(a, b)]
    while spare and _bridges(G):
        best, best_score = None, (_bridges(G), len(list(nx.articulation_points(G))))
        for a, b in spare:
            trial = G.copy()
            trial.add_edge(a, b, **H.edges[a, b])
            score = (_bridges(trial), len(list(nx.articulation_points(trial))))
            if score < best_score or (best is not None and score == best_score and H.edges[a, b]["dist"] < H.edges[best]["dist"]):
                best, best_score = (a, b), score
        if best is None:
            break
        G.add_edge(*best, **H.edges[best])
        spare.remove(best)
    return G


def layout_metrics(G, demand, gateways, relays, seeds=60):
    served_node = [resilience.demand_connected(G.subgraph([n for n in G if n != v]), demand, [g for g in gateways if g != v]) for v in relays] or [1.0]
    served_link = []
    for a, b in list(G.edges):
        trial = G.copy()
        trial.remove_edge(a, b)
        served_link.append(resilience.demand_connected(trial, demand, gateways))
    served_link = served_link or [1.0]
    random_r = resilience.monte_carlo(G, "random", seeds, demand, gateways)["schneider_r"]
    return dict(links=G.number_of_edges(), link_km=sum(d["dist"] for _, _, d in G.edges(data=True)) / 1000,
                nominal_served=resilience.demand_connected(G, demand, gateways),
                single_points_of_failure=len([n for n in nx.articulation_points(G) if n in relays]), single_path_links=_bridges(G),
                worst_after_node_loss=min(served_node), mean_after_node_loss=float(np.mean(served_node)),
                worst_after_link_loss=min(served_link), schneider_r_random=float(random_r))


NAMES = {"tree": "Tree (branches only)", "loop": "Tree with loops", "mesh": "Full mesh"}
BLURB = {"tree": "The fewest links that connect everything. Cheapest, and one break cuts off everything beyond it.",
         "loop": "A tree plus only the links that remove single-path links. Redundancy where it counts.",
         "mesh": "Every line-of-sight link between the chosen sites. Most redundant, most hardware."}


def analyse(net, meta):
    H, members = members_graph(net)
    demand, gateways = list(net["demand"]), list(net["gateways"])
    relays = list(net["plan"]["chosen"]) + gateways
    tree = nx.minimum_spanning_tree(H, weight="dist")
    graphs = {"tree": tree, "loop": loop_layout(H, tree), "mesh": H}
    layouts = {}
    for key, G in graphs.items():
        layouts[key] = dict(name=NAMES[key], blurb=BLURB[key], edges=[[int(a), int(b)] for a, b in G.edges],
                            **layout_metrics(G, demand, gateways, relays))
    # Best format: survives the most single failures, then needs the fewest links, then the least cable.
    def robustness(item):
        m = item[1]
        return (round(min(m["worst_after_node_loss"], m["worst_after_link_loss"]), 6), -m["links"], -m["link_km"])
    best = max(layouts.items(), key=robustness)[0]
    cheapest = layouts["tree"]
    chosen = layouts[best]
    worst = min(chosen["worst_after_node_loss"], chosen["worst_after_link_loss"])
    if not net["gateways"]:
        reason = "No ground inside or within 6 km of this area sees Earth for long enough to act as a gateway, so no layout can reach Earth from here."
    elif not demand:
        reason = "No gentle ground was found for mining sites inside this area."
    else:
        extra = chosen["links"] - cheapest["links"]
        reason = (f"{chosen['name']}: after losing any one relay or link, at least {worst * 100:.0f}% of mining sites stay linked, "
                  f"using {chosen['links']} links{f' ({extra} more than a plain tree)' if extra > 0 else ''}.")
    role = {**{v: "relay" for v in net["plan"]["chosen"]}, **{v: "gateway" for v in gateways}, **{v: "mine" for v in demand}}
    nodes = [dict(id=int(v), role=role[v], lat=float(net["lat"][v]), lon=float(net["lon"][v]), elevation=float(net["ground"][v]),
                  sun=float(net["sun_frac"][v]), earth=float(net["earth_frac"][v]),
                  score=float(net["score"][v]) if v < net["n_relays"] else None,
                  outside=bool(v < net["n_relays"] and v >= net.get("inside", net["n_relays"]))) for v in members]
    plan = net["plan"]
    return dict(site=dict(key=net["scenario"].site, name=net["site_name"], lat=meta["lat"], lon=meta["lon"], area_km2=meta["area_km2"],
                          radius_m=meta["radius_m"]),
                polygon=[list(p) for p in net["scenario"].polygon], nodes=nodes, layouts=layouts, recommended=best, reason=reason,
                score_method=net["score_method"], candidates=int(net["n_relays"]),
                plan=dict(served=plan["served"], worst_after_failure=plan["worst_after_failure"], feasible=bool(plan["feasible"]),
                          chosen=[int(v) for v in plan["chosen"]], gateways=[int(v) for v in gateways], mines=[int(v) for v in demand],
                          unreachable=[int(v) for v in plan["unreachable"]]))
