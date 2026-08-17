"""Stage: raw -> normalized entity tables.

The join that makes this cheap: PostHog squash-merges, so GraphQL
``mergeCommit.oid`` maps each PR onto exactly one commit on ``master``, and
that commit's Git diff supplies ``pr_files`` in full -- statuses, renames,
per-file line counts, binary markers -- without spending API budget.

The mapping is taken from GitHub metadata, never from the ``(#12345)`` suffix
in the commit subject (spec: "Map PR merge commits to PR numbers using GitHub
metadata rather than assuming commit-message syntax").  The suffix is still
parsed and *compared*, and disagreements are counted as a reconciliation
signal rather than being quietly preferred either way.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterable, Mapping

from ..config import Settings, iso, parse_ts
from ..hashing import commit_id, file_id, issue_id, pr_id
from ..ingest.git_source import read_file_at
from ..ingest.runs import ExtractionRun, RawPageLedger
from ..store import RawStore, read_json, write_json, write_table
from ..versions import SCHEMA_VERSION, feature_version
from . import references as R
from .actors import ActorResolver, resolve_actor_ref
from .components import ComponentIndex, summarise_coverage
from .paths import PathClassifier
from .title_parser import parse_title

log = logging.getLogger("impact.normalize")

UTC = dt.timezone.utc
FLAG_REGISTRY_PATH = "frontend/src/lib/constants.tsx"


def _now() -> str:
    return iso(dt.datetime.now(UTC))


def _tc(node: Mapping[str, Any] | None, key: str) -> int | None:
    """totalCount of a connection, preserving 'not recorded' as None."""
    if not node:
        return None
    conn = node.get(key)
    if not isinstance(conn, dict):
        return None
    value = conn.get("totalCount")
    return int(value) if value is not None else None


def _nodes(node: Mapping[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not node:
        return []
    conn = node.get(key)
    if not isinstance(conn, dict):
        return []
    return [n for n in (conn.get("nodes") or []) if n]


def run(settings: Settings) -> dict[str, Any]:
    run_rec = ExtractionRun.start(settings, "normalize")
    gh = RawStore(settings.path("raw", "github"))
    git = RawStore(settings.path("raw", "git_extract"))
    out = settings.path("normalized")
    out.mkdir(parents=True, exist_ok=True)

    clone_info = read_json(settings.path("raw", "git_extract", "clone_info.json"), {})
    head_sha = clone_info.get("analyzed_head_sha", "")
    window = settings.window

    log.info("building component index at %s", head_sha[:12])
    components = ComponentIndex.build(settings, head_sha)
    paths = PathClassifier(settings.generated)
    actors = ActorResolver(settings.bots)

    flag_source = read_file_at(settings, head_sha, FLAG_REGISTRY_PATH)
    flag_registry = R.parse_flag_registry(flag_source)
    flag_owners = R.parse_flag_registry_owners(flag_source)
    log.info("feature-flag registry: %d keys", len(flag_registry))

    # ------------------------------------------------------------------
    # Load raw
    # ------------------------------------------------------------------
    pr_cores = gh.read_entity("pr_core")
    pr_details = {int(d["number"]): d for d in gh.read_entity("pr_detail") if d.get("number")}
    pr_index = {int(r["number"]): r for r in gh.read("pr_index", "index") if r.get("number")}
    raw_issues = gh.read_entity("issues")
    commits_raw = git.read_entity("commits")
    files_raw = git.read_entity("commit_files")
    patches = {
        p["commit_sha"]: p for p in git.read("commit_patches", "window") if p.get("commit_sha")
    }
    # Diffs of commits that touch a feature-flag reference. Separate from the
    # general patch store because flag evidence must not depend on whether the
    # (bounded, optional) patch collection happened to cover a given commit.
    flag_diffs = {
        d["commit_sha"]: d.get("diff_text")
        for d in git.read("flag_diffs", "window")
        if d.get("commit_sha")
    }
    log.info(
        "raw loaded: %d pr_core, %d pr_detail, %d issues, %d commits, %d commit-files",
        len(pr_cores), len(pr_details), len(raw_issues), len(commits_raw), len(files_raw),
    )

    files_by_commit: dict[str, list[dict[str, Any]]] = {}
    for record in files_raw:
        files_by_commit.setdefault(record.get("commit_sha") or "", []).append(record)

    # ------------------------------------------------------------------
    # commits + commit_parents
    # ------------------------------------------------------------------
    commit_rows: list[dict[str, Any]] = []
    parent_rows: list[dict[str, Any]] = []
    for commit in commits_raw:
        actors.add_git_identity(
            commit.get("author_name"), commit.get("author_email"), source="git_author"
        )
        actors.add_git_identity(
            commit.get("committer_name"), commit.get("committer_email"),
            source="git_committer",
        )
        for co in commit.get("co_authors") or []:
            actors.add_git_identity(
                co.get("name"), co.get("email"), source="git_co_author"
            )

        row = dict(commit)
        row["author_actor_id"] = actors.add_git_identity(
            commit.get("author_name"), commit.get("author_email"), source="git_author"
        )
        row["committer_actor_id"] = actors.add_git_identity(
            commit.get("committer_name"), commit.get("committer_email"),
            source="git_committer",
        )
        row["co_author_actor_ids"] = [
            actors.add_git_identity(c.get("name"), c.get("email"), source="git_co_author")
            for c in (commit.get("co_authors") or [])
        ]
        row["in_window"] = window.contains(parse_ts(commit.get("committed_at")))
        row["has_patch_text"] = bool((patches.get(commit["commit_sha"]) or {}).get("patch_text"))
        row["patch_unavailable_reason"] = (
            patches.get(commit["commit_sha"]) or {}
        ).get("unavailable_reason")
        # pr_number filled in below once the authoritative mapping is known.
        row["pr_number"] = None
        row["pr_mapping_source"] = None
        commit_rows.append(row)

        for position, parent in enumerate(commit.get("parent_shas") or []):
            parent_rows.append(
                {
                    "commit_id": commit["commit_id"],
                    "commit_sha": commit["commit_sha"],
                    "parent_sha": parent,
                    "parent_position": position,
                    "parent_commit_id": commit_id(parent, settings.qualifier),
                }
            )

    commits_by_sha = {c["commit_sha"]: c for c in commit_rows}

    # ------------------------------------------------------------------
    # pull_requests (+ authoritative PR -> commit mapping)
    # ------------------------------------------------------------------
    pr_rows: list[dict[str, Any]] = []
    pr_file_rows: list[dict[str, Any]] = []
    reference_rows: list[dict[str, Any]] = []
    flag_rows: list[dict[str, Any]] = []
    mapping_conflicts = 0
    mapped_by_metadata = 0
    merged_no_commit = 0
    path_resolutions: dict[str, dict[str, Any]] = {}

    for core in sorted(pr_cores, key=lambda c: int(c.get("number") or 0)):
        number = core.get("number")
        if number is None:
            continue
        number = int(number)
        if core.get("_unresolvable"):
            run_rec.count("pr_unresolvable")
            continue

        author_id = actors.add_github_actor(core.get("author"), source="pr_author")
        merger_id = actors.add_github_actor(core.get("mergedBy"), source="pr_merged_by")
        for assignee in _nodes(core, "assignees"):
            actors.add_github_actor(assignee, source="pr_assignee")
        for participant in _nodes(core, "participants"):
            actors.add_github_actor(participant, source="pr_participant")

        title = core.get("title") or ""
        body = core.get("bodyText") or ""
        parsed = parse_title(title, body)

        merged_at = parse_ts(core.get("mergedAt"))
        created_at = parse_ts(core.get("createdAt"))
        merge_commit = (core.get("mergeCommit") or {}).get("oid")

        # Authoritative mapping, with the commit-subject suffix used only to
        # corroborate.
        subject_number = None
        if merge_commit and merge_commit in commits_by_sha:
            commit_row = commits_by_sha[merge_commit]
            subject_number = commit_row.get("pr_number_from_subject")
            commit_row["pr_number"] = number
            commit_row["pr_mapping_source"] = "github_merge_commit"
            mapped_by_metadata += 1
            if subject_number is not None and int(subject_number) != number:
                mapping_conflicts += 1
        elif merged_at is not None:
            merged_no_commit += 1

        is_merge_queue = parsed.title_class == "merge_queue_artifact"
        merged_in_window = window.contains(merged_at)
        created_in_window = window.contains(created_at)

        classifications = []
        commit_files = files_by_commit.get(merge_commit or "", [])
        for record in commit_files:
            path = record.get("path") or ""
            if not path:
                continue
            classification = paths.classify(path)
            classifications.append(classification)
            resolution = components.resolve(path)
            path_resolutions.setdefault(path, resolution.as_dict())
            pr_file_rows.append(
                {
                    "pr_file_id": file_id(number, path, settings.qualifier),
                    "pr_id": pr_id(number, settings.qualifier),
                    "pr_number": number,
                    "commit_sha": merge_commit,
                    "path": path,
                    "old_path": record.get("old_path"),
                    "new_path": record.get("new_path"),
                    "change_status": record.get("change_status"),
                    "similarity_score": record.get("similarity_score"),
                    "additions": record.get("additions"),
                    "deletions": record.get("deletions"),
                    "is_binary": record.get("is_binary"),
                    "line_counts_unavailable_reason": record.get(
                        "line_counts_unavailable_reason"
                    ),
                    "is_submodule": record.get("is_submodule"),
                    "old_blob_sha": record.get("old_blob_sha"),
                    "new_blob_sha": record.get("new_blob_sha"),
                    **{
                        k: v
                        for k, v in classification.as_dict().items()
                        if k not in {"path"}
                    },
                    "component": resolution.component,
                    "platform": resolution.platform,
                    "component_source": resolution.component_source,
                    "component_rule_priority": resolution.component_priority,
                    "component_rule_pattern": resolution.component_pattern,
                    "owners": resolution.owners,
                    "owner_source": resolution.owner_source,
                    "license_area": resolution.license_area,
                    "mapping_uncertainty": resolution.uncertainty,
                    "computed_at": _now(),
                }
            )

        bulk = paths.is_bulk_change(classifications)

        refs = R.extract_all(
            title=title, body=body, self_number=number, flag_registry=flag_registry
        )
        for ref in refs:
            reference_rows.append(
                {
                    "source_kind": "pull_request",
                    "source_number": number,
                    "source_id": pr_id(number, settings.qualifier),
                    **ref,
                    "computed_at": _now(),
                }
            )
        # GitHub's own closing links are the strongest issue linkage available.
        for issue_ref in _nodes(core, "closingIssuesReferences"):
            reference_rows.append(
                {
                    "source_kind": "pull_request",
                    "source_number": number,
                    "source_id": pr_id(number, settings.qualifier),
                    "reference_kind": "issue_or_pr",
                    "reference_value": str(issue_ref.get("number")),
                    "reference_subtype": "github_closing_reference",
                    "strength": "strong",
                    "source_field": "github_metadata",
                    "evidence": (issue_ref.get("title") or "")[:280],
                    "computed_at": _now(),
                }
            )

        # Prefer the flag-filtered diff; fall back to the general patch store.
        patch = flag_diffs.get(merge_commit or "") or (
            patches.get(merge_commit or "") or {}
        ).get("patch_text")
        for flag in R.flags_from_diff(patch, flag_registry):
            flag_rows.append(
                {
                    "pr_number": number,
                    "pr_id": pr_id(number, settings.qualifier),
                    "flag_key": flag["reference_value"],
                    "detection": flag["reference_subtype"],
                    "diff_side": flag.get("diff_side"),
                    "strength": flag["strength"],
                    "evidence": flag["evidence"],
                    "owner_annotation": flag_owners.get(flag["reference_value"]),
                    "computed_at": _now(),
                }
            )
        for ref in refs:
            if ref["reference_kind"] == "feature_flag":
                flag_rows.append(
                    {
                        "pr_number": number,
                        "pr_id": pr_id(number, settings.qualifier),
                        "flag_key": ref["reference_value"],
                        "detection": ref["reference_subtype"],
                        "diff_side": None,
                        "strength": ref["strength"],
                        "evidence": ref["evidence"],
                        "owner_annotation": flag_owners.get(ref["reference_value"]),
                        "computed_at": _now(),
                    }
                )

        index_entry = pr_index.get(number, {})
        pr_rows.append(
            {
                "pr_id": pr_id(number, settings.qualifier),
                "pr_number": number,
                "repository": settings.qualifier,
                "url": core.get("url"),
                "node_id": core.get("id"),
                "database_id": core.get("databaseId"),
                "title_raw": title,
                "body_text": body,
                "body_length": len(body),
                "state": core.get("state"),
                "is_draft": core.get("isDraft"),
                "created_at": iso(created_at),
                "updated_at": iso(parse_ts(core.get("updatedAt"))),
                "closed_at": iso(parse_ts(core.get("closedAt"))),
                "merged_at": iso(merged_at),
                "author_actor_id": author_id,
                "author_login": (core.get("author") or {}).get("login"),
                "author_typename": (core.get("author") or {}).get("__typename"),
                "merged_by_actor_id": merger_id,
                "merged_by_login": (core.get("mergedBy") or {}).get("login"),
                "base_ref": core.get("baseRefName"),
                "head_ref": core.get("headRefName"),
                "base_sha": core.get("baseRefOid"),
                "head_sha": core.get("headRefOid"),
                "merge_commit_sha": merge_commit,
                "is_cross_repository": core.get("isCrossRepository"),
                "head_repository_owner": (core.get("headRepositoryOwner") or {}).get("login"),
                "milestone_title": (core.get("milestone") or {}).get("title"),
                "labels": [n.get("name") for n in _nodes(core, "labels")],
                "label_count": _tc(core, "labels"),
                "assignee_logins": [n.get("login") for n in _nodes(core, "assignees")],
                "participant_logins": [n.get("login") for n in _nodes(core, "participants")],
                "participant_count": _tc(core, "participants"),
                # GitHub-reported descriptors, kept for reconciliation only.
                "github_additions": core.get("additions"),
                "github_deletions": core.get("deletions"),
                "github_changed_files": core.get("changedFiles"),
                "review_count": _tc(core, "reviews"),
                "review_thread_count": _tc(core, "reviewThreads"),
                "comment_count": _tc(core, "comments"),
                # Whether the *detail* pass actually retrieved this PR's review
                # threads and comments. The counts above come from the cheap
                # core pass and exist for every PR, so without this column a
                # partially-completed detail pass looks identical to a PR that
                # genuinely had no discussion. Detail batches are bucketed by PR
                # number, so an incomplete pass leaves a contiguous *date* gap --
                # which would systematically under-credit whoever reviewed in
                # that period rather than adding random noise.
                "review_detail_fetched": number in pr_details,
                "commit_count": _tc(core, "commits"),
                "reaction_count": _tc(core, "reactions"),
                # Parsed title
                "title_prefix": parsed.prefix_normalized,
                "title_prefix_raw": parsed.prefix,
                "title_scope": parsed.scope,
                "title_breaking": parsed.breaking,
                "title_subject": parsed.subject,
                "title_parser_status": parsed.parser_status,
                "title_parser_confidence": parsed.confidence,
                "title_parser_notes": parsed.parser_notes,
                "title_class": parsed.title_class,
                "title_squash_pr_number": parsed.squash_pr_number,
                # Change shape descriptors sourced from Git
                "git_file_count": len(commit_files),
                "git_additions": sum(
                    f.get("additions") or 0 for f in commit_files
                ) or None,
                "git_deletions": sum(
                    f.get("deletions") or 0 for f in commit_files
                ) or None,
                "has_binary_files": any(f.get("is_binary") for f in commit_files),
                **bulk,
                # Cohort + eligibility. Bot authorship is a SEPARATE column so
                # a consumer can decide; it is not folded into eligibility.
                "cohorts": index_entry.get("cohorts") or [],
                "merged_in_window": merged_in_window,
                "created_in_window": created_in_window,
                "context_only": not (merged_in_window or created_in_window),
                "is_merge_queue_artifact": is_merge_queue,
                "ranking_eligible": bool(
                    core.get("state") == "MERGED"
                    and merged_in_window
                    and not is_merge_queue
                ),
                "ranking_ineligible_reason": (
                    None
                    if (core.get("state") == "MERGED" and merged_in_window and not is_merge_queue)
                    else "merge_queue_artifact" if is_merge_queue
                    else "not_merged" if core.get("state") != "MERGED"
                    else "merged_outside_window"
                ),
                "has_merge_commit_in_clone": bool(
                    merge_commit and merge_commit in commits_by_sha
                ),
                "schema_version": SCHEMA_VERSION,
                "computed_at": _now(),
            }
        )

    log.info(
        "pull_requests: %d rows (%d mapped to a local merge commit, %d merged w/o local commit)",
        len(pr_rows), mapped_by_metadata, merged_no_commit,
    )

    # ------------------------------------------------------------------
    # reviews / threads / comments
    # ------------------------------------------------------------------
    review_rows: list[dict[str, Any]] = []
    thread_rows: list[dict[str, Any]] = []
    review_comment_rows: list[dict[str, Any]] = []
    comment_rows: list[dict[str, Any]] = []

    for number, detail in sorted(pr_details.items()):
        prid = pr_id(number, settings.qualifier)
        for review in _nodes(detail, "reviews"):
            reviewer = actors.add_github_actor(review.get("author"), source="review")
            review_rows.append(
                {
                    "review_id": review.get("id"),
                    "pr_id": prid,
                    "pr_number": number,
                    "database_id": review.get("databaseId"),
                    "reviewer_actor_id": reviewer,
                    "reviewer_login": (review.get("author") or {}).get("login"),
                    "reviewer_typename": (review.get("author") or {}).get("__typename"),
                    "state": review.get("state"),
                    "submitted_at": iso(parse_ts(review.get("submittedAt"))),
                    "created_at": iso(parse_ts(review.get("createdAt"))),
                    "updated_at": iso(parse_ts(review.get("updatedAt"))),
                    "body_text": review.get("bodyText") or "",
                    "body_length": len(review.get("bodyText") or ""),
                    "commit_sha": (review.get("commit") or {}).get("oid"),
                    "url": review.get("url"),
                    "computed_at": _now(),
                }
            )

        for thread in _nodes(detail, "reviewThreads"):
            path = thread.get("path") or ""
            resolution = components.resolve(path) if path else None
            if path and resolution:
                path_resolutions.setdefault(path, resolution.as_dict())
            comments = _nodes(thread, "comments")
            thread_rows.append(
                {
                    "thread_id": thread.get("id"),
                    "pr_id": prid,
                    "pr_number": number,
                    "path": path or None,
                    "line": thread.get("line"),
                    "start_line": thread.get("startLine"),
                    "original_line": thread.get("originalLine"),
                    "diff_side": thread.get("diffSide"),
                    "subject_type": thread.get("subjectType"),
                    "is_resolved": thread.get("isResolved"),
                    "is_outdated": thread.get("isOutdated"),
                    "is_collapsed": thread.get("isCollapsed"),
                    "resolved_by_login": (thread.get("resolvedBy") or {}).get("login"),
                    "resolved_by_actor_id": resolve_actor_ref(thread.get("resolvedBy")),
                    "comment_count": _tc(thread, "comments"),
                    "comments_truncated": bool(
                        (thread.get("comments") or {}).get("_truncated")
                    ),
                    "component": resolution.component if resolution else None,
                    "owners": resolution.owners if resolution else [],
                    "first_comment_at": min(
                        (iso(parse_ts(c.get("createdAt"))) for c in comments if c.get("createdAt")),
                        default=None,
                    ),
                    "participant_logins": sorted(
                        {
                            (c.get("author") or {}).get("login")
                            for c in comments
                            if (c.get("author") or {}).get("login")
                        }
                    ),
                    "computed_at": _now(),
                }
            )
            for position, comment in enumerate(comments):
                commenter = actors.add_github_actor(
                    comment.get("author"), source="review_comment"
                )
                review_comment_rows.append(
                    {
                        "comment_id": comment.get("id"),
                        "thread_id": thread.get("id"),
                        "pr_id": prid,
                        "pr_number": number,
                        "database_id": comment.get("databaseId"),
                        "position_in_thread": position,
                        "is_thread_opener": position == 0,
                        "author_actor_id": commenter,
                        "author_login": (comment.get("author") or {}).get("login"),
                        "author_typename": (comment.get("author") or {}).get("__typename"),
                        "body_text": comment.get("bodyText") or "",
                        "body_length": len(comment.get("bodyText") or ""),
                        "created_at": iso(parse_ts(comment.get("createdAt"))),
                        "updated_at": iso(parse_ts(comment.get("updatedAt"))),
                        "reply_to_id": (comment.get("replyTo") or {}).get("id"),
                        "original_commit_sha": (comment.get("originalCommit") or {}).get("oid"),
                        "commit_sha": (comment.get("commit") or {}).get("oid"),
                        "is_outdated": comment.get("outdated"),
                        "path": path or None,
                        "url": comment.get("url"),
                        "computed_at": _now(),
                    }
                )

        for comment in _nodes(detail, "comments"):
            commenter = actors.add_github_actor(comment.get("author"), source="pr_comment")
            comment_rows.append(
                {
                    "comment_id": comment.get("id"),
                    "parent_kind": "pull_request",
                    "parent_number": number,
                    "parent_id": prid,
                    "database_id": comment.get("databaseId"),
                    "author_actor_id": commenter,
                    "author_login": (comment.get("author") or {}).get("login"),
                    "author_typename": (comment.get("author") or {}).get("__typename"),
                    "body_text": comment.get("bodyText") or "",
                    "body_length": len(comment.get("bodyText") or ""),
                    "created_at": iso(parse_ts(comment.get("createdAt"))),
                    "updated_at": iso(parse_ts(comment.get("updatedAt"))),
                    "url": comment.get("url"),
                    "computed_at": _now(),
                }
            )

        for item in _nodes(detail, "timelineItems"):
            target = item.get("source") or item.get("subject") or {}
            target_number = target.get("number")
            if target_number is None:
                continue
            reference_rows.append(
                {
                    "source_kind": "pull_request",
                    "source_number": number,
                    "source_id": prid,
                    "reference_kind": "issue_or_pr",
                    "reference_value": str(target_number),
                    "reference_subtype": f"timeline_{item.get('__typename')}",
                    "strength": "strong" if item.get("willCloseTarget") else "medium",
                    "source_field": "github_timeline",
                    "evidence": f"{item.get('__typename')} at {item.get('createdAt')}",
                    "computed_at": _now(),
                }
            )

    # ------------------------------------------------------------------
    # issues
    # ------------------------------------------------------------------
    issue_rows: list[dict[str, Any]] = []
    for issue in sorted(raw_issues, key=lambda i: int(i.get("number") or 0)):
        number = issue.get("number")
        if number is None:
            continue
        number = int(number)
        author = actors.add_github_actor(issue.get("author"), source="issue_author")
        body = issue.get("bodyText") or ""
        issue_rows.append(
            {
                "issue_id": issue_id(number, settings.qualifier),
                "issue_number": number,
                "url": issue.get("url"),
                "title": issue.get("title"),
                "body_text": body,
                "state": issue.get("state"),
                "state_reason": issue.get("stateReason"),
                "created_at": iso(parse_ts(issue.get("createdAt"))),
                "updated_at": iso(parse_ts(issue.get("updatedAt"))),
                "closed_at": iso(parse_ts(issue.get("closedAt"))),
                "author_actor_id": author,
                "author_login": (issue.get("author") or {}).get("login"),
                "labels": [n.get("name") for n in _nodes(issue, "labels")],
                "assignee_logins": [n.get("login") for n in _nodes(issue, "assignees")],
                "comment_count": _tc(issue, "comments"),
                "created_in_window": window.contains(parse_ts(issue.get("createdAt"))),
                "computed_at": _now(),
            }
        )
        for comment in _nodes(issue, "comments"):
            commenter = actors.add_github_actor(comment.get("author"), source="issue_comment")
            comment_rows.append(
                {
                    "comment_id": comment.get("id"),
                    "parent_kind": "issue",
                    "parent_number": number,
                    "parent_id": issue_id(number, settings.qualifier),
                    "database_id": None,
                    "author_actor_id": commenter,
                    "author_login": (comment.get("author") or {}).get("login"),
                    "author_typename": (comment.get("author") or {}).get("__typename"),
                    "body_text": comment.get("bodyText") or "",
                    "body_length": len(comment.get("bodyText") or ""),
                    "created_at": iso(parse_ts(comment.get("createdAt"))),
                    "updated_at": None,
                    "url": comment.get("url"),
                    "computed_at": _now(),
                }
            )
        for item in _nodes(issue, "timelineItems"):
            target = item.get("source") or item.get("subject") or {}
            if target.get("number") is None:
                continue
            reference_rows.append(
                {
                    "source_kind": "issue",
                    "source_number": number,
                    "source_id": issue_id(number, settings.qualifier),
                    "reference_kind": "issue_or_pr",
                    "reference_value": str(target["number"]),
                    "reference_subtype": f"timeline_{item.get('__typename')}",
                    "strength": "strong" if item.get("willCloseTarget") else "medium",
                    "source_field": "github_timeline",
                    "evidence": f"{item.get('__typename')} at {item.get('createdAt')}",
                    "computed_at": _now(),
                }
            )

    # ------------------------------------------------------------------
    # actors (finalised last so every source has contributed)
    # ------------------------------------------------------------------
    actor_rows, actor_summary = actors.finalize()
    actor_by_id = {r["actor_id"]: r for r in actor_rows}
    for row in pr_rows:
        author = actor_by_id.get(row["author_actor_id"] or "")
        row["author_is_bot"] = bool(author and author["is_bot"])
        row["author_bot_probability"] = author["bot_probability"] if author else None
    for row in commit_rows:
        co_ids = row.get("co_author_actor_ids") or []
        row["has_ai_co_author"] = any(
            (actor_by_id.get(a) or {}).get("is_ai_assistant_identity") for a in co_ids
        )

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------
    written: dict[str, Any] = {}

    def emit(name: str, rows: list[dict[str, Any]], keys: list[str]) -> None:
        written[name] = write_table(out / f"{name}.parquet", rows, sort_keys=keys)
        log.info("wrote %-18s %7d rows", name, len(rows))

    emit("actors", actor_rows, ["actor_id"])
    emit("pull_requests", pr_rows, ["pr_number"])
    emit("commits", commit_rows, ["commit_sha"])
    emit("commit_parents", parent_rows, ["commit_sha", "parent_position"])
    emit("pr_files", pr_file_rows, ["pr_number", "path"])
    emit("reviews", review_rows, ["pr_number", "review_id"])
    emit("review_threads", thread_rows, ["pr_number", "thread_id"])
    emit("review_comments", review_comment_rows, ["pr_number", "thread_id", "position_in_thread"])
    emit("comments", comment_rows, ["parent_kind", "parent_number", "comment_id"])
    emit("issues", issue_rows, ["issue_number"])
    emit("references", reference_rows,
         ["source_kind", "source_number", "reference_kind", "reference_value", "source_field"])
    emit("feature_flags", flag_rows, ["pr_number", "flag_key", "detection", "diff_side"])
    emit("components", components.component_catalog(), ["component"])
    emit("path_map", sorted(path_resolutions.values(), key=lambda r: r["path"]), ["path"])

    ledger = RawPageLedger.load(settings.path("raw", "github") / "_ledger.json")
    written["raw_pages"] = ledger.to_parquet(out / "raw_pages.parquet")
    runs = read_json(settings.path("raw", "extraction_runs.json"), []) or []
    written["extraction_runs"] = write_table(
        out / "extraction_runs.parquet",
        [{**r, "counters": None, "notes": r.get("notes")} for r in runs],
        sort_keys=["run_started_at", "stage"],
    )

    write_json(
        settings.path("normalized", "_component_rules_snapshot.json"),
        components.rule_snapshot(),
    )

    coverage = summarise_coverage(path_resolutions.values())
    run_rec.set("tables", {k: v["row_count"] for k, v in written.items()})
    run_rec.set("actors", actor_summary)
    run_rec.set("component_coverage", coverage)
    run_rec.set(
        "pr_commit_mapping",
        {
            "mapped_by_github_metadata": mapped_by_metadata,
            "subject_suffix_conflicts": mapping_conflicts,
            "merged_without_local_commit": merged_no_commit,
        },
    )
    run_rec.set("feature_flag_registry_keys", len(flag_registry))
    run_rec.finish("ok")
    run_rec.append_to(settings.path("raw", "extraction_runs.json"))
    log.info("normalize complete: %s", {k: v["row_count"] for k, v in written.items()})
    return run_rec.as_row()


def load_normalized(settings: Settings, name: str) -> list[dict[str, Any]]:
    from ..store import read_table

    return read_table(settings.path("normalized", f"{name}.parquet"))


def index_rows(rows: Iterable[Mapping[str, Any]], key: str) -> dict[Any, dict[str, Any]]:
    return {r[key]: dict(r) for r in rows if r.get(key) is not None}
