"""Drawn areas: terrain for a polygon on the south polar cap, a mask of its cells, and layout comparison.

A user draws a square or a free-form shape on the Moon. ``register`` checks that it lies inside the 20 m
LOLA product (85 S to the pole), crops terrain around it under a generated site key, and adds that key to the
site tables so the mission engine and the relay lab can both use it. ``analyse`` then compares network layouts
(tree, loop, full mesh) over the relays the planner chose inside the area.
"""
import hashlib

import networkx as nx
import numpy as np

from lunar_core.terrain import local_frame, polar_stereographic_south, polar_stereographic_south_inverse, set_frame
from moonmesh import data as moon_data
from moonmesh import globaldem

from . import data as ora_data
from . import resilience

EDGE_20M = 15168 * 20 / 2      # half-width of the 20 m LOLA product (85 S to the pole)
EDGE_240M = 3812 * 240 / 2     # half-width of the 240 m product (75 S to the pole)
MIN_RADIUS_M = 100.0
MAX_GRID_PX = 2100             # terrain is thinned so the working grid never exceeds this many pixels across
KEEP_AREAS = 12
POLAR_LAT = -75.5              # shapes entirely south of this use the polar LOLA products; others use the global LDEM_64
MAX_SPAN_M = 2_000_000.0       # largest radius a flat local projection represents well (about 60 degrees of arc)


class AreaError(ValueError):
    """The drawn shape cannot be analysed; the message says why in plain words."""


def project(polygon):
    pts = np.asarray(polygon, float)
    if pts.ndim != 2 or pts.shape[1] != 2 or not 3 <= len(pts) <= 200 or not np.isfinite(pts).all():
        raise AreaError("Draw a closed shape with at least three corners.")
    if (pts[:, 0] > 90).any() or (pts[:, 0] < -90).any():
        raise AreaError("Latitudes must be between 90° S and 90° N.")
    x, y = polar_stereographic_south(pts[:, 0], pts[:, 1])
    return np.column_stack([x, y])


def frame_for(polygon):
    """Local projection for shapes that reach north of the polar terrain products; None keeps the polar projection."""
    pts = np.asarray(polygon, float)
    if pts.ndim != 2 or pts.shape[1] != 2 or not np.isfinite(pts).all():
        raise AreaError("Draw a closed shape with at least three corners.")
    if (pts[:, 0] <= POLAR_LAT).all():
        return None
    if not globaldem.available():
        raise AreaError("Global terrain is not installed, so only the area south of 75° S can be analysed. Use the 'Go to the pole' button.")
    la, lo = np.radians(pts[:, 0]), np.radians(pts[:, 1])
    mean = np.array([(np.cos(la) * np.cos(lo)).mean(), (np.cos(la) * np.sin(lo)).mean(), np.sin(la).mean()])
    if np.linalg.norm(mean) < 1e-6:
        raise AreaError("That shape wraps around the Moon. Draw a smaller area.")
    mean /= np.linalg.norm(mean)
    return local_frame(float(np.degrees(np.arcsin(mean[2]))), float(np.degrees(np.arctan2(mean[1], mean[0]))))


