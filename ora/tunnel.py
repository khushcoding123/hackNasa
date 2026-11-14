"""Underground: a mine as a tunnel graph, and radio links that follow the tunnels.

Radio does not go through rock, so a link between two radios follows the tunnels between them.
Its loss is a free-space term for the path length, a per-metre waveguide term and a penalty for
every bend. The three parameters are meant to be fitted from a bench test (see hardware/), not
trusted from a textbook: the defaults are placeholders for a 2.4 GHz link in a narrow tunnel.
"""
import heapq
from dataclasses import dataclass

import networkx as nx
import numpy as np

from .links import fspl_db


@dataclass(frozen=True)
class TunnelRadio:
    ghz: float = 2.4
    tx_dbm: float = 20.0
    gains_db: float = 4.0  # both antennas together
    sensitivity_dbm: float = -95.0
    alpha_db_per_100m: float = 4.0  # waveguide loss
    bend_db_per_90deg: float = 14.0  # loss for a right-angle bend
    required_margin_db: float = 8.0


def path_loss_db(length_m, bend_deg, radio=TunnelRadio()):
    return fspl_db(length_m, radio.ghz) + radio.alpha_db_per_100m * length_m / 100 + radio.bend_db_per_90deg * bend_deg / 90


def margin_db(length_m, bend_deg, radio=TunnelRadio()):
    return radio.tx_dbm + radio.gains_db - path_loss_db(length_m, bend_deg, radio) - radio.sensitivity_dbm


class Mine:
    """Tunnel graph with junction coordinates in metres. Edges are straight tunnel segments."""

    def __init__(self):
        self.G = nx.Graph()
        self.order = []  # segments in the order they are dug
        self._turns = {}

    def junction(self, v, x, y, z=0.0):
        self.G.add_node(v, pos=np.array([x, y, z], float))

    def dig(self, a, b):
        length = float(np.linalg.norm(self.G.nodes[a]["pos"] - self.G.nodes[b]["pos"]))
        self.G.add_edge(a, b, length=length)
        self.order.append((a, b))

    def turn(self, u, v, w):
        """Direction change in degrees when passing through junction v from u to w (cached)."""
        key = (u, v, w)
        if key not in self._turns:
            nodes = self.G.nodes
            self._turns[key] = _turn_deg(nodes[u]["pos"], nodes[v]["pos"], nodes[w]["pos"])
        return self._turns[key]

    def point(self, segment, t):
        a, b = segment
        return self.G.nodes[a]["pos"] * (1 - t) + self.G.nodes[b]["pos"] * t

    def total_length(self):
        return float(sum(d["length"] for _, _, d in self.G.edges(data=True)))

    def sequence(self, start="portal"):
        """Dig order made physically valid: every segment starts from tunnel that already exists."""
        pending, dug, out = list(self.order), {start}, []
        while pending:
            index = next((i for i, (a, b) in enumerate(pending) if a in dug or b in dug), None)
            if index is None:
                raise ValueError("The mine has tunnels that never connect to the portal.")
            a, b = pending.pop(index)
            out.append((a, b) if a in dug else (b, a))
            dug |= {a, b}
        return out

    @classmethod
    def from_survey(cls, stations, legs, portal=None):
        """Build from a cave or mine survey: stations {name: (x, y, z)} in metres and legs [(a, b), ...]."""
        mine = cls()
        for name, xyz in stations.items():
            mine.junction(name, *xyz)
        first = portal if portal is not None else next(iter(stations))
        if first != "portal":
            mine.G = nx.relabel_nodes(mine.G, {first: "portal"})
        for a, b in legs:
            a, b = ("portal" if a == first else a), ("portal" if b == first else b)
            if a != b and not mine.G.has_edge(a, b):
                mine.dig(a, b)
        return mine


def _turn_deg(p, q, r):
    u, v = q - p, r - q
    cos = float(np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-12))
    return float(np.degrees(np.arccos(np.clip(cos, -1, 1))))


