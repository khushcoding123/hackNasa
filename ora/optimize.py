"""Choose the relay set: the model proposes, the line-of-sight graph decides.

Stage 1 connects every mining site to a gateway along the cheapest chain in the real
line-of-sight graph. Stage 2 greedily adds relays that most improve what survives a single
failure. The learned siting score only breaks ties and steers chains toward good ground.
The result is a heuristic: the best set evaluated, never a proven optimum.
"""
import heapq

import networkx as nx

from .resilience import demand_connected


def connect(G, demand, gateways, cost):
    """Relays on the cheapest chain from each demand node to any gateway. cost: {node: >= 0}."""
    best, parent, heap = {g: 0.0 for g in gateways if g in G}, {}, [(0.0, i, g) for i, g in enumerate(gateways) if g in G]
    heapq.heapify(heap)
    counter = len(heap)
    while heap:
        d, _, v = heapq.heappop(heap)
        if d > best.get(v, float("inf")):
            continue
        for w in G[v]:
            step = d + 1.0 + cost.get(w, 0.0)
            if step < best.get(w, float("inf")):
                best[w], parent[w] = step, v
                counter += 1
                heapq.heappush(heap, (step, counter, w))
    chosen, unreachable = set(), []
    for d in demand:
        if d not in best:
            unreachable.append(d)
            continue
        v = d
        while v in parent:
            v = parent[v]
            chosen.add(v)
    return chosen - set(demand) - set(gateways), unreachable


def survivability(G, chosen, demand, gateways):
    """Demand served now, and the mean and worst demand served after any single relay or gateway fails."""
    members = list(chosen) + list(gateways)
    H = G.subgraph(members + list(demand))
    served = demand_connected(H, demand, gateways)
    after = []
    for failed in members:
        after.append(demand_connected(G.subgraph([v for v in members if v != failed] + list(demand)), demand,
                                      [g for g in gateways if g != failed]))
    mean = sum(after) / len(after) if after else 0.0
    return served, mean, (min(after) if after else 0.0)


def greedy_place(candidates, G_full, demand, gateways, k, score_prior, alpha=0.5, chosen=()):
    """Greedily add up to ``k`` relays maximizing coverage plus single-failure survival, prior as tie-break."""
    chosen, history = list(chosen), []

    def objective(members):
        served, mean, worst = survivability(G_full, members, demand, gateways)
        return served + alpha * (0.5 * mean + 0.5 * worst), served, mean, worst

    base = objective(chosen)
    reach = set().union(*(nx.node_connected_component(G_full, g) for g in gateways if g in G_full)) if gateways else set()
    pool = [c for c in candidates if c in reach]
    for _ in range(k):
        best, best_value = None, None
        linked = set(chosen) | set(gateways) | set(demand)
        for c in pool:
            if c in chosen or not any(w in linked for w in G_full[c]):
                continue
            value = objective(chosen + [c])
            gain = value[0] - base[0] + 0.02 * score_prior.get(c, 0.0)
            if best_value is None or gain > best_value[0]:
                best, best_value = c, (gain, value)
        if best is None or best_value[1][0] <= base[0] + 1e-9:
            break
        chosen.append(best)
        base = best_value[1]
        history.append(dict(added=best, served=base[1], mean_after_failure=base[2], worst_after_failure=base[3]))
    return chosen, history


def place(G_full, demand, gateways, candidates, score_prior, extra=4, alpha=0.5, shade_penalty=None):
    """Full placement: connect, then add ``extra`` redundancy relays. Returns a dict with the audit trail."""
    penalty = shade_penalty or {}
    cost = {c: 0.6 * (1.0 - score_prior.get(c, 0.0)) + penalty.get(c, 0.0) for c in candidates}
    usable = G_full.subgraph(list(candidates) + list(demand) + list(gateways))
    chain, unreachable = connect(usable, demand, gateways, cost)
    tie_break = {c: score_prior.get(c, 0.0) - penalty.get(c, 0.0) for c in candidates}
    chosen, history = greedy_place(candidates, usable, demand, gateways, extra, tie_break, alpha, sorted(chain))
    served, mean, worst = survivability(usable, chosen, demand, gateways)
    return dict(chosen=chosen, chain=sorted(chain), added=[step["added"] for step in history], history=history,
                unreachable=unreachable, served=served, mean_after_failure=mean, worst_after_failure=worst,
                feasible=served >= 1.0 - 1e-9)
