"""Open formats: write a network as GeoJSON, and draw the headline figure as a plain SVG.

GeoJSON schema (one FeatureCollection):
- Point features, one per site. Properties: id, role (candidate | relay | gateway | mine),
  elevation_m, sun_fraction, earth_fraction, siting_score (relays only), chosen (bool).
- LineString features, one per link in the chosen network. Properties: a, b, distance_m,
  margin_db, kind.
Coordinates are [east longitude, latitude] in degrees on the Moon (IAU MOON_ME), not WGS 84.
The collection carries a ``crs_note`` member saying so, because GeoJSON has no lunar CRS field.
"""
import json


def to_geojson(network):
    plan, G = network["plan"], network["G"]
    role = {**{v: "candidate" for v in range(network["n_relays"])}, **{v: "relay" for v in plan["chosen"]},
            **{v: "gateway" for v in network["gateways"]}, **{v: "mine" for v in network["demand"]}}
    members = set(plan["chosen"]) | set(network["gateways"]) | set(network["demand"])
    point = lambda v: [float(network["lon"][v]), float(network["lat"][v])]
    features = [dict(type="Feature", geometry=dict(type="Point", coordinates=point(v)),
                     properties=dict(id=int(v), role=role[v], elevation_m=float(network["ground"][v]),
                                     sun_fraction=float(network["sun_frac"][v]), earth_fraction=float(network["earth_frac"][v]),
                                     siting_score=float(network["score"][v]) if v < network["n_relays"] else None,
                                     chosen=v in members)) for v in G.nodes]
    features += [dict(type="Feature", geometry=dict(type="LineString", coordinates=[point(a), point(b)]),
                      properties=dict(a=int(a), b=int(b), distance_m=d["dist"], margin_db=d.get("margin"), kind=d.get("kind", "rf")))
                 for a, b, d in G.subgraph(members).edges(data=True)]
    return dict(type="FeatureCollection", name=network["scenario"].name,
                crs_note="Lunar planetocentric coordinates (IAU MOON_ME), east longitude; not WGS 84.", features=features)


def write_geojson(network, path):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(to_geojson(network), file, indent=1)
    return path


def line_chart_svg(series, title, x_label, y_label, width=860, height=480):
    """Minimal dependency-free line chart. series: [dict(label, color, x, y, band=None, dash=False)]."""
    left, right, top, bottom = 70, width - 24, 56, height - 92
    x_max = max(max(s["x"]) for s in series)
    px = lambda x: left + x / x_max * (right - left)
    py = lambda y: bottom - min(max(y, 0), 1) * (bottom - top)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" font-family="system-ui,sans-serif" font-size="13">',
           f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
           f'<text x="{left}" y="30" font-size="17" font-weight="600" fill="#111827">{title}</text>']
    for i in range(5):
        y = top + (bottom - top) * i / 4
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        out.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" fill="#4b5563">{1 - i / 4:.2f}</text>')
        out.append(f'<text x="{px(x_max * i / 4):.1f}" y="{bottom + 20}" text-anchor="middle" fill="#4b5563">{x_max * i / 4:.2f}</text>')
    out.append(f'<text x="{(left + right) / 2}" y="{bottom + 42}" text-anchor="middle" fill="#374151">{x_label}</text>')
    out.append(f'<text transform="translate(18 {(top + bottom) / 2}) rotate(-90)" text-anchor="middle" fill="#374151">{y_label}</text>')
    for s in series:
        if s.get("band"):
            upper = [f"{px(x):.1f},{py(y + b):.1f}" for x, y, b in zip(s["x"], s["y"], s["band"])]
            lower = [f"{px(x):.1f},{py(y - b):.1f}" for x, y, b in zip(s["x"], s["y"], s["band"])][::-1]
            out.append(f'<polygon points="{" ".join(upper + lower)}" fill="{s["color"]}" fill-opacity="0.18"/>')
        points = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in zip(s["x"], s["y"]))
        dash = ' stroke-dasharray="7 5"' if s.get("dash") else ""
        out.append(f'<polyline points="{points}" fill="none" stroke="{s["color"]}" stroke-width="2.5"{dash}/>')
    for i, s in enumerate(series):
        x = left + (i % 2) * 400
        y = height - 34 + (i // 2) * 20
        dash = ' stroke-dasharray="7 5"' if s.get("dash") else ""
        out.append(f'<line x1="{x}" y1="{y - 4}" x2="{x + 28}" y2="{y - 4}" stroke="{s["color"]}" stroke-width="3"{dash}/>')
        out.append(f'<text x="{x + 36}" y="{y}" fill="#111827">{s["label"]}</text>')
    out.append("</svg>")
    return "\n".join(out)