def radio_paths(mine, radios, source, radio=TunnelRadio(), blocked=()):
    """Best tunnel path from ``source`` to every other radio: {name: (length_m, bend_deg, margin_db)}.

    radios: {name: (segment, t)} positions along tunnel segments, t in 0..1. blocked: collapsed
    segments that nothing passes. The search minimizes waveguide plus bend loss over directed
    tunnel segments, so turns at junctions are counted.
    """
    blocked = {frozenset(s) for s in blocked}
    G = mine.G
    on_segment = {}
    for name, (segment, t) in radios.items():
        on_segment.setdefault(frozenset(segment), []).append((name, segment, t))
    cost = lambda length, bend: radio.alpha_db_per_100m * length / 100 + radio.bend_db_per_90deg * bend / 90 + 1e-6 * length
    best = {}

    def reach(name, length, bend):
        value = cost(length, bend)
        if name != source and (name not in best or value < best[name][0]):
            best[name] = (value, length, bend)

    (sa, sb), st = radios[source]
    if frozenset((sa, sb)) in blocked:
        return {}
    seg_len = G[sa][sb]["length"]
    for name, segment, t in on_segment[frozenset((sa, sb))]:
        t_same = t if segment == (sa, sb) else 1 - t
        reach(name, abs(t_same - st) * seg_len, 0.0)
    # State: (arrived at junction v, coming from junction u).
    heap, seen, counter = [], {}, 0
    for u, v, length in ((sb, sa, st * seg_len), (sa, sb, (1 - st) * seg_len)):
        heapq.heappush(heap, (cost(length, 0.0), counter, v, u, length, 0.0))
        counter += 1
    while heap:
        value, _, v, u, length, bend = heapq.heappop(heap)
        if seen.get((v, u), float("inf")) <= value:
            continue
        seen[(v, u)] = value
        for w in G[v]:
            if w == u or frozenset((v, w)) in blocked:
                continue
            # A radio standing in a junction looks straight down every tunnel that meets there,
            # so no bend is charged for leaving the source's junction or reaching one at this junction.
            turn = 0.0 if length == 0.0 else mine.turn(u, v, w)
            edge_len = G[v][w]["length"]
            for name, segment, t in on_segment.get(frozenset((v, w)), ()):
                along = t if segment[0] == v else 1 - t
                reach(name, length + along * edge_len, bend + (turn if along > 0 else 0.0))
            heapq.heappush(heap, (cost(length + edge_len, bend + turn), counter, w, v, length + edge_len, bend + turn))
            counter += 1
    return {name: (length, bend, float(margin_db(max(length, 0.5), bend, radio))) for name, (_, length, bend) in best.items()}


def radio_graph(mine, radios, radio=TunnelRadio(), blocked=(), dead=()):
    """Graph of radios joined where the tunnel-path margin meets the requirement."""
    alive = {name: where for name, where in radios.items() if name not in set(dead) and frozenset(where[0]) not in
             {frozenset(s) for s in blocked}}
    R = nx.Graph()
    R.add_nodes_from(alive)
    for name in alive:
        for other, (length, bend, margin) in radio_paths(mine, alive, name, radio, blocked).items():
            if margin >= radio.required_margin_db:
                R.add_edge(name, other, length=length, bend=bend, margin=margin)
    return R


def drift_mine(branches=4, main_m=600, branch_m=220, loops=True, seed=0, segment_m=60, depth_m=-40.0):
    """A decline to a main drift with side branches, optionally cross-cut into loops. Dug portal-first."""
    rng = np.random.default_rng(seed)
    mine = Mine()
    mine.junction("portal", 0, 0, 0)
    previous, x = "portal", 0.0
    main = []
    for i in range(int(main_m / segment_m)):
        x += segment_m
        name = f"m{i}"
        mine.junction(name, x, rng.normal(0, 4), depth_m * min(1.0, (i + 1) / 3))
        mine.dig(previous, name)
        main.append(name)
        previous = name
    tips = []
    spots = np.linspace(2, len(main) - 1, branches).astype(int)
    for k, index in enumerate(spots):
        side = 1 if k % 2 == 0 else -1
        previous, base = main[index], mine.G.nodes[main[index]]["pos"]
        chain = []
        for j in range(int(branch_m / segment_m)):
            name = f"b{k}_{j}"
            mine.junction(name, base[0] + rng.normal(0, 5) + 12 * j, base[1] + side * segment_m * (j + 1), base[2])
            mine.dig(previous, name)
            chain.append(name)
            previous = name
        tips.append(chain)
    if loops:  # cross-cuts between the ends of same-side neighbouring branches give a second way out
        for k in range(len(tips) - 2):
            mine.dig(tips[k][-1], tips[k + 2][-1])
    return mine


def room_and_pillar(rows=5, cols=7, spacing_m=30.0):
    """A room-and-pillar panel, the common layout of Earth coal and salt mines: a grid of entries and cross-cuts."""
    mine = Mine()
    mine.junction("portal", -spacing_m * 2, 0, 0)
    for r in range(rows):
        for c in range(cols):
            mine.junction(f"r{r}c{c}", c * spacing_m, (r - rows // 2) * spacing_m, -8.0)
    mine.dig("portal", f"r{rows // 2}c0")
    for c in range(cols):  # advance the face column by column, as a panel is mined
        for r in range(rows):
            if c > 0:
                mine.dig(f"r{r}c{c - 1}", f"r{r}c{c}")
            if r > 0:
                mine.dig(f"r{r - 1}c{c}", f"r{r}c{c}")
    return mine
