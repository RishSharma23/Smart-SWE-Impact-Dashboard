"""Sanity and anomaly evidence.

Two independent sources describe the same change -- GitHub's reported
``additions``/``deletions``/``changedFiles`` and the Git diff of the merge
commit -- so they can be reconciled against each other.  Disagreement is
recorded per PR with the delta, never silently resolved in favour of one side.

Everything in here is a *flag with a reason*.  A lockfile-only PR, a huge
mechanical reformat and a bot dependency bump are all legitimate; the point is
that a consumer can see them rather than having them quietly inflate a count.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Mapping

from ..config import parse_ts
from ..versions import feature_version

# GitHub counts a PR's full branch diff; Git counts the squashed merge commit.
# Rebases and merge-queue re-bases make small differences normal, so only a
# material gap is worth flagging.
RECONCILE_ABS_TOLERANCE = 5
RECONCILE_REL_TOLERANCE = 0.10


def compute(
    *,
    prs: Mapping[int, Mapping[str, Any]],
    files_by_pr: Mapping[int, list[Mapping[str, Any]]],
    actors: Mapping[str, Mapping[str, Any]],
    commits_by_sha: Mapping[str, Mapping[str, Any]],
    window_start: dt.datetime,
    window_end: dt.datetime,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    # Duplicate/imported commits: the same tree landing under several SHAs.
    tree_counts: dict[str, int] = {}
    for commit in commits_by_sha.values():
        tree = str(commit.get("tree_sha") or "")
        if tree:
            tree_counts[tree] = tree_counts.get(tree, 0) + 1

    for number, pr in sorted(prs.items()):
        # `flags` = something may be WRONG with the data.
        # `shape` = something notable but perfectly legitimate about the change.
        # Mixing them made 92% of PRs "anomalous", which is the same as none.
        flags: list[str] = []
        shape: list[str] = []
        files = files_by_pr.get(number, [])

        # -- GitHub vs Git reconciliation --------------------------------
        gh_files = pr.get("github_changed_files")
        git_files = pr.get("git_file_count")
        gh_add, gh_del = pr.get("github_additions"), pr.get("github_deletions")
        git_add, git_del = pr.get("git_additions"), pr.get("git_deletions")

        def gap(a: Any, b: Any) -> tuple[int | None, bool]:
            if a is None or b is None:
                return None, False
            delta = abs(int(a) - int(b))
            base = max(abs(int(a)), abs(int(b)), 1)
            return delta, (
                delta > RECONCILE_ABS_TOLERANCE
                and delta / base > RECONCILE_REL_TOLERANCE
            )

        file_delta, file_mismatch = gap(gh_files, git_files)
        add_delta, add_mismatch = gap(gh_add, git_add)
        del_delta, del_mismatch = gap(gh_del, git_del)

        # Reconciliation only means anything when there IS a local merge commit
        # to reconcile against. Without one, git_file_count is 0 by definition
        # and comparing it to GitHub's count flags every large unmerged PR as a
        # mismatch -- 1,425 false positives on the real dataset.
        has_local_commit = bool(pr.get("has_merge_commit_in_clone"))
        if not has_local_commit:
            if pr.get("merged_at"):
                flags.append("merge_commit_not_in_local_clone")
            file_delta = add_delta = del_delta = None
            file_mismatch = add_mismatch = del_mismatch = False
        else:
            if file_mismatch:
                flags.append("github_vs_git_file_count_mismatch")
            if add_mismatch or del_mismatch:
                flags.append("github_vs_git_line_count_mismatch")

        # -- timestamps ---------------------------------------------------
        created = parse_ts(pr.get("created_at"))
        merged = parse_ts(pr.get("merged_at"))
        closed = parse_ts(pr.get("closed_at"))
        if created and merged and merged < created:
            flags.append("impossible_timestamps_merged_before_created")
        if created and closed and closed < created:
            flags.append("impossible_timestamps_closed_before_created")
        if merged and merged > window_end:
            flags.append("merged_after_window_end")
        lifetime_hours = (
            round((merged - created).total_seconds() / 3600, 3)
            if created and merged else None
        )

        # -- actors --------------------------------------------------------
        author = actors.get(str(pr.get("author_actor_id") or ""))
        if pr.get("merged_at") and not pr.get("author_actor_id"):
            flags.append("merged_pr_has_no_resolvable_author")
        if author and author.get("ambiguity_status") == "ambiguous":
            flags.append("author_identity_ambiguous")
        if author and author.get("is_bot"):
            shape.append("bot_authored")

        # -- change shape ---------------------------------------------------
        if files:
            total = len(files)
            for label, predicate in (
                ("lockfile_only", lambda f: f.get("is_lockfile")),
                ("snapshot_only", lambda f: f.get("is_snapshot")),
                ("generated_only", lambda f: f.get("is_generated")),
                ("vendored_only", lambda f: f.get("is_vendor")),
                ("docs_only", lambda f: f.get("is_docs")),
                ("test_only", lambda f: f.get("is_test")),
            ):
                if all(predicate(f) for f in files):
                    shape.append(label)
            if total >= 200:
                shape.append(f"very_large_file_count:{total}")
            if pr.get("is_bulk_change"):
                shape.append(f"bulk_mechanical_change:{pr.get('bulk_category')}")
        elif pr.get("merged_at") and has_local_commit:
            flags.append("merged_pr_with_zero_changed_files")

        # -- title vs content ------------------------------------------------
        if pr.get("title_parser_status") == "not_conventional":
            shape.append("title_not_conventional")
        if pr.get("title_parser_confidence") is not None and float(
            pr["title_parser_confidence"]
        ) < 0.5 and pr.get("title_prefix"):
            flags.append("low_confidence_title_parse")
        if pr.get("is_merge_queue_artifact"):
            shape.append("merge_queue_artifact")

        # -- duplicate / imported ---------------------------------------------
        merge_sha = str(pr.get("merge_commit_sha") or "")
        commit = commits_by_sha.get(merge_sha) or {}
        tree = str(commit.get("tree_sha") or "")
        if tree and tree_counts.get(tree, 0) > 1:
            flags.append(f"duplicate_tree_sha:{tree_counts[tree]}_commits")
        if commit.get("is_cherry_pick"):
            shape.append("cherry_picked_commit")
        # NOTE: author != committer is the NORMAL state for every squash merge
        # (GitHub is the committer), so it fired on 100% of rows and carried no
        # information. It is deliberately not flagged.

        out.append(
            {
                "pr_number": number,
                "pr_id": pr.get("pr_id"),
                "anomaly_flags": sorted(set(flags)),
                "anomaly_count": len(set(flags)),
                "has_anomaly": bool(flags),
                "shape_flags": sorted(set(shape)),
                "shape_flag_count": len(set(shape)),
                # reconciliation detail, retained even when it agrees
                "github_changed_files": gh_files,
                "git_changed_files": git_files,
                "changed_files_delta": file_delta,
                "changed_files_mismatch": file_mismatch,
                "github_additions": gh_add,
                "git_additions": git_add,
                "additions_delta": add_delta,
                "github_deletions": gh_del,
                "git_deletions": git_del,
                "deletions_delta": del_delta,
                "reconciliation_possible": has_local_commit,
                "reconciliation_skipped_reason": (
                    None if has_local_commit
                    else "merge commit is outside the cloned history depth"
                ),
                "pr_lifetime_hours": lifetime_hours,
                "anomaly_version": feature_version("anomaly"),
            }
        )
    return out


def summarise(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    counts: dict[str, int] = {}
    shape_counts: dict[str, int] = {}
    for row in items:
        for flag in row.get("anomaly_flags") or []:
            key = flag.split(":", 1)[0]
            counts[key] = counts.get(key, 0) + 1
        for flag in row.get("shape_flags") or []:
            key = flag.split(":", 1)[0]
            shape_counts[key] = shape_counts.get(key, 0) + 1
    reconcilable = [r for r in items if r.get("reconciliation_possible")]
    file_mismatch = sum(1 for r in reconcilable if r.get("changed_files_mismatch"))
    return {
        "pull_requests": len(items),
        "with_any_anomaly": sum(1 for r in items if r.get("has_anomaly")),
        "anomaly_flag_counts": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "shape_flag_counts": dict(sorted(shape_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "reconcilable_prs": len(reconcilable),
        "changed_file_mismatches": file_mismatch,
        "changed_file_agreement_rate": (
            round(1 - file_mismatch / len(reconcilable), 4) if reconcilable else None
        ),
    }


def completeness_by_month(
    prs: Iterable[Mapping[str, Any]], key: str = "merged_at"
) -> list[dict[str, Any]]:
    """Extraction completeness by entity and month, for the quality report."""
    buckets: dict[str, dict[str, int]] = {}
    for pr in prs:
        stamp = pr.get(key)
        if not stamp:
            continue
        month = str(stamp)[:7]
        bucket = buckets.setdefault(
            month,
            {"pull_requests": 0, "with_merge_commit": 0, "with_reviews": 0,
             "review_detail_fetched": 0, "with_files": 0, "ranking_eligible": 0},
        )
        bucket["pull_requests"] += 1
        if pr.get("has_merge_commit_in_clone"):
            bucket["with_merge_commit"] += 1
        if (pr.get("review_count") or 0) > 0:
            bucket["with_reviews"] += 1
        # Distinct from with_reviews: this is extraction coverage, not activity.
        if pr.get("review_detail_fetched"):
            bucket["review_detail_fetched"] += 1
        if (pr.get("git_file_count") or 0) > 0:
            bucket["with_files"] += 1
        if pr.get("ranking_eligible"):
            bucket["ranking_eligible"] += 1
    return [{"month": month, **counts} for month, counts in sorted(buckets.items())]
