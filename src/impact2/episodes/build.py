"""Impact episodes: the unit of analysis.

An episode is a connected initiative arc — motivating issue, implementation
PRs, consequential reviews, rollout/feature flags, follow-up corrections,
documentation, and downstream adoption.  This module turns a proposed cluster
into a record with a problem, an intervention, an observable outcome, a status,
and — crucially — an ``episode_artifacts`` list in which every single supporting
link is named, typed and provenanced.

The narrative fields are *derived*, never invented.  ``problem`` comes from the
linked issue's title or the PR body's problem statement; ``intervention`` comes
from what the diff actually did; ``observable_outcome`` comes from status and
release evidence.  Each is emitted with the artifact IDs it was read from, so
the claim layer can attach a URL to every sentence that reaches the UI.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from ..config import Phase2Config, iso, parse_ts
from ..ids import (
    comment_artifact, commit_artifact, episode_id, file_artifact, flag_artifact,
    issue_artifact, pr_artifact, url_for_issue, url_for_pr,
)
from ..graph import clustering
from ..versions import derivation_version
from . import status as status_mod

log = logging.getLogger("impact2.episodes")

VERSION = derivation_version("episode_construction")

# Sentences that state a problem rather than describe a change.  Used to pull a
# problem statement out of a PR body when there is no linked issue.
PROBLEM_MARKERS = (
    "problem", "issue is", "currently", "today we", "the bug", "users are",
    "users can't", "users cannot", "this breaks", "fails when", "regression",
    "we don't", "we do not", "there is no", "it's not possible", "pain point",
    "motivation", "context",
)

MECHANICAL_FLAGS = ("is_lockfile", "is_generated", "is_snapshot", "is_vendor",
                    "is_binary_asset")


def _clean(text: str, limit: int = 400) -> str:
    """Strip markdown noise so a derived sentence reads like a sentence."""
    cleaned = re.sub(r"```.*?```", " ", text or "", flags=re.DOTALL)
    cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", cleaned)
    cleaned = re.sub(r"[#*_>`]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _problem_statement(
    prs: Sequence[Mapping[str, Any]], issues: Sequence[Mapping[str, Any]]
) -> tuple[str, list[str]]:
    """Prefer the linked issue; fall back to problem-shaped body sentences."""
    if issues:
        issue = issues[0]
        return (
            _clean(str(issue.get("title") or ""), 240),
            [issue_artifact(int(issue["issue_number"]))],
        )
    for pr in prs:
        body = _clean(str(pr.get("body_text") or ""), 4000)
        for sentence in _sentences(body):
            lowered = sentence.lower()
            if any(marker in lowered for marker in PROBLEM_MARKERS) and len(sentence) > 40:
                return sentence[:300], [pr_artifact(int(pr["pr_number"]))]
    return (
        "No problem statement is recorded in the linked issue or PR bodies.",
        [],
    )


def _intervention(
    prs: Sequence[Mapping[str, Any]], files: Sequence[Mapping[str, Any]]
) -> tuple[str, list[str]]:
    """Describe what changed, from the diff — not from the title's claim."""
    if not files:
        titles = "; ".join(_clean(str(p.get("title_raw") or ""), 120) for p in prs[:3])
        return (
            f"No file-level diff is available in the clone. Titles state: {titles}",
            [pr_artifact(int(p["pr_number"])) for p in prs[:3]],
        )
    by_component: dict[str, int] = defaultdict(int)
    added = modified = deleted = 0
    for row in files:
        by_component[str(row.get("component") or "unknown")] += 1
        status_code = str(row.get("change_status") or "")
        if status_code == "A":
            added += 1
        elif status_code == "D":
            deleted += 1
        else:
            modified += 1
    top = sorted(by_component.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    component_text = ", ".join(f"{name}" for name, _ in top)
    parts: list[str] = []
    if added:
        parts.append(f"added {added} file(s)")
    if modified:
        parts.append(f"modified {modified}")
    if deleted:
        parts.append(f"removed {deleted}")
    return (
        f"Across {len(prs)} pull request(s) the work {', '.join(parts)} in "
        f"{component_text}.",
        [pr_artifact(int(p["pr_number"])) for p in prs[:5]],
    )


def _outcome(status_record: Mapping[str, Any]) -> tuple[str, list[str]]:
    status = str(status_record.get("status"))
    corroboration = str(status_record.get("release_corroboration"))
    evidence = status_record.get("release_evidence") or []
    phrases = {
        "shipped_observable": "The change landed on the default branch",
        "partial_or_behind_flag": "The change landed but remains behind a feature flag",
        "reverted": "The change was reverted",
        "superseded": "The change was superseded by later work",
        "maintenance": "The change is routine maintenance",
        "exploratory": "Nothing from this arc landed",
        "unknown": "The outcome could not be established from the available evidence",
    }
    base = phrases.get(status, phrases["unknown"])
    if corroboration == "corroborated" and evidence:
        detail = "; ".join(str(e.get("detail")) for e in evidence[:2])
        return f"{base}. Release corroborated by: {detail}.", []
    return (
        f"{base}. Release is not independently corroborated — merging to the "
        "default branch is not proof that users saw the change.",
        [],
    )


def build_episodes(
    *,
    config: Phase2Config,
    clusters: Sequence[Sequence[int]],
    pair_edges: Mapping[tuple[int, int], list[dict[str, Any]]],
    edges: Sequence[Mapping[str, Any]],
    prs: Mapping[int, Mapping[str, Any]],
    files_by_pr: Mapping[int, list[Mapping[str, Any]]],
    issues: Mapping[int, Mapping[str, Any]],
    flags_by_pr: Mapping[int, list[Mapping[str, Any]]],
    regression_by_pr: Mapping[int, Mapping[str, Any]],
    change_shape_by_pr: Mapping[int, Mapping[str, Any]],
    blast_by_pr: Mapping[int, Mapping[str, Any]],
    commits_by_pr: Mapping[int, list[Mapping[str, Any]]],
    interventions_by_pr: Mapping[int, list[Mapping[str, Any]]],
    downstream_counts: Mapping[str, int],
    window_end: Any,
    qualifier: str,
    repo_url: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (episodes, episode_artifacts, review_queue)."""
    merged_at = {n: parse_ts(p.get("merged_at")) for n, p in prs.items()}
    # Built once: without it every per-episode pair lookup scans the whole pair
    # graph, which is ~300M iterations on this repository.
    pair_index = clustering.index_pairs_by_pr(pair_edges)
    review_threshold = float(
        config.get("episodes.clustering.review_queue_confidence_below")
    )

    # Issue links per PR, from the deterministic closing edges.
    issues_by_pr: dict[int, set[int]] = defaultdict(set)
    superseding: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if edge.get("source_kind") != "pull_request":
            continue
        source = int(edge["source_key"])
        if edge.get("target_kind") == "issue" and edge.get("edge_type") == "closes_issue":
            try:
                issues_by_pr[source].add(int(edge["target_key"]))
            except (TypeError, ValueError):
                continue
        if edge.get("edge_type") in {"supersedes", "reverts", "reapplies"}:
            try:
                superseding[int(edge["target_key"])].append(dict(edge))
            except (TypeError, ValueError):
                continue

    episodes: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []

    for members in clusters:
        members = sorted(members)
        eid = episode_id(members, qualifier)
        member_prs = [dict(prs[n]) for n in members if n in prs]
        if not member_prs:
            continue

        episode_files = [f for n in members for f in files_by_pr.get(n, [])]
        episode_flags = [f for n in members for f in flags_by_pr.get(n, [])]
        episode_commits = [c for n in members for c in commits_by_pr.get(n, [])]
        episode_interventions = [
            i for n in members for i in interventions_by_pr.get(n, [])
        ]
        linked_issue_numbers = sorted({i for n in members for i in issues_by_pr.get(n, set())})
        linked_issues = [issues[i] for i in linked_issue_numbers if i in issues]
        regression_rows = [regression_by_pr[n] for n in members if n in regression_by_pr]
        supersede_edges = [e for n in members for e in superseding.get(n, [])]

        status_record = status_mod.classify(
            config=config,
            prs=member_prs,
            files=episode_files,
            flags=episode_flags,
            regression_rows=regression_rows,
            superseded_by=supersede_edges,
            linked_issues=linked_issues,
            downstream_adoption_count=int(downstream_counts.get(eid, 0)),
            window_end=window_end,
        )

        confidence, confidence_reasons = clustering.cluster_confidence(
            members, pair_edges, merged_at=merged_at, pair_index=pair_index
        )
        sub_links = clustering.sub_episode_links(members, pair_edges, pair_index)

        starts = [
            parse_ts(p.get("created_at")) for p in member_prs if p.get("created_at")
        ] + [parse_ts(i.get("created_at")) for i in linked_issues if i.get("created_at")]
        ends = [merged_at.get(n) for n in members if merged_at.get(n)]
        start = min([s for s in starts if s], default=None)
        end = max([e for e in ends if e], default=None)

        components: dict[str, int] = defaultdict(int)
        for row in episode_files:
            components[str(row.get("component") or "unknown")] += 1
        products = sorted({c for c in components if c.startswith("product:")})

        problem, problem_evidence = _problem_statement(member_prs, linked_issues)
        intervention, intervention_evidence = _intervention(member_prs, episode_files)
        outcome, outcome_evidence = _outcome(status_record)

        # Title: the linked issue, else the largest PR's subject. Never a
        # generated phrase with no source.
        if linked_issues:
            title = _clean(str(linked_issues[0].get("title") or ""), 160)
            title_source = issue_artifact(int(linked_issues[0]["issue_number"]))
        else:
            anchor = max(
                member_prs,
                key=lambda p: (len(files_by_pr.get(int(p["pr_number"]), [])),
                               -int(p["pr_number"])),
            )
            title = _clean(str(anchor.get("title_raw") or ""), 160)
            title_source = pr_artifact(int(anchor["pr_number"]))

        counterevidence = _counterevidence(regression_rows, status_record, member_prs,
                                           change_shape_by_pr)

        reach_bands = [
            str((blast_by_pr.get(n) or {}).get("reachability_band") or "unknown")
            for n in members
        ]
        band_rank = ["local", "component", "cross_product", "platform_wide"]
        known = [b for b in reach_bands if b in band_rank]
        episode_reach = (
            max(known, key=band_rank.index) if known else "unknown"
        )

        episodes.append(
            {
                "episode_id": eid,
                "title": title,
                "title_evidence": [title_source],
                "problem": problem,
                "problem_evidence": problem_evidence,
                "intervention": intervention,
                "intervention_evidence": intervention_evidence,
                "observable_outcome": outcome,
                "outcome_evidence": outcome_evidence,
                "started_at": iso(start),
                "ended_at": iso(end),
                "duration_days": (
                    round((end - start).total_seconds() / 86400.0, 2)
                    if start and end else None
                ),
                "status": status_record["status"],
                "status_reasons": status_record["status_reasons"],
                "release_corroboration": status_record["release_corroboration"],
                "release_evidence": status_record["release_evidence"],
                "pr_numbers": members,
                "pr_count": len(members),
                "issue_numbers": linked_issue_numbers,
                "components": sorted(components),
                "component_histogram": dict(sorted(components.items())),
                "products": products,
                "reachability_band": episode_reach,
                "file_count": len(episode_files),
                "production_file_count": sum(
                    1 for f in episode_files
                    if not any(f.get(flag) for flag in MECHANICAL_FLAGS)
                    and not f.get("is_test") and not f.get("is_docs")
                ),
                "test_file_count": sum(1 for f in episode_files if f.get("is_test")),
                "doc_file_count": sum(1 for f in episode_files if f.get("is_docs")),
                "feature_flag_keys": sorted(
                    {str(f.get("flag_key")) for f in episode_flags if f.get("flag_key")}
                ),
                "commit_count": len(episode_commits),
                "review_intervention_count": sum(
                    1 for i in episode_interventions if i.get("is_intervention_candidate")
                ),
                "cluster_confidence": confidence,
                "cluster_confidence_reasons": confidence_reasons,
                "sub_episode_links": sub_links,
                "counterevidence": counterevidence,
                "ranked": status_mod.is_ranked_status(status_record["status"]),
                "ranking_eligible_prs": sorted(
                    n for n in members if (prs.get(n) or {}).get("ranking_eligible")
                ),
                "has_ai_co_author": any(
                    c.get("has_ai_co_author") for c in episode_commits
                ),
                "touches_enterprise_licensed_code": any(
                    str((change_shape_by_pr.get(n) or {}).get(
                        "touches_enterprise_licensed_code")) == "True"
                    or (change_shape_by_pr.get(n) or {}).get(
                        "touches_enterprise_licensed_code") is True
                    for n in members
                ),
                "episode_construction_version": VERSION,
            }
        )

        artifacts.extend(
            _episode_artifacts(
                eid, member_prs, linked_issues, episode_commits, episode_files,
                episode_flags, episode_interventions, repo_url,
            )
        )

        if confidence < review_threshold or len(members) > int(
            config.get("episodes.clustering.split_threshold")
        ):
            review_queue.append(
                {
                    "episode_id": eid,
                    "title": title,
                    "pr_numbers": members,
                    "pr_count": len(members),
                    "cluster_confidence": confidence,
                    "reasons": confidence_reasons,
                    "internal_edge_types": sorted(
                        {
                            str(e.get("edge_type"))
                            for pair in clustering.internal_pairs(
                                members, pair_edges, pair_index
                            )
                            for e in pair_edges[pair]
                        }
                    ),
                    "pr_urls": [url_for_pr(n, repo_url) for n in members],
                    "human_verdict": None,
                    "human_notes": None,
                }
            )

    log.info(
        "episodes: %d built, %d queued for human review",
        len(episodes), len(review_queue),
    )
    return episodes, artifacts, review_queue


def _counterevidence(
    regression_rows: Sequence[Mapping[str, Any]],
    status_record: Mapping[str, Any],
    prs: Sequence[Mapping[str, Any]],
    change_shape_by_pr: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Everything that argues against this episode having had impact.

    Recorded on the episode itself rather than buried in the scoring, because
    a dashboard that only shows supporting evidence is an advertisement.
    """
    out: list[dict[str, Any]] = []
    for row in regression_rows:
        tier = str(row.get("regression_evidence_tier"))
        if tier == "none":
            continue
        out.append(
            {
                "kind": "corrective_follow_up",
                "evidence_tier": tier,
                "requires_human_confirmation": bool(row.get("requires_human_confirmation")),
                "pr_number": row.get("pr_number"),
                "detail": (
                    f"PR #{row.get('pr_number')} has {tier} evidence of later "
                    f"corrective work"
                    + (" (proximate-only: not confirmed to be a regression)"
                       if row.get("requires_human_confirmation") else "")
                ),
            }
        )
        if row.get("was_reverted"):
            out.append(
                {
                    "kind": "reverted",
                    "evidence_tier": "explicit",
                    "requires_human_confirmation": False,
                    "pr_number": row.get("pr_number"),
                    "detail": f"PR #{row.get('pr_number')} was explicitly reverted",
                }
            )
    if status_record.get("release_corroboration") != "corroborated":
        out.append(
            {
                "kind": "release_unverified",
                "evidence_tier": "structural",
                "requires_human_confirmation": False,
                "detail": (
                    "No documentation, changelog, flag removal, closed issue or "
                    "downstream adoption corroborates that this reached users."
                ),
            }
        )
    for pr in prs:
        number = int(pr["pr_number"])
        shape = change_shape_by_pr.get(number) or {}
        if shape.get("title_claim_corroborated") is False:
            out.append(
                {
                    "kind": "title_claim_not_corroborated",
                    "evidence_tier": "structural",
                    "requires_human_confirmation": False,
                    "pr_number": number,
                    "detail": str(shape.get("title_claim_note") or "")[:240],
                }
            )
        if shape.get("production_without_test_change"):
            out.append(
                {
                    "kind": "production_change_without_tests",
                    "evidence_tier": "structural",
                    "requires_human_confirmation": False,
                    "pr_number": number,
                    "detail": "production code changed with no accompanying test change",
                }
            )
    return out


def _episode_artifacts(
    eid: str,
    prs: Sequence[Mapping[str, Any]],
    issues: Sequence[Mapping[str, Any]],
    commits: Sequence[Mapping[str, Any]],
    files: Sequence[Mapping[str, Any]],
    flags: Sequence[Mapping[str, Any]],
    interventions: Sequence[Mapping[str, Any]],
    repo_url: str,
) -> list[dict[str, Any]]:
    """Every artifact link, typed and provenanced. This is the audit trail."""
    rows: list[dict[str, Any]] = []

    def add(kind: str, artifact: str, relationship: str, url: str | None,
            title: str | None, provenance: str, detail: str = "") -> None:
        rows.append(
            {
                "episode_id": eid,
                "artifact_kind": kind,
                "artifact_id": artifact,
                "relationship": relationship,
                "url": url,
                "title": (title or "")[:280] or None,
                "evidence_provenance": provenance,
                "detail": detail[:280] or None,
                "episode_construction_version": VERSION,
            }
        )

    for pr in prs:
        number = int(pr["pr_number"])
        add("pull_request", pr_artifact(number), "implementation",
            pr.get("url") or url_for_pr(number, repo_url), pr.get("title_raw"),
            "deterministic:phase1.pull_requests",
            f"state={pr.get('state')} merged_at={pr.get('merged_at')}")
    for issue in issues:
        number = int(issue["issue_number"])
        add("issue", issue_artifact(number), "motivating_problem",
            issue.get("url") or url_for_issue(number, repo_url), issue.get("title"),
            "deterministic:github_closing_reference",
            f"state={issue.get('state')} reason={issue.get('state_reason')}")
    for commit in commits[:200]:
        sha = str(commit.get("commit_sha") or "")
        add("commit", commit_artifact(sha), "merge_commit", None,
            commit.get("subject"), "deterministic:github_merge_commit",
            f"authored_at={commit.get('authored_at')}")
    for flag in flags:
        key = str(flag.get("flag_key") or "")
        add("feature_flag", flag_artifact(key), "rollout_control", None, key,
            f"deterministic:{flag.get('detection')}",
            f"diff_side={flag.get('diff_side')} owner={flag.get('owner_annotation')}")
    for row in interventions:
        if not row.get("is_intervention_candidate"):
            continue
        add("review_comment", comment_artifact(str(row.get("comment_id"))),
            "review_intervention", row.get("url"),
            str(row.get("body_text") or "")[:120],
            "deterministic:phase1.review_intervention_candidates",
            f"substance={row.get('substance_class')} "
            f"followed_by_change={row.get('followed_by_change_in_path')}")
    # Files are the largest class; keep the ones that carry meaning.
    notable = [
        f for f in files
        if f.get("change_status") == "A" or f.get("is_migration") or f.get("is_docs")
    ][:120]
    for row in notable:
        path = str(row.get("path") or "")
        relationship = (
            "introduced_file" if row.get("change_status") == "A"
            else "migration" if row.get("is_migration") else "documentation"
        )
        add("file", file_artifact(path), relationship, None, path,
            "deterministic:git_merge_commit_diff",
            f"status={row.get('change_status')} component={row.get('component')}")
    return rows


def summarise(episodes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(episodes)
    by_status: dict[str, int] = defaultdict(int)
    by_corroboration: dict[str, int] = defaultdict(int)
    for episode in items:
        by_status[str(episode.get("status"))] += 1
        by_corroboration[str(episode.get("release_corroboration"))] += 1
    sizes = sorted(int(e.get("pr_count") or 0) for e in items)
    confidences = [float(e.get("cluster_confidence") or 0) for e in items]
    return {
        "episodes": len(items),
        "by_status": dict(sorted(by_status.items())),
        "by_release_corroboration": dict(sorted(by_corroboration.items())),
        "ranked_episodes": sum(1 for e in items if e.get("ranked")),
        "single_pr_episodes": sum(1 for s in sizes if s == 1),
        "largest_episode_prs": sizes[-1] if sizes else 0,
        "median_episode_prs": sizes[len(sizes) // 2] if sizes else 0,
        "mean_cluster_confidence": (
            round(sum(confidences) / len(confidences), 4) if confidences else None
        ),
        "with_counterevidence": sum(1 for e in items if e.get("counterevidence")),
        "episode_construction_version": VERSION,
    }
