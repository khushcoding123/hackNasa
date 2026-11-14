"""Chain against loop: why a relay chain is fragile and a few extra links help.

Toy graphs with independent failures and no terrain. They show the shape of the effect only.
"""
import networkx as nx
import numpy as np


def make(N=20):
    """{name: (graph, source, target)} for four ways to join two ends with N nodes."""
    return {
        "chain": (nx.path_graph(N), 0, N - 1),
        "ring": (nx.cycle_graph(N), 0, N // 2),
        "ladder": (nx.ladder_graph(N // 2), 0, N - 1),
        "chain+skip": (nx.Graph([(i, i + 1) for i in range(N - 1)] + [(i, i + 2) for i in range(N - 2)]), 0, N - 1),
    }


def p_connected(G, a, b, p, trials=6000, seed=0):
    """Probability that a still reaches b when every other node fails independently with probability p."""
    rng = np.random.default_rng(seed)
    ok = 0
    for _ in range(trials):
        H = G.copy()
        for v in list(G.nodes):
            if v not in (a, b) and rng.random() < p:
                H.remove_node(v)
        ok += nx.has_path(H, a, b)
    return ok / trials


def table(N=20, probabilities=(0.05, 0.10, 0.20), trials=6000, seed=0):
    """One row per topology: links, single points of failure, bridges and end-to-end reliability."""
    rows = []
    for name, (G, a, b) in make(N).items():
        rows.append(dict(topology=name, nodes=G.number_of_nodes(), links=G.number_of_edges(),
                         articulation_points=len(list(nx.articulation_points(G))), bridges=len(list(nx.bridges(G))),
                         reliability={f"{p:g}": p_connected(G, a, b, p, trials, seed) for p in probabilities}))
    return rows
