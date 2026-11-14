"""The dig twin: a robot follows the dig front and drops repeaters ("breadcrumbs") behind it.

Three drop policies are compared on the same mine:

- threshold: drop when the link margin back to the network falls below a threshold.
- fixed: drop every fixed distance.
- resilient: keep two independent links back to the network, and drop at every junction, so no
  single repeater and no single tunnel is the only way out.

After the dig, every repeater is failed in turn and every tunnel segment is collapsed in turn,
and the share of the mine that can still reach the surface gateway is measured.
"""
from dataclasses import dataclass

import networkx as nx
import numpy as np

from .tunnel import Mine, TunnelRadio, drift_mine, radio_graph, radio_paths, room_and_pillar

POLICIES = ("threshold", "fixed", "resilient")
GATEWAY = "gateway"


def should_drop(link_margin_db, threshold_db=8.0, hysteresis_db=2.0, dropped_recently=False):
    """Breadcrumb rule: drop when margin is inside the hysteresis band above the threshold, unless one was just dropped."""
    return (link_margin_db < threshold_db + hysteresis_db) and not dropped_recently


@dataclass(frozen=True)
class DropPolicy:
    name: str = "threshold"
    threshold_db: float = 8.0
    hysteresis_db: float = 2.0
    spacing_m: float = 70.0  # fixed policy
    min_gap_m: float = 20.0
    links_needed: int = 1  # resilient policy uses 2


def policy(name):
    return DropPolicy(name, links_needed=2 if name == "resilient" else 1)


def _connected(mine, radios, radio, blocked=(), dead=()):
    R = radio_graph(mine, radios, radio, blocked, dead)
    return nx.node_connected_component(R, GATEWAY) if GATEWAY in R else set()


def coverage(mine, radios, radio=TunnelRadio(), blocked=(), dead=()):
    """Share of mine junctions from which a handheld or robot radio reaches the gateway.

    A junction counts when some repeater that is still linked to the gateway is within margin
    along open tunnels. Junctions on the far side of a collapse stay in the count: those are
    the people or machines that most need the link.
    """
    blocked_set = {frozenset(s) for s in blocked}
    linked = _connected(mine, radios, radio, blocked, dead)
    covered, total = 0, 0
    for v in mine.G:
        segment = next(((v, w) for w in mine.G[v] if frozenset((v, w)) not in blocked_set), None)
        if segment is None:
            continue
        total += 1
        live = {name: where for name, where in radios.items() if name in linked}
        paths = radio_paths(mine, {**live, "_client": (segment, 0.0)}, "_client", radio, blocked)
        covered += any(margin >= radio.required_margin_db for _, _, margin in paths.values())
    return covered / max(total, 1)


def dig(mine, drop=DropPolicy(), radio=TunnelRadio(), step_m=10.0):
    """Run the dig: returns the repeaters placed and the robot's link state at every step."""
    order = mine.sequence()
    dug = Mine()
    dug.junction("portal", *mine.G.nodes["portal"]["pos"])
    radios = {GATEWAY: (order[0], 0.0)}
    timeline, since_drop, travelled, count = [], 0.0, 0.0, 0

    def place(segment, t):
        nonlocal count, since_drop
        count += 1
        radios[f"R{count:02}"] = (segment, float(np.clip(t, 0.0, 1.0)))
        since_drop = 0.0

    for a, b in order:
        if b not in dug.G:
            dug.junction(b, *mine.G.nodes[b]["pos"])
        dug.dig(a, b)
        length = dug.G[a][b]["length"]
        if drop.name == "resilient" and dug.G.degree(a) >= 3 and not any(seg[0] == a and t == 0.0 or seg[1] == a and t == 1.0
                                                                         for seg, t in radios.values()):
            place((a, b), 0.0)  # a repeater at every junction covers each arm of it
        steps = max(1, int(np.ceil(length / step_m)))
        previous_t = 0.0
        linked = _connected(dug, radios, radio)
        for i in range(1, steps + 1):
            t = i / steps
            since_drop += length / steps
            travelled += length / steps
            paths = radio_paths(dug, {**{n: w for n, w in radios.items() if n in linked}, "_robot": ((a, b), t)}, "_robot", radio)
            margins = sorted((m for _, _, m in paths.values()), reverse=True)
            good = sum(m >= drop.threshold_db + drop.hysteresis_db for m in margins)
            recently = since_drop < drop.min_gap_m
            if drop.name == "fixed":
                want = since_drop >= drop.spacing_m
            else:
                needed = min(drop.links_needed, max(len(linked), 1))
                reference = margins[needed - 1] if len(margins) >= needed else -np.inf
                want = should_drop(reference, drop.threshold_db, drop.hysteresis_db, recently) or good < needed and not recently
            if want:
                place((a, b), previous_t if drop.name != "fixed" else t)
                linked = _connected(dug, radios, radio)
                paths = radio_paths(dug, {**{n: w for n, w in radios.items() if n in linked}, "_robot": ((a, b), t)}, "_robot", radio)
                margins = sorted((m for _, _, m in paths.values()), reverse=True)
            timeline.append(dict(metres=travelled, connected=bool(margins and margins[0] >= radio.required_margin_db),
                                 margin_db=float(margins[0]) if margins else None, repeaters=count))
            previous_t = t
    return dict(radios=radios, timeline=timeline, repeaters=count,
                connected_fraction=float(np.mean([step["connected"] for step in timeline])))


