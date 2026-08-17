"""Weighted community detection, used only to *propose* episode clusters.

The phase spec is precise about the role of this step: community detection
proposes, constraints dispose, and every merge or split is logged.  A cluster
is a hypothesis about which artifacts belong to one initiative arc, and a
hypothesis that nobody can inspect is worthless.

Pair weights
------------
Several edges can connect the same two PRs.  They are combined with a noisy-OR,
``1 - prod(1 - s_i)``, rather than a sum: two independent pieces of evidence
should raise confidence but two copies of the same bookkeeping should not push
the weight past certainty.  The result is bounded in [0, 1], which keeps
modularity well behaved and keeps the number interpretable — 0.91 means "two
strong structural signals agree", not "1.4 units of episode".

Louvain, by hand
----------------
Implemented here rather than imported so it is deterministic: nodes are visited
in sorted order, ties are broken by community id, and there is no RNG in the
hot path.  Two runs on the same edge set produce byte-identical clusters, which
is what makes ``episode_id`` — derived from cluster membership — stable.

Constraints applied after detection
-----------------------------------
* a cluster above ``split_threshold`` is re-clustered on tier A+B edges only;
* a cluster still above ``hard_max`` is split into connected components of
  tier A edges;
* clusters joined only by tier C evidence are dissolved;
* explicit ``part_of`` structure is preserved as sub-episode links rather than
  being flattened away;
* every action lands in the audit log, and low-confidence clusters land in a
  human review queue.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from ..config import Phase2Config, days_between, parse_ts
from ..versions import derivation_version

log = logging.getLogger("impact2.graph.clustering")

VERSION = derivation_version("episode_construction")
TIER_RANK = {"A": 0, "B": 1, "C": 2}


# --------------------------------------------------------------------------
# pair weighting
# --------------------------------------------------------------------------


def combine_strengths(strengths: Sequence[float]) -> float:
    """Noisy-OR combination, bounded in [0, 1]."""
    product = 1.0
    for value in strengths:
        product *= max(0.0, 1.0 - min(1.0, float(value)))
    return round(1.0 - product, 6)


def build_pair_weights(
    pair_edges: Mapping[tuple[int, int], list[dict[str, Any]]],
    config: Phase2Config,
    *,
    merged_at: Mapping[int, Any],
) -> tuple[dict[tuple[int, int], float], list[dict[str, Any]]]:
    """Weighted, filtered PR-pair graph plus the audit trail of what was dropped."""
    min_pair = float(config.get("episodes.clustering.min_pair_strength"))
    tier_c_needs_corroboration = bool(
        config.get("episodes.clustering.tier_c_requires_corroboration")
    )
    max_span = float(config.get("episodes.clustering.max_span_days_for_structural_join"))

    weights: dict[tuple[int, int], float] = {}
    dropped: list[dict[str, Any]] = []

    for pair, edges in sorted(pair_edges.items()):
        usable = [e for e in edges if e.get("usable_for_clustering")]
        if not usable:
            dropped.append(
                {"pair": list(pair), "reason": "all edges guarded out",
                 "guards": sorted({g for e in edges for g in (e.get("guards_applied") or [])}),
                 "edge_types": sorted({str(e.get("edge_type")) for e in edges})}
            )
            continue

        tiers = {str(e.get("tier")) for e in usable}
        if tier_c_needs_corroboration and tiers == {"C"}:
            dropped.append(
                {"pair": list(pair), "reason": "tier C only, uncorroborated",
                 "edge_types": sorted({str(e.get("edge_type")) for e in usable})}
            )
            continue

        if "A" not in tiers:
            span = days_between(merged_at.get(pair[0]), merged_at.get(pair[1]))
            if span is not None and abs(span) > max_span:
                dropped.append(
                    {"pair": list(pair),
                     "reason": f"structural-only join spanning {abs(span):.1f} days "
                               f"(> {max_span})",
                     "edge_types": sorted({str(e.get("edge_type")) for e in usable})}
                )
                continue

        weight = combine_strengths([float(e.get("strength") or 0.0) for e in usable])
        if weight < min_pair:
            dropped.append(
                {"pair": list(pair),
                 "reason": f"combined strength {weight:.3f} < min_pair_strength {min_pair}",
                 "edge_types": sorted({str(e.get("edge_type")) for e in usable})}
            )
            continue
        weights[pair] = weight

    log.info(
        "pair graph: %d pairs kept, %d dropped by guards/thresholds",
        len(weights), len(dropped),
    )
    return weights, dropped


# --------------------------------------------------------------------------
# deterministic weighted Louvain
# --------------------------------------------------------------------------


def louvain(
    weights: Mapping[tuple[int, int], float],
    nodes: Iterable[int],
    *,
    resolution: float = 1.0,
    max_passes: int = 12,
) -> dict[int, int]:
    """Return node -> community id. Deterministic for a given edge set."""
    node_list = sorted(set(nodes))
    if not node_list:
        return {}

    adjacency: dict[int, dict[int, float]] = {n: {} for n in node_list}
    for (a, b), weight in weights.items():
        if a not in adjacency or b not in adjacency or weight <= 0:
            continue
        adjacency[a][b] = adjacency[a].get(b, 0.0) + weight
        adjacency[b][a] = adjacency[b].get(a, 0.0) + weight

    # Level 0: every node is its own community, so an original node's
    # membership *is* its own id. Later levels rewrite this to the aggregated
    # community id, which is why the identity start matters — indexing by
    # position here would desynchronise membership from the level's node ids.
    membership = {n: n for n in node_list}
    current_adjacency = adjacency
    current_nodes = node_list
    current_selfloops: dict[int, float] = {n: 0.0 for n in node_list}

    for level in range(max_passes):
        assignment, improved = _one_level(
            current_nodes, current_adjacency, current_selfloops, resolution
        )
        if not improved:
            break
        # Relabel communities to a dense, deterministic range.
        labels = {c: i for i, c in enumerate(sorted(set(assignment.values())))}
        assignment = {n: labels[c] for n, c in assignment.items()}
        membership = {n: assignment[membership[n]] for n in node_list}

        # Aggregate the graph: one node per community.
        aggregated: dict[int, dict[int, float]] = defaultdict(dict)
        selfloops: dict[int, float] = defaultdict(float)
        for node in current_nodes:
            source = assignment[node]
            selfloops[source] += current_selfloops.get(node, 0.0)
            for neighbour, weight in current_adjacency.get(node, {}).items():
                target = assignment[neighbour]
                if source == target:
                    selfloops[source] += weight / 2.0
                else:
                    aggregated[source][target] = aggregated[source].get(target, 0.0) + weight
        current_nodes = sorted(set(assignment.values()))
        current_adjacency = {n: dict(sorted(aggregated.get(n, {}).items()))
                             for n in current_nodes}
        current_selfloops = {n: selfloops.get(n, 0.0) for n in current_nodes}
        if len(current_nodes) == len(set(membership.values())) and level > 0:
            # Nothing merged this pass beyond relabelling.
            pass

    # Final relabel so ids are stable and small.
    labels = {c: i for i, c in enumerate(sorted(set(membership.values())))}
    return {n: labels[c] for n, c in membership.items()}


def _one_level(
    nodes: Sequence[int],
    adjacency: Mapping[int, Mapping[int, float]],
    selfloops: Mapping[int, float],
    resolution: float,
) -> tuple[dict[int, int], bool]:
    degrees = {
        n: sum(adjacency.get(n, {}).values()) + 2.0 * selfloops.get(n, 0.0)
        for n in nodes
    }
    total = sum(degrees.values())
    if total <= 0:
        return {n: n for n in nodes}, False
    m2 = total   # 2m

    community = {n: n for n in nodes}
    tot = dict(degrees)
    improved_any = False

    for _ in range(20):     # local-moving sweeps
        moved = False
        for node in nodes:
            own = community[node]
            degree = degrees[node]
            # Weights from `node` into each neighbouring community.
            into: dict[int, float] = defaultdict(float)
            for neighbour, weight in adjacency.get(node, {}).items():
                into[community[neighbour]] += weight
            tot[own] -= degree

            best_community, best_gain = own, into.get(own, 0.0) - resolution * tot[own] * degree / m2
            for candidate in sorted(into):
                if candidate == own:
                    continue
                gain = into[candidate] - resolution * tot[candidate] * degree / m2
                if gain > best_gain + 1e-12 or (
                    abs(gain - best_gain) <= 1e-12 and candidate < best_community
                ):
                    best_community, best_gain = candidate, gain
            tot[best_community] += degree
            community[node] = best_community
            if best_community != own:
                moved = True
                improved_any = True
        if not moved:
            break
    return community, improved_any


# --------------------------------------------------------------------------
# constraints
# --------------------------------------------------------------------------


def connected_components(
    members: Iterable[int], weights: Mapping[tuple[int, int], float]
) -> list[list[int]]:
    member_set = set(members)
    parent = {n: n for n in member_set}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b) in weights:
        if a in member_set and b in member_set:
            ra, rb = find(a), find(b)
            if ra != rb:
                lo, hi = sorted((ra, rb))
                parent[hi] = lo

    groups: dict[int, list[int]] = defaultdict(list)
    for node in sorted(member_set):
        groups[find(node)].append(node)
    return [sorted(g) for _, g in sorted(groups.items())]


def apply_constraints(
    clusters: Mapping[int, list[int]],
    pair_edges: Mapping[tuple[int, int], list[dict[str, Any]]],
    weights: Mapping[tuple[int, int], float],
    config: Phase2Config,
) -> tuple[list[list[int]], list[dict[str, Any]]]:
    """Split catch-all clusters; log every action."""
    split_threshold = int(config.get("episodes.clustering.split_threshold"))
    hard_max = int(config.get("episodes.clustering.hard_max"))

    def tier_weights(allowed: set[str]) -> dict[tuple[int, int], float]:
        out: dict[tuple[int, int], float] = {}
        for pair, edges in pair_edges.items():
            usable = [
                e for e in edges
                if e.get("usable_for_clustering") and str(e.get("tier")) in allowed
            ]
            if usable:
                out[pair] = combine_strengths(
                    [float(e.get("strength") or 0.0) for e in usable]
                )
        return out

    ab_weights = tier_weights({"A", "B"})
    a_weights = tier_weights({"A"})

    final: list[list[int]] = []
    audit: list[dict[str, Any]] = []

    for cluster_index, members in sorted(clusters.items()):
        members = sorted(members)
        if len(members) <= split_threshold:
            final.append(members)
            audit.append(
                {
                    "action": "keep",
                    "cluster_index": cluster_index,
                    "members": members,
                    "size": len(members),
                    "reason": f"size {len(members)} <= split_threshold {split_threshold}",
                    "episode_construction_version": VERSION,
                }
            )
            continue

        # First remedy: drop tier C support and re-cluster on A+B only.
        sub = {
            pair: w for pair, w in ab_weights.items()
            if pair[0] in set(members) and pair[1] in set(members)
        }
        parts = connected_components(members, sub)
        audit.append(
            {
                "action": "split",
                "cluster_index": cluster_index,
                "members": members,
                "size": len(members),
                "reason": (
                    f"size {len(members)} > split_threshold {split_threshold}; "
                    f"re-clustered on tier A+B edges only"
                ),
                "result_sizes": [len(p) for p in parts],
                "episode_construction_version": VERSION,
            }
        )

        for part in parts:
            if len(part) <= hard_max:
                final.append(part)
                continue
            # Second remedy: connected components of deterministic evidence only.
            sub_a = {
                pair: w for pair, w in a_weights.items()
                if pair[0] in set(part) and pair[1] in set(part)
            }
            hard_parts = connected_components(part, sub_a)
            audit.append(
                {
                    "action": "split",
                    "cluster_index": cluster_index,
                    "members": part,
                    "size": len(part),
                    "reason": (
                        f"still {len(part)} > hard_max {hard_max} after the tier A+B "
                        "pass; split into connected components of tier A edges only"
                    ),
                    "result_sizes": [len(p) for p in hard_parts],
                    "episode_construction_version": VERSION,
                }
            )
            for hard_part in hard_parts:
                if len(hard_part) > hard_max:
                    audit.append(
                        {
                            "action": "flag_oversized",
                            "cluster_index": cluster_index,
                            "members": hard_part,
                            "size": len(hard_part),
                            "reason": (
                                f"{len(hard_part)} PRs remain connected by tier A "
                                "evidence alone; kept intact and queued for human "
                                "review rather than split arbitrarily"
                            ),
                            "episode_construction_version": VERSION,
                        }
                    )
                final.append(hard_part)

    return [sorted(c) for c in final], audit


def index_pairs_by_pr(
    pair_edges: Mapping[tuple[int, int], list[dict[str, Any]]]
) -> dict[int, list[tuple[int, int]]]:
    """PR number -> the pairs it participates in.

    Without this, every per-episode lookup scans the whole pair graph. At ~30k
    pairs and ~10k episodes that is 300M iterations and dominates the run.
    """
    index: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for pair in pair_edges:
        index[pair[0]].append(pair)
        index[pair[1]].append(pair)
    return dict(index)


def internal_pairs(
    members: Sequence[int],
    pair_edges: Mapping[tuple[int, int], list[dict[str, Any]]],
    pair_index: Mapping[int, Sequence[tuple[int, int]]] | None = None,
) -> list[tuple[int, int]]:
    """The pairs with both ends inside ``members``."""
    member_set = set(members)
    if pair_index is None:
        return [
            pair for pair in pair_edges
            if pair[0] in member_set and pair[1] in member_set
        ]
    seen: set[tuple[int, int]] = set()
    for number in member_set:
        for pair in pair_index.get(number, ()):
            if pair[0] in member_set and pair[1] in member_set:
                seen.add(pair)
    return sorted(seen)


def cluster_confidence(
    members: Sequence[int],
    pair_edges: Mapping[tuple[int, int], list[dict[str, Any]]],
    *,
    merged_at: Mapping[int, Any],
    pair_index: Mapping[int, Sequence[tuple[int, int]]] | None = None,
) -> tuple[float, list[str]]:
    """How much to trust that these PRs really are one initiative.

    Driven by the *kind* of evidence holding the cluster together, never by its
    size — a two-PR cluster joined by a closing reference is far more certain
    than a nine-PR cluster joined by lexical similarity.
    """
    if len(members) == 1:
        return 1.0, ["single-PR episode: no clustering decision was made"]

    member_set = set(members)
    pairs = internal_pairs(members, pair_edges, pair_index)
    internal = [
        edge
        for pair in pairs
        for edge in pair_edges[pair]
        if edge.get("usable_for_clustering")
    ]
    if not internal:
        return 0.25, ["cluster has no usable internal edge"]

    tiers = [str(e.get("tier")) for e in internal]
    tier_a_share = tiers.count("A") / len(tiers)
    reasons: list[str] = []

    # Every member should be reachable by tier A or B evidence.
    ab_pairs = {
        pair for pair in pairs
        if any(e.get("usable_for_clustering") and str(e.get("tier")) in {"A", "B"}
               for e in pair_edges[pair])
    }
    components = connected_components(members, {p: 1.0 for p in ab_pairs})
    fully_connected = len(components) == 1

    confidence = 0.35 + 0.45 * tier_a_share
    if fully_connected:
        confidence += 0.20
    else:
        reasons.append(
            f"members fall into {len(components)} groups under tier A/B evidence; "
            "tier C similarity is holding parts of this cluster together"
        )

    spans = [
        abs(days_between(merged_at.get(a), merged_at.get(b)) or 0.0)
        for a in members for b in members if a < b
    ]
    span = max(spans) if spans else 0.0
    if span > 60:
        confidence -= 0.15
        reasons.append(f"cluster spans {span:.0f} days")
    if len(members) > 8:
        confidence -= 0.10
        reasons.append(f"{len(members)} PRs is a large arc for one initiative")

    if tier_a_share >= 0.6:
        reasons.append(
            f"{tier_a_share:.0%} of internal edges are deterministic (tier A)"
        )
    return round(min(1.0, max(0.0, confidence)), 4), reasons


def sub_episode_links(
    members: Sequence[int],
    pair_edges: Mapping[tuple[int, int], list[dict[str, Any]]],
    pair_index: Mapping[int, Sequence[tuple[int, int]]] | None = None,
) -> list[dict[str, Any]]:
    """Preserve explicit ``part_of`` structure inside a cluster.

    Flattening "part 3 of the migration" into an undifferentiated blob loses
    exactly the structure the author bothered to state.
    """
    links: list[dict[str, Any]] = []
    for pair in internal_pairs(members, pair_edges, pair_index):
        for edge in pair_edges[pair]:
            if str(edge.get("edge_type")) in {"part_of", "stacked_branch", "depends_on"}:
                links.append(
                    {
                        "child_pr": int(edge["source_key"]),
                        "parent_pr": int(edge["target_key"]),
                        "relation": str(edge["edge_type"]),
                        "evidence": edge.get("evidence"),
                    }
                )
    return links


def summarise(clusters: Sequence[Sequence[int]], audit: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sizes = sorted(len(c) for c in clusters)
    actions: dict[str, int] = defaultdict(int)
    for entry in audit:
        actions[str(entry.get("action"))] += 1
    return {
        "clusters": len(clusters),
        "prs_clustered": sum(sizes),
        "singletons": sum(1 for s in sizes if s == 1),
        "multi_pr_clusters": sum(1 for s in sizes if s > 1),
        "largest": sizes[-1] if sizes else 0,
        "median_size": sizes[len(sizes) // 2] if sizes else 0,
        "audit_actions": dict(sorted(actions.items())),
        "episode_construction_version": VERSION,
    }
