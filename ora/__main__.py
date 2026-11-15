"""Command line for ORA. Every number in the pitch should come from one of these commands.

    python -m ora demo                              headline figure: terrain vs unit-disk vs rewired
    python -m ora network    [scenario]             place relays and report the plan
    python -m ora resilience [scenario] [--seeds 200]
    python -m ora twin       [scenario] [--beam] [--night] [--hibernate DUTY]
    python -m ora parts      [scenario]             per-site parts list from the configurator
    python -m ora export     [scenario] [--out file.geojson]
    python -m ora dte                               Earth-visibility map south of 85 S
    python -m ora topologies                        chain vs ring vs ladder vs skip links
    python -m ora tunnel     [--layout drift|room] [--seeds 10]
    python -m ora benchmark  [site] [--budget 8]    siting strategies on held-out lunar sites
    python -m ora earth      [tile]
    python -m ora dem        <file>                 describe an elevation file
    python -m ora run        scenarios/<file>.yaml  run any scenario file by its kind
    python -m ora preflight  [--json] [--no-hash]  inventory data, models, libraries and code revision
    python -m ora accuracy                          accuracy against dense line-of-sight truth on untouched windows
"""
import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "docs" / "figures"


def stamped(report):
    """Attach the code revision and model hashes, so a saved report can be tied to what produced it."""
    from .status import stamp
    if not isinstance(report, dict):   # e.g. the per-window list from `benchmark`: keep it under "results"
        return {"results": report, "provenance": stamp()}
    return {**report, "provenance": stamp()}
DEFAULT = "shackleton_rim_to_floor"


def surface(name, seeds):
    from . import resilience
    from .graph import degree_matched_disk
    from .scenario import build
    net = build(name)
    G, px, plan = net["G"], net["grid"].pixel_size_m, net["plan"]
    matched, _ = degree_matched_disk(net["sites"], px, G.number_of_edges())
    pos = {v: (float(net["sites"][v][0] * px), float(net["sites"][v][1] * px)) for v in G}
    graphs = (("terrain", G), ("same_degree_no_terrain", matched))
    table = {label: {mode: round(resilience.monte_carlo(H, mode, seeds, net["demand"], net["gateways"], pos)["schneider_r"], 3)
                     for mode in ("random", "betweenness", "articulation", "disk", "edge")} for label, H in graphs}
    from .scenario import plan_assessment
    return dict(site=net["site_name"], sites=G.number_of_nodes(), links=G.number_of_edges(), siting=net["score_method"],
                siting_evidence=net["score_evidence"], assessment=plan_assessment(net),
                plan={k: plan[k] for k in ("chosen", "chain", "added", "served", "mean_after_failure", "worst_after_failure", "feasible")},
                gateways=net["gateways"], mines=net["demand"],
                articulation_points={label: resilience.structural_report(H, connectivity=False)["articulation_points"] for label, H in graphs},
                schneider_r=table)


def demo(name, seeds):
    """Headline figure: giant-component fraction against nodes removed, on three graphs over the same sites."""
    from . import resilience
    from .export import line_chart_svg
    from .graph import degree_matched_disk, unit_disk_graph
    from .scenario import build
    net = build(name)
    G, sites, px, scenario = net["G"], net["sites"], net["grid"].pixel_size_m, net["scenario"]
    matched, matched_range = degree_matched_disk(sites, px, G.number_of_edges())
    graphs = {"Real terrain": (G, "#059669"), "Unit disk, same range": (unit_disk_graph(sites, px, scenario.max_range_km * 1000), "#6b7280"),
              "Unit disk, same mean degree": (matched, "#2563eb"), "Rewired, same degrees": (resilience.rewired_null(G, scenario.seed), "#7c3aed")}
    FIGURES.mkdir(parents=True, exist_ok=True)
    summary = dict(scenario=name, sites=G.number_of_nodes(), range_km=scenario.max_range_km, seeds=seeds,
                   matched_range_km=matched_range / 1000, graphs={})
    for mode, title in (("random", "random failures"), ("betweenness", "targeted failures (busiest relay first)")):
        series = []
        for label, (H, color) in graphs.items():
            result = resilience.monte_carlo(H, mode, seeds, net["demand"], net["gateways"])
            series.append(dict(label=label, color=color, x=result["fraction"], y=result["giant"],
                               band=result["giant_ci"] if result["runs"] > 1 else None, dash=label != "Real terrain"))
            entry = summary["graphs"].setdefault(label, dict(links=H.number_of_edges(),
                                                             articulation_points=resilience.structural_report(H, connectivity=False)["articulation_points"]))
            entry[f"schneider_r_{mode}"] = round(result["schneider_r"], 4)
        svg = line_chart_svg(series, f"{net['site_name']}: {title}", "share of nodes removed", "largest connected share of the network")
        (FIGURES / f"ora_headline_{mode}.svg").write_text(svg, encoding="utf-8")
    (FIGURES / "ora_headline.json").write_text(json.dumps(stamped(summary), indent=1) + "\n")
    summary["figures"] = [str(FIGURES / f"ora_headline_{mode}.svg") for mode in ("random", "betweenness")]
    return summary


