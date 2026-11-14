"""Resilience lab: how a relay graph degrades as nodes or links fail.

A failure mode produces a removal order. The curve for an order is computed in one pass by
adding the elements back in reverse with a union-find, tracking the giant-component fraction
and the fraction of demand nodes that still reach a gateway.
"""
import networkx as nx
import numpy as np

NODE_MODES = ("random", "degree", "betweenness", "articulation", "disk")
MODES = NODE_MODES + ("edge",)
STOCHASTIC = {"random", "disk", "edge"}


class _Components:
    def __init__(self, n, demand, gateways):
        self.parent = list(range(n))
        self.size = [0] * n
        self.demand = [0] * n
        self.gateway = [False] * n
        self.is_demand, self.is_gateway = set(demand), set(gateways)
        self.giant = 0
        self.connected_demand = 0

    def find(self, v):
        while self.parent[v] != v:
            self.parent[v] = self.parent[self.parent[v]]
            v = self.parent[v]
        return v

    def activate(self, v):
        self.size[v] = 1
        self.demand[v] = int(v in self.is_demand)
        self.gateway[v] = v in self.is_gateway
        if self.gateway[v]:
            self.connected_demand += self.demand[v]
        self.giant = max(self.giant, 1)

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a == b:
            return
        if self.size[a] < self.size[b]:
            a, b = b, a
        if self.gateway[a] != self.gateway[b]:
            self.connected_demand += self.demand[b] if self.gateway[a] else self.demand[a]
        self.parent[b] = a
        self.size[a] += self.size[b]
        self.demand[a] += self.demand[b]
        self.gateway[a] = self.gateway[a] or self.gateway[b]
        self.giant = max(self.giant, self.size[a])


def _index(G):
    nodes = list(G.nodes)
    lookup = {v: i for i, v in enumerate(nodes)}
    return nodes, lookup, [(lookup[a], lookup[b]) for a, b in G.edges]


def node_curve(G, order, demand=(), gateways=()):
    """Curves after removing 0..N nodes in ``order``: (giant fraction, demand-to-gateway fraction)."""
    nodes, lookup, edges = _index(G)
    n = len(nodes)
    adjacency = [[] for _ in range(n)]
    for a, b in edges:
        adjacency[a].append(b)
        adjacency[b].append(a)
    demand_i = [lookup[v] for v in demand if v in lookup]
    state = _Components(n, demand_i, [lookup[v] for v in gateways if v in lookup])
    alive = [False] * n
    giant, served = np.zeros(n + 1), np.zeros(n + 1)
    for step, v in enumerate(reversed([lookup[v] for v in order])):
        state.activate(v)
        alive[v] = True
        for w in adjacency[v]:
            if alive[w]:
                state.union(v, w)
        giant[n - step - 1] = state.giant / n
        served[n - step - 1] = state.connected_demand / max(len(demand_i), 1)
    return giant, served


def edge_curve(G, order, demand=(), gateways=()):
    """Curves after removing 0..E edges in ``order`` (link degradation). Nodes stay alive."""
    nodes, lookup, _ = _index(G)
    n, m = len(nodes), len(order)
    demand_i = [lookup[v] for v in demand if v in lookup]
    state = _Components(n, demand_i, [lookup[v] for v in gateways if v in lookup])
    for v in range(n):
        state.activate(v)
    giant, served = np.zeros(m + 1), np.zeros(m + 1)
    giant[m], served[m] = state.giant / n, state.connected_demand / max(len(demand_i), 1)
    for step, (a, b) in enumerate(reversed(order)):
        state.union(lookup[a], lookup[b])
        giant[m - step - 1] = state.giant / n
        served[m - step - 1] = state.connected_demand / max(len(demand_i), 1)
    return giant, served


