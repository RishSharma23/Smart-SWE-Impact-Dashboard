"""Candidate episode edges between pull requests.

PostHog ships continuously behind feature flags and in small PRs, so "one PR"
is rarely "one feature".  This module reconstructs candidate *episodes*: sets
of PRs that plausibly belong to one piece of work.

Every edge carries an explicit type, the deterministic evidence that produced
it, a strength band and its source.  Nothing is asserted as fact -- an edge is
a claim Phase 2 can accept, weight, or drop.

Strength bands:
    strong  GitHub itself created the link (closing reference, timeline
            connected event) -- no inference involved.
    medium  an explicit artefact number plus edge language in the body.
    weak    a shared feature flag or shared issue with no direct reference.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from ..hashing import edge_id
from ..versions import feature_version

EDGE_TYPES = (
    "closes_issue", "references_pr", "follow_up", "part_of", "stacked_on",
    "reverts", "reapplies", "supersedes", "shared_issue", "shared_feature_flag",
    "timeline_connected",
)

# Body phrase -> edge type. Phrases only upgrade an existing number reference;
# a phrase with no number cannot point anywhere.
PHRASE_TO_EDGE = {
    "follow_up": "follow_up",
    "part_of": "part_of",
    "stacked_on": "stacked_on",
    "reverts": "reverts",
    "reapplies": "reapplies",
    "supersedes": "supersedes",
}


def build_edges(
    *,
    prs: Mapping[int, Mapping[str, Any]],
    references: Iterable[Mapping[str, Any]],
    feature_flags: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    refs = list(references)
    flags = list(feature_flags)
    known_prs = set(prs)

    # Phrases present in each PR's own text, used to type a numeric reference.
    phrases_by_pr: dict[int, set[str]] = defaultdict(set)
    for ref in refs:
        if ref.get("reference_kind") == "edge_phrase" and ref.get("source_number"):
            phrases_by_pr[int(ref["source_number"])].add(str(ref["reference_value"]))

    edges: list[dict[str, Any]] = []

    def add(
        source: int, target: int | str, edge_type: str, strength: str,
        source_name: str, evidence: str, target_kind: str = "pull_request",
    ) -> None:
        if target_kind == "pull_request" and str(target) == str(source):
            return
        edges.append(
            {
                "edge_id": edge_id(edge_type, f"pr/{source}", f"{target_kind}/{target}"),
                "source_pr_number": source,
                "source_pr_id": (prs.get(source) or {}).get("pr_id"),
                "target_kind": target_kind,
                "target_number": int(target) if str(target).isdigit() else None,
                "target_pr_in_dataset": (
                    int(target) in known_prs if str(target).isdigit() and target_kind == "pull_request" else None
                ),
                "edge_type": edge_type,
                "strength": strength,
                "evidence_source": source_name,
                "evidence": evidence[:400],
                "episode_edges_version": feature_version("episode_edges"),
            }
        )

    # -- reference-derived edges ----------------------------------------
    for ref in refs:
        if ref.get("reference_kind") != "issue_or_pr":
            continue
        # `references` carries rows sourced from BOTH pull requests and issues.
        # An episode edge is a claim about a PR, and its column is literally
        # `source_pr_number`, so an issue-sourced row must not become one --
        # doing so made issue #1 look like PR #1 and produced 1,472 edges
        # pointing at a pull request that does not exist. Issue->PR references
        # are already preserved in the `references` table.
        if ref.get("source_kind") != "pull_request":
            continue
        source_number = ref.get("source_number")
        value = str(ref.get("reference_value") or "")
        if source_number is None or not value.isdigit():
            continue
        source_number = int(source_number)
        target_number = int(value)
        subtype = str(ref.get("reference_subtype") or "")
        field = str(ref.get("source_field") or "")
        evidence = str(ref.get("evidence") or "")

        target_kind = "pull_request" if target_number in known_prs else "issue"

        if subtype == "github_closing_reference":
            add(source_number, target_number, "closes_issue", "strong",
                "github_closing_reference", evidence, "issue")
            continue
        if subtype.startswith("timeline_"):
            add(source_number, target_number, "timeline_connected", "strong",
                subtype, evidence, target_kind)
            continue
        if subtype == "closing":
            add(source_number, target_number, "closes_issue", "strong",
                f"closing_keyword:{field}", evidence,
                "issue" if target_kind == "issue" else "pull_request")
            continue

        # A plain mention becomes a typed edge only when the PR's own text also
        # carries the matching phrase.
        typed = False
        for phrase in phrases_by_pr.get(source_number, set()):
            if phrase in PHRASE_TO_EDGE:
                add(source_number, target_number, PHRASE_TO_EDGE[phrase], "medium",
                    f"phrase:{phrase}+{subtype}", evidence, target_kind)
                typed = True
        if not typed:
            add(source_number, target_number, "references_pr", "medium",
                f"mention:{field}", evidence, target_kind)

    # -- shared feature flag --------------------------------------------
    by_flag: dict[str, set[int]] = defaultdict(set)
    for flag in flags:
        key = str(flag.get("flag_key") or "")
        number = flag.get("pr_number")
        if key and number is not None:
            by_flag[key].add(int(number))
    for key, numbers in sorted(by_flag.items()):
        # A flag touched by half the repository is not evidence of an episode.
        if len(numbers) < 2 or len(numbers) > 40:
            continue
        ordered = sorted(numbers)
        for index, source in enumerate(ordered):
            for target in ordered[index + 1 :]:
                add(source, target, "shared_feature_flag", "weak",
                    "feature_flag_cooccurrence", f"both touch feature flag '{key}'")

    # -- shared issue ----------------------------------------------------
    by_issue: dict[int, set[int]] = defaultdict(set)
    for edge in edges:
        if edge["edge_type"] == "closes_issue" and edge["target_number"]:
            by_issue[edge["target_number"]].add(edge["source_pr_number"])
    for issue_number, numbers in sorted(by_issue.items()):
        if len(numbers) < 2 or len(numbers) > 25:
            continue
        ordered = sorted(numbers)
        for index, source in enumerate(ordered):
            for target in ordered[index + 1 :]:
                add(source, target, "shared_issue", "weak",
                    "issue_cooccurrence", f"both close issue #{issue_number}")

    # Deduplicate, keeping the strongest edge for each (source, target, type).
    rank = {"strong": 0, "medium": 1, "weak": 2}
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for edge in edges:
        key = (
            edge["source_pr_number"], edge["target_kind"],
            edge["target_number"], edge["edge_type"],
        )
        current = best.get(key)
        if current is None or rank[edge["strength"]] < rank[current["strength"]]:
            best[key] = edge
    return sorted(
        best.values(),
        key=lambda e: (e["source_pr_number"], e["edge_type"], e["target_number"] or 0),
    )


# Edge types that assert a *work* relationship, and are therefore allowed to
# merge two PRs into one episode.
#
# `references_pr` and `timeline_connected` are deliberately excluded. A bare
# mention ("see #123") and GitHub's CrossReferencedEvent both mean only "these
# two were linked", which is not a claim that they are one piece of work.
# Chaining them transitively produced episodes of 581 and 496 PRs -- an
# artifact of connected components over a promiscuous relation, not a feature.
# Both remain in `candidate_episode_edges`; they just do not group.
GROUPING_EDGE_TYPES = frozenset(
    {"follow_up", "part_of", "stacked_on", "reverts", "reapplies", "supersedes"}
)


def build_episodes(edges: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Connected components over typed, medium-or-stronger PR-to-PR edges.

    Weak edges are excluded too: a shared feature flag is worth recording as an
    edge but is far too promiscuous to define an episode.
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            lo, hi = sorted((ra, rb))
            parent[hi] = lo

    used: list[Mapping[str, Any]] = []
    for edge in edges:
        if edge.get("target_kind") != "pull_request":
            continue
        if edge.get("strength") == "weak":
            continue
        if str(edge.get("edge_type")) not in GROUPING_EDGE_TYPES:
            continue
        if not edge.get("target_pr_in_dataset"):
            continue
        target = edge.get("target_number")
        if target is None:
            continue
        union(int(edge["source_pr_number"]), int(target))
        used.append(edge)

    groups: dict[int, set[int]] = defaultdict(set)
    for number in list(parent):
        groups[find(number)].add(number)

    out: list[dict[str, Any]] = []
    for root, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        member_list = sorted(members)
        types = sorted(
            {
                str(e["edge_type"]) for e in used
                if int(e["source_pr_number"]) in members
            }
        )
        out.append(
            {
                "episode_id": f"episode/{root}",
                "pr_numbers": member_list,
                "pr_count": len(member_list),
                "edge_types": types,
                "root_pr_number": member_list[0],
                "episode_edges_version": feature_version("episode_edges"),
            }
        )
    return out


def summarise(
    edges: Iterable[Mapping[str, Any]], episodes: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    edge_list = list(edges)
    episode_list = list(episodes)
    by_type: dict[str, int] = {}
    by_strength: dict[str, int] = {}
    for edge in edge_list:
        by_type[str(edge["edge_type"])] = by_type.get(str(edge["edge_type"]), 0) + 1
        by_strength[str(edge["strength"])] = by_strength.get(str(edge["strength"]), 0) + 1
    sizes = [e["pr_count"] for e in episode_list]
    return {
        "edges": len(edge_list),
        "by_type": dict(sorted(by_type.items())),
        "by_strength": dict(sorted(by_strength.items())),
        "episodes": len(episode_list),
        "prs_in_episodes": sum(sizes),
        "largest_episode": max(sizes) if sizes else 0,
        "median_episode_size": sorted(sizes)[len(sizes) // 2] if sizes else 0,
    }
