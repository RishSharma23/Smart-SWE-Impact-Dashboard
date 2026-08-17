"""Reconciliation against independent sources.

Three comparisons, none of which trusts the pipeline's own bookkeeping:

1. **PR counts vs an independent GitHub query.** The dataset's merged-in-window
   count is compared against a fresh ``search`` issued at validation time for
   the same ``mergedAt`` range. Search totals drift slightly (indexing lag,
   PRs merged to non-default branches), so a tolerance is applied and the raw
   numbers are always printed.
2. **GitHub-reported vs Git-computed change size**, per PR.
3. **Commit coverage** — how many merged PRs have their merge commit locally.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterable, Mapping

from ..config import Settings, parse_ts
from ..ingest import graphql_queries as Q
from ..ingest.github_client import GitHubClient

log = logging.getLogger("impact.quality.reconcile")

# Search indexing lag makes exact agreement unrealistic; 2% is generous enough
# to avoid false alarms and tight enough to catch a real extraction gap.
COUNT_TOLERANCE = 0.02


def independent_pr_count(
    settings: Settings, client: GitHubClient, *, offline: bool = False
) -> dict[str, Any]:
    """Ask GitHub, independently, how many PRs merged in the window."""
    start = settings.window.start.date().isoformat()
    end = settings.window.end.date().isoformat()
    query = (
        f"repo:{settings.owner}/{settings.name} is:pr "
        f"merged:{start}..{end}"
    )
    try:
        payload = client.graphql(
            Q.SEARCH_QUERY,
            {"q": query, "cursor": None},
            entity="reconcile", shard="merged_count", query_name="reconcile_count",
        )
    except Exception as exc:  # noqa: BLE001 - reconciliation must not abort validation
        return {"available": False, "reason": str(exc)[:200], "query": query}
    total = ((payload.get("data") or {}).get("search") or {}).get("issueCount")
    return {"available": True, "query": query, "reported_total": total}


def reconcile_counts(
    dataset_merged: int, independent: Mapping[str, Any]
) -> dict[str, Any]:
    if not independent.get("available"):
        return {
            "status": "skipped",
            "reason": independent.get("reason", "independent query unavailable"),
            "dataset_merged_in_window": dataset_merged,
        }
    reported = independent.get("reported_total")
    if reported is None:
        return {"status": "skipped", "reason": "no issueCount returned",
                "dataset_merged_in_window": dataset_merged}
    delta = abs(int(reported) - dataset_merged)
    base = max(int(reported), dataset_merged, 1)
    within = delta / base <= COUNT_TOLERANCE
    return {
        "status": "pass" if within else "fail",
        "query": independent.get("query"),
        "independent_reported_total": int(reported),
        "dataset_merged_in_window": dataset_merged,
        "absolute_delta": delta,
        "relative_delta": round(delta / base, 5),
        "tolerance": COUNT_TOLERANCE,
        "note": (
            "GitHub search indexes asynchronously and counts PRs merged to any "
            "branch; small deltas are expected and are reported rather than hidden."
        ),
    }


def reconcile_change_size(anomalies: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [a for a in anomalies if a.get("reconciliation_possible")]
    if not rows:
        return {"status": "skipped", "reason": "no PR has a local merge commit"}
    file_mismatch = [r for r in rows if r.get("changed_files_mismatch")]
    deltas = [
        r["changed_files_delta"] for r in rows
        if isinstance(r.get("changed_files_delta"), (int, float))
    ]
    return {
        "status": "pass" if len(file_mismatch) / len(rows) <= 0.05 else "warn",
        "reconcilable_prs": len(rows),
        "changed_file_mismatches": len(file_mismatch),
        "agreement_rate": round(1 - len(file_mismatch) / len(rows), 4),
        "mean_absolute_file_delta": (
            round(sum(deltas) / len(deltas), 3) if deltas else None
        ),
        "worst_examples": sorted(
            (
                {
                    "pr_number": r["pr_number"],
                    "github": r.get("github_changed_files"),
                    "git": r.get("git_changed_files"),
                    "delta": r.get("changed_files_delta"),
                }
                for r in file_mismatch
            ),
            key=lambda r: -(r["delta"] or 0),
        )[:10],
        "note": (
            "GitHub counts the full branch diff; Git counts the squashed merge "
            "commit. Rebases and merge-queue re-bases make small deltas normal."
        ),
    }


def commit_coverage(prs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    merged = [p for p in prs if p.get("merged_in_window")]
    if not merged:
        return {"status": "skipped", "reason": "no merged PRs in window"}
    with_commit = [p for p in merged if p.get("has_merge_commit_in_clone")]
    rate = len(with_commit) / len(merged)
    return {
        "status": "pass" if rate >= 0.98 else "warn",
        "merged_in_window": len(merged),
        "with_local_merge_commit": len(with_commit),
        "coverage_rate": round(rate, 5),
        "missing": len(merged) - len(with_commit),
        "note": (
            "A merged PR without a local merge commit is normally one merged to "
            "a non-default branch, or one whose commit predates the shallow "
            "clone boundary."
        ),
    }


def window_boundary_report(
    prs: Iterable[Mapping[str, Any]], settings: Settings
) -> dict[str, Any]:
    """Prove the window filter is applied on mergedAt, in UTC, half-open."""
    rows = list(prs)
    start, end = settings.window.start, settings.window.end
    merged_flagged = [p for p in rows if p.get("merged_in_window")]
    violations = []
    for pr in merged_flagged:
        when = parse_ts(pr.get("merged_at"))
        if when is None or not (start <= when < end):
            violations.append({"pr_number": pr.get("pr_number"),
                               "merged_at": pr.get("merged_at")})
    # PRs created before the window but merged inside it are the interesting
    # boundary case; they must be present, not filtered out.
    straddling = [
        p for p in merged_flagged
        if (parse_ts(p.get("created_at")) or start) < start
    ]
    return {
        "status": "pass" if not violations else "fail",
        "window_start": settings.window.as_dict()["start"],
        "window_end": settings.window.as_dict()["end"],
        "semantics": "half-open [start, end) on mergedAt, UTC",
        "merged_in_window": len(merged_flagged),
        "boundary_violations": len(violations),
        "violation_examples": violations[:10],
        "created_before_window_merged_inside": len(straddling),
        "note": (
            "PRs opened before the window but merged inside it are retained; "
            "they are the reason the clone reaches 30 days further back."
        ),
    }