def dte():
    import numpy as np
    from .earth_vis import dte_map, simple_visibility
    data = dte_map()
    terrain, flat = data["terrain"], data["flat"]
    inside = np.isfinite(terrain)
    return dict(cells=int(inside.sum()), cell_km=float(data["cell_km"]), days=int(data["days"]), samples=int(data["samples"]),
                mean_terrain=float(np.nanmean(terrain)), mean_flat=float(np.nanmean(flat)),
                never_visible_share=float((terrain[inside] == 0).mean()), at_least_half_share=float((terrain[inside] >= 0.5).mean()),
                best_cell=float(np.nanmax(terrain)),
                smooth_sphere_simple_model={f"{lat:g}": simple_visibility(lat) for lat in (0, -60, -85, -89.9)})


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["preflight"]:
        from .status import main as preflight
        return preflight(argv[1:])
    parser = argparse.ArgumentParser(prog="python -m ora", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("demo", "network", "resilience", "twin", "parts", "export", "dte", "topologies", "tunnel",
                                            "earth", "dem", "run", "benchmark", "tall-towers", "evaluate", "stress", "preflight", "accuracy"))
    parser.add_argument("target", nargs="?")
    parser.add_argument("--seeds", type=int, default=None)
    parser.add_argument("--budget", type=int, default=8)
    parser.add_argument("--layout", default="drift")
    parser.add_argument("--out", default=None)
    parser.add_argument("--beam", action="store_true")
    parser.add_argument("--night", action="store_true")
    parser.add_argument("--hibernate", type=float, default=None, metavar="DUTY")
    args = parser.parse_args(argv)
    command, options = args.command, {}
    if command == "run":
        options = yaml.safe_load(Path(args.target).read_text()) or {}
        command = {"tunnel": "tunnel", "earth": "earth"}.get(options.get("kind"), "resilience")
    if command in ("network", "resilience"):
        out = surface(args.target or DEFAULT, args.seeds or (200 if command == "resilience" else 20))
        if command == "network":
            out.pop("schneider_r")
    elif command == "demo":
        out = demo(args.target or DEFAULT, args.seeds or 200)
    elif command == "twin":
        from . import twin
        from .scenario import build
        from .thermal import NodeDesign
        design = NodeDesign() if args.hibernate is None else replace(NodeDesign(), hibernate=True, duty=args.hibernate)
        out = twin.run(build(args.target or DEFAULT), design, beam=args.beam, night=(48.0, 354.0) if args.night else None)["summary"]
    elif command == "parts":
        from . import twin
        from .scenario import build
        out = [dict(id=c["id"], role=c["role"], mass_kg=c["mass_kg"], power_w=c["power_w"], modules=[p["module"] for p in c["parts"]],
                    notes=c["notes"]) for c in twin.node_configs(build(args.target or DEFAULT))]
        if any(c["assumed_modules"] for c in twin.node_configs(build(args.target or DEFAULT))):
            print("WARNING: masses and powers come from ora/modules.yaml placeholders (assumed: true), not datasheets.", file=sys.stderr)
    elif command == "export":
        from .export import write_geojson
        from .scenario import build
        name = args.target or DEFAULT
        out = dict(written=str(write_geojson(build(name), args.out or f"{name}.geojson")))
    elif command == "benchmark":
        import joblib

        from .lunar_windows import build, split
        from .selector import compare
        from .train import MODELS
        _, test = split()
        built = [build(*w) for w in test]
        surrogate, gain = (joblib.load(MODELS / f"{n}.joblib") for n in ("ora_coverage_surrogate", "ora_gain_model"))
        out = [dict(window=list(w), budget=args.budget, **compare(b, surrogate, gain, budget=args.budget)) for w, b in zip(test, built)]
        FIGURES.mkdir(parents=True, exist_ok=True)
        (FIGURES / "ora_benchmark.json").write_text(json.dumps(stamped(out), indent=1) + "\n")
    elif command == "tall-towers":
        from .tall_towers import reproduce
        out = reproduce()
        FIGURES.mkdir(parents=True, exist_ok=True)
        (FIGURES / "ora_tall_towers.json").write_text(json.dumps(stamped(out), indent=1) + "\n")
    elif command == "evaluate":
        from .evaluate import evaluate
        out = evaluate()
        FIGURES.mkdir(parents=True, exist_ok=True)
        (FIGURES / "ora_evaluation.json").write_text(json.dumps(stamped(out), indent=1) + "\n")
    elif command == "accuracy":
        from .accuracy import run
        out = run()
        FIGURES.mkdir(parents=True, exist_ok=True)
        (FIGURES / "ora_accuracy.json").write_text(json.dumps(stamped(out), indent=1, default=float) + "\n")
    elif command == "stress":
        from .stress import run
        out = run()
        FIGURES.mkdir(parents=True, exist_ok=True)
        (FIGURES / "ora_stress.json").write_text(json.dumps(stamped(out), indent=1, default=float) + "\n")
    elif command == "dte":
        out = dte()
    elif command == "topologies":
        from .topologies import table
        out = table()
    elif command == "dem":
        from .loaders import describe
        out = describe(args.target)
    elif command == "tunnel":
        from . import breadcrumb
        from .tunnel import TunnelRadio
        radio = TunnelRadio(**{k: options[k] for k in ("ghz", "alpha_db_per_100m", "bend_db_per_90deg") if k in options})
        out = breadcrumb.experiment(seeds=args.seeds or options.get("seeds", 10), loops=options.get("loops", True), radio=radio,
                                    layout=options.get("layout", args.layout))
    else:
        from .earth import validate
        keys = ("tile", "window_km", "candidates", "max_range_km", "mast_m", "seed")
        out = validate(**({k: options[k] for k in keys if k in options} or ({"tile": args.target} if args.target else {})))
    json.dump(out, sys.stdout, indent=1, default=lambda v: v.tolist() if hasattr(v, "tolist") else str(v))
    print()


if __name__ == "__main__":
    sys.exit(main())