def removal_order(G, mode, seed=0, pos=None, disk_radius=1_000.0, recalc_every=None, protect=()):
    """Order in which ``mode`` removes nodes (or edges for mode "edge").

    ``protect`` nodes (for example gateways and mining sites) are removed last, so curves show
    relay failures first. Targeted modes recalculate after every ``recalc_every`` removals.
    """
    rng = np.random.default_rng(seed)
    if mode == "edge":
        edges = list(G.edges)
        return [edges[i] for i in rng.permutation(len(edges))]
    protect = [v for v in protect if v in G]
    pool = [v for v in G.nodes if v not in set(protect)]
    if mode == "random":
        order = [pool[i] for i in rng.permutation(len(pool))]
    elif mode == "disk":
        if pos is None:
            raise ValueError("Spatially correlated failure needs node positions in metres.")
        xy = np.array([pos[v] for v in pool], float)
        remaining, order = np.ones(len(pool), bool), []
        while remaining.any():
            center = xy[rng.choice(np.flatnonzero(remaining))]
            hit = remaining & (np.hypot(*(xy - center).T) <= disk_radius)
            order += [pool[i] for i in np.flatnonzero(hit)]
            remaining &= ~hit
    elif mode in ("degree", "betweenness", "articulation"):
        H, order = G.subgraph(pool).copy(), []
        batch = recalc_every or max(1, len(pool) // 40)
        while H.number_of_nodes():
            by_degree = sorted(H.degree, key=lambda item: (-item[1], str(item[0])))
            if mode == "degree":
                take = [v for v, _ in by_degree[:batch]]
            elif mode == "betweenness":
                score = nx.betweenness_centrality(H, k=min(60, len(H)), seed=seed)
                take = sorted(score, key=lambda v: (-score[v], str(v)))[:batch]
            else:  # single points of failure first, largest first; then fall back to hubs
                cuts = set(nx.articulation_points(H))
                take = next(([v] for v, _ in by_degree if v in cuts), [v for v, _ in by_degree[:batch]])
            order += take
            H.remove_nodes_from(take)
    else:
        raise ValueError(f"Unknown failure mode {mode}.")
    return order + protect


def schneider_r(curve):
    """Schneider robustness R: mean giant fraction after each removal."""
    return float(np.mean(curve[1:])) if len(curve) > 1 else 0.0


def critical_fraction(curve, threshold=0.5):
    """Smallest removed fraction at which the curve first drops below ``threshold`` of its start."""
    below = np.flatnonzero(curve < threshold * curve[0])
    return float(below[0] / (len(curve) - 1)) if len(below) else 1.0


def monte_carlo(G, mode, seeds=200, demand=(), gateways=(), pos=None, disk_radius=1_000.0, points=41):
    """Mean curve and 95% confidence interval for a failure mode, resampled to ``points`` fractions."""
    runs = seeds if mode in STOCHASTIC else 1
    grid = np.linspace(0, 1, points)
    giants, serveds, rs, criticals = [], [], [], []
    for seed in range(runs):
        order = removal_order(G, mode, seed, pos, disk_radius, protect=tuple(gateways) + tuple(demand))
        giant, served = (edge_curve if mode == "edge" else node_curve)(G, order, demand, gateways)
        x = np.linspace(0, 1, len(giant))
        giants.append(np.interp(grid, x, giant))
        serveds.append(np.interp(grid, x, served))
        rs.append(schneider_r(giant))
        criticals.append(critical_fraction(served if len(demand) else giant))
    def summary(rows):
        rows = np.array(rows)
        half = 1.96 * rows.std(axis=0, ddof=1) / np.sqrt(len(rows)) if len(rows) > 1 else np.zeros(rows.shape[1:])
        return rows.mean(axis=0), half
    giant, giant_ci = summary(giants)
    served, served_ci = summary(serveds)
    r, r_ci = summary(np.array(rs)[:, None])
    critical, critical_ci = summary(np.array(criticals)[:, None])
    return dict(mode=mode, runs=runs, fraction=grid.tolist(), giant=giant.tolist(), giant_ci=giant_ci.tolist(),
                served=served.tolist(), served_ci=served_ci.tolist(), schneider_r=float(r[0]), schneider_r_ci=float(r_ci[0]),
                critical_fraction=float(critical[0]), critical_fraction_ci=float(critical_ci[0]))


def disk_failure(G, pos, center, radius):
    """Copy of G without the nodes within ``radius`` metres of ``center``. pos: {node: (x, y)} in metres."""
    H = G.copy()
    H.remove_nodes_from([v for v, (x, y) in pos.items() if (x - center[0]) ** 2 + (y - center[1]) ** 2 <= radius ** 2])
    return H


def demand_connected(G, demand, gateways):
    """Fraction of demand nodes that can reach any gateway."""
    reach = set()
    for g in gateways:
        if g in G and g not in reach:
            reach |= nx.node_connected_component(G, g)
    return len([d for d in demand if d in reach]) / max(len(demand), 1)


def structural_report(G, demand=(), gateways=(), connectivity=True):
    """Static fragility numbers: cut vertices, bridges, connectivity and disjoint paths to a gateway."""
    connected = G.number_of_nodes() > 0 and nx.is_connected(G)
    report = {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "mean_degree": 2 * G.number_of_edges() / max(G.number_of_nodes(), 1),
        "components": nx.number_connected_components(G),
        "giant_fraction": max((len(c) for c in nx.connected_components(G)), default=0) / max(G.number_of_nodes(), 1),
        "articulation_points": len(list(nx.articulation_points(G))),
        "bridges": len(list(nx.bridges(G))),
        "node_connectivity": (nx.node_connectivity(G) if connectivity and G.number_of_nodes() <= 250 else None) if connected else 0,
        "edge_connectivity": (nx.edge_connectivity(G) if connectivity and G.number_of_nodes() <= 250 else None) if connected else 0,
    }
    if len(demand) and len(gateways):
        report["demand_connected"] = demand_connected(G, demand, gateways)
        report["disjoint_paths"] = disjoint_paths(G, demand, gateways)
    return report


def disjoint_paths(G, demand, gateways):
    """Vertex-disjoint relay paths from each demand node to the gateway set (0 when unreachable).

    The gateways are merged into one target, so paths may end at different gateways. A direct
    link from a demand node to a gateway counts as one path.
    """
    gateways = [g for g in gateways if g in G]
    H = nx.Graph()
    H.add_nodes_from(v for v in G if v not in gateways)
    H.add_node("_gateway")
    merged = lambda v: "_gateway" if v in gateways else v
    H.add_edges_from((merged(a), merged(b)) for a, b in G.edges if merged(a) != merged(b))
    out = {}
    for d in demand:
        if d not in H or not gateways or not nx.has_path(H, d, "_gateway"):
            out[d] = 0
            continue
        direct = H.has_edge(d, "_gateway")
        K = H.copy()
        if direct:
            K.remove_edge(d, "_gateway")
        rest = nx.algorithms.connectivity.local_node_connectivity(K, d, "_gateway") if nx.has_path(K, d, "_gateway") else 0
        out[d] = int(rest) + int(direct)
    return out


def rewired_null(G, seed=0):
    """Degree-preserving random rewiring: the same degrees without the terrain's spatial structure."""
    H = G.copy()
    if H.number_of_edges() > 3:
        try:
            nx.double_edge_swap(H, nswap=10 * H.number_of_edges(), max_tries=1000 * H.number_of_edges(), seed=seed)
        except nx.NetworkXException:
            pass
    return H


def compare(graphs, modes=("random", "betweenness", "articulation", "disk", "edge"), seeds=200, demand=(), gateways=(),
            pos=None, disk_radius=1_000.0):
    """Run every failure mode on every graph. graphs: {label: nx.Graph} sharing node ids."""
    return {label: {"structure": structural_report(G, connectivity=False),
                    "modes": {mode: monte_carlo(G, mode, seeds, demand, gateways, pos, disk_radius) for mode in modes}}
            for label, G in graphs.items()}


# ---- Step-by-step reference interface (the build spec's API). The Monte Carlo above is the
# fast path; these recalculate after every single removal and are handy for small graphs.
def giant_frac(G, n0):
    return max((len(c) for c in nx.connected_components(G)), default=0) / n0


def pick_random(H, rng, **_):
    return rng.choice(list(H.nodes))


def pick_degree(H, **_):
    return max(H.degree, key=lambda x: x[1])[0]


def pick_betweenness(H, seed=0, k=200, **_):
    bc = nx.betweenness_centrality(H, k=min(k, len(H)), seed=seed)
    return max(bc, key=bc.get)


def attack_curve(G, picker, seed=0):
    """Giant fraction after each removal, with the picker re-run on the surviving graph every step."""
    rng = np.random.default_rng(seed)
    n0 = G.number_of_nodes()
    H = G.copy()
    curve = [giant_frac(H, n0)]
    while H.number_of_nodes() > 1:
        H.remove_node(picker(H, rng=rng, seed=seed))
        curve.append(giant_frac(H, n0))
    return np.array(curve)


schneider_R = schneider_r