def stress(mine, radios, radio=TunnelRadio()):
    """Fail every repeater in turn, then collapse every segment in turn. Coverage after each event."""
    base = coverage(mine, radios, radio)
    repeater = {name: coverage(mine, radios, radio, dead=[name]) for name in radios if name != GATEWAY}
    collapse = {}
    for a, b in mine.G.edges:
        if GATEWAY in radios and frozenset(radios[GATEWAY][0]) == frozenset((a, b)):
            continue  # a collapse at the portal seals the mine; no repeater layout can help
        lost = [name for name, (segment, _) in radios.items() if frozenset(segment) == frozenset((a, b))]
        collapse[f"{a}-{b}"] = coverage(mine, radios, radio, blocked=[(a, b)], dead=lost)
    summarize = lambda values: dict(mean=float(np.mean(values)) if values else base, worst=float(np.min(values)) if values else base)
    return dict(coverage=base, repeater_failure=summarize(list(repeater.values())), collapse=summarize(list(collapse.values())),
                per_repeater=repeater, per_collapse=collapse)


def compare(mine, radio=TunnelRadio(), policies=POLICIES, step_m=10.0):
    """Dig the same mine under each policy and stress the result."""
    out = {}
    for name in policies:
        run = dig(mine, policy(name), radio, step_m)
        out[name] = dict(repeaters=run["repeaters"], connected_fraction=run["connected_fraction"], timeline=run["timeline"],
                         radios={k: dict(segment=list(seg), t=t, pos=mine.point(seg, t).tolist()) for k, (seg, t) in run["radios"].items()},
                         **stress(mine, run["radios"], radio))
    return out


def experiment(seeds=10, loops=True, radio=TunnelRadio(), layout="drift"):
    """Monte Carlo over mine layouts: mean and 95% interval of each policy's results."""
    rows = {name: [] for name in POLICIES}
    for seed in range(seeds):
        mine = drift_mine(seed=seed, loops=loops) if layout == "drift" else room_and_pillar(rows=4 + seed % 3, cols=5 + seed % 4)
        for name, result in compare(mine, radio).items():
            rows[name].append([result["repeaters"], result["connected_fraction"], result["coverage"],
                               result["repeater_failure"]["mean"], result["repeater_failure"]["worst"],
                               result["collapse"]["mean"], result["collapse"]["worst"]])
    keys = ("repeaters", "connected_fraction", "coverage", "repeater_failure_mean", "repeater_failure_worst", "collapse_mean", "collapse_worst")
    out = {}
    for name, values in rows.items():
        values = np.array(values)
        half = 1.96 * values.std(axis=0, ddof=1) / np.sqrt(len(values)) if len(values) > 1 else np.zeros(values.shape[1])
        out[name] = {key: dict(mean=float(m), ci95=float(h)) for key, m, h in zip(keys, values.mean(axis=0), half)}
    return out