def centroid(xy):
    """Area-weighted centroid of a simple polygon (falls back to the mean for degenerate shapes)."""
    x, y = xy[:, 0], xy[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y1 - x1 * y
    area = cross.sum() / 2
    if abs(area) < 1e-6:
        return xy.mean(axis=0), 0.0
    return np.array([((x + x1) * cross).sum(), ((y + y1) * cross).sum()]) / (6 * area), abs(area)


def ring_for(radius_m):
    """Width of the ring around a shape where gateway-only and spare sites may be placed."""
    return max(6_000.0, 0.25 * radius_m)


def info(polygon):
    """Size, centre and terrain plan for a shape. Any size is accepted: terrain is thinned to keep the grid workable."""
    frame = frame_for(polygon)
    set_frame(frame)
    xy = project(polygon)
    (cx, cy), area = centroid(xy)
    radius = float(np.hypot(xy[:, 0] - cx, xy[:, 1] - cy).max())
    reach = float(np.hypot(xy[:, 0], xy[:, 1]).max())
    if frame is None and reach > EDGE_240M - 1_000:
        raise AreaError("That shape reaches past the edge of the polar terrain data (about 75° S). Draw it closer to the pole.")
    if frame is not None and reach > MAX_SPAN_M:
        raise AreaError("That shape is too large to analyse as one area (over about 4,000 km across). Draw a smaller one.")
    if radius < MIN_RADIUS_M:
        raise AreaError(f"Draw a larger area: at least about {2 * MIN_RADIUS_M:.0f} m across.")
    if frame is not None:
        product, native = "LOLA LDEM_64 (global)", float(globaldem.PIXEL_M)
    else:
        product, native = ("LDEM_85S_20M", 20.0) if reach <= EDGE_20M - 1_000 else ("LDEM_75S_240M", 240.0)
    ring = ring_for(radius)
    extent = radius + ring + 1_500.0   # margin so sites at the edge of the ring still have terrain under them
    if native == 20.0:
        extent = max(20_000.0, extent)
    elif frame is not None:
        extent = max(40_000.0, extent)
    stride = max(1, int(np.ceil(2 * extent / (native * MAX_GRID_PX))))
    lat, lon = polar_stereographic_south_inverse(cx, cy)
    return dict(xy=xy, center_xy=(float(cx), float(cy)), area_km2=float(area) / 1e6, radius_m=radius, lat=float(lat), lon=float(lon),
                product=product, native_m=native, extent_m=float(extent), stride=stride, resolution_m=native * stride, ring_m=ring,
                frame=frame, projection="polar" if frame is None else "local")


def thin(crop, stride):
    """Block-average a terrain crop by ``stride`` so large areas stay computable."""
    if stride <= 1:
        return crop
    heights = crop["heights"]
    rows, cols = (heights.shape[0] // stride) * stride, (heights.shape[1] // stride) * stride
    blocks = heights[:rows, :cols].reshape(rows // stride, stride, cols // stride, stride)
    with np.errstate(all="ignore"):
        coarse = np.nanmean(blocks, axis=(1, 3)).astype(np.float32)
    return dict(heights=coarse, x_left=crop["x_left"], y_top=crop["y_top"], pixel_size_m=crop["pixel_size_m"] * stride)


def register(polygon):
    """Crop terrain for the shape under a stable site key. Returns (key, display name, info)."""
    meta = info(polygon)
    rounded = np.round(np.asarray(polygon, float), 4).tolist()
    key = "area-" + hashlib.sha1((repr(rounded) + ("" if meta["frame"] is None else "|local")).encode()).hexdigest()[:10]
    name = f"Drawn area ({meta['lat']:.2f}°, {meta['lon']:.1f}° E)"
    site = (name, meta["lat"], meta["lon"])
    folder = moon_data.DATA / "terrain"
    folder.mkdir(parents=True, exist_ok=True)
    local = meta["frame"] is not None
    if local:
        far_extent = max(180_000.0, meta["extent_m"] + 150_000.0)
        plans = (("near20km", None, meta["extent_m"], meta["resolution_m"]),
                 ("far", None, far_extent, max(globaldem.PIXEL_M, 2 * far_extent / MAX_GRID_PX)))
    else:
        plans = (("near20km", meta["product"], meta["extent_m"], meta["stride"]), ("far", "LDEM_75S_240M", 180_000.0, 1))
    for suffix, product, extent, stride in plans:
        path = folder / f"{key}_{suffix}.npz"
        if not path.is_file():
            partial = path.with_suffix(".partial")
            data = globaldem.crop(extent, stride) if local else thin(moon_data.crop(site, product, extent), stride)
            if local and suffix == "near20km":
                data.update(frame=meta["frame"], name=name, lat=meta["lat"], lon=meta["lon"])
            with partial.open("wb") as file:
                np.savez_compressed(file, **data)
            partial.replace(path)
    if local:
        moon_data.SITES[key] = site
        moon_data.LOCAL.add(key)
    else:
        moon_data.SITES[key] = site   # only polar crops can be drawn by the Moon viewer's polar terrain tiles
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
    from .scenario import plan_assessment
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
        reason = "No ground inside or just around this area sees Earth for long enough to act as a gateway, so no layout can reach Earth from here."
    elif not demand:
        reason = "No gentle ground was found for mining sites inside this area."
    elif net["plan"]["served"] <= 0:
        reason = (f"No chain of relays within line of sight links the mining sites to a gateway: all {len(demand)} are cut off. "
                  "Line of sight is rare across big areas. Draw a smaller area around the mining ground and a nearby high rim.")
    else:
        extra = chosen["links"] - cheapest["links"]
        reason = (f"{chosen['name']}: after losing any one relay or link, at least {worst * 100:.0f}% of mining sites stay linked, "
                  f"using {chosen['links']} links{f' ({extra} more than a plain tree)' if extra > 0 else ''}."
                  + ("" if net["plan"]["served"] >= 1 else f" Only {net['plan']['served'] * 100:.0f}% of mining sites can be reached at all in this area."))
    role = {**{v: "relay" for v in net["plan"]["chosen"]}, **{v: "gateway" for v in gateways}, **{v: "mine" for v in demand}}
    nodes = [dict(id=int(v), role=role[v], lat=float(net["lat"][v]), lon=float(net["lon"][v]), elevation=float(net["ground"][v]),
                  sun=float(net["sun_frac"][v]), earth=float(net["earth_frac"][v]),
                  score=float(net["score"][v]) if v < net["n_relays"] else None,
                  outside=bool(v < net["n_relays"] and net["outside"][v])) for v in members]
    plan = net["plan"]
    return dict(site=dict(key=net["scenario"].site, name=net["site_name"], lat=meta["lat"], lon=meta["lon"], area_km2=meta["area_km2"],
                          radius_m=meta["radius_m"], resolution_m=meta["resolution_m"], ring_m=meta["ring_m"], projection=meta["projection"], source=meta["product"]),
                polygon=[list(p) for p in net["scenario"].polygon], nodes=nodes, layouts=layouts, recommended=best, reason=reason,
                score_method=net["score_method"], score_evidence=net.get("score_evidence"), assessment=plan_assessment(net),
                candidates=int(net["n_relays"]), cost=plan["cost"],
                plan=dict(served=plan["served"], worst_after_failure=plan["worst_after_failure"], feasible=bool(plan["feasible"]),
                          chosen=[int(v) for v in plan["chosen"]], gateways=[int(v) for v in gateways], mines=[int(v) for v in demand],
                          unreachable=[int(v) for v in plan["unreachable"]]))
