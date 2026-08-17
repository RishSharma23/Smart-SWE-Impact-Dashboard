"""Regression and durability *candidates*.

The spec is emphatic on the failure mode to avoid: **never label a PR a
regression solely because a later fix touched the same files.**  A large
monorepo has files that change every day; co-occurrence is not causation.

So this module grades evidence explicitly and refuses to collapse the grades:

    explicit    a revert/reapply that names the PR or its subject, or a later
                PR whose body references this PR with fix language.  This is
                the only tier that is close to a fact.
    linked      a later fix PR that closes the same issue, or touches the same
                feature flag, as this PR.
    proximate   a later fix PR overlapping this PR's files inside the
                proximity window.  Weakest tier, retained for recall, and
                flagged as needing human confirmation.

Durability is measured the same way: survival of files introduced by a PR is
only reported when there is enough follow-up history *after* the PR to
observe it.  A PR merged three days before the window ends gets
``survival_30d = None``, not ``True``.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any, Iterable, Mapping

from ..config import parse_ts
from ..versions import feature_version

FIX_PREFIXES = {"fix", "revert", "perf"}


def _overlap(a: set[str], b: set[str]) -> set[str]:
    return a & b


def compute(
    *,
    prs: Mapping[int, Mapping[str, Any]],
    files_by_pr: Mapping[int, list[Mapping[str, Any]]],
    edges: Iterable[Mapping[str, Any]],
    flags_by_pr: Mapping[int, set[str]],
    issues_by_pr: Mapping[int, set[int]],
    window_end: dt.datetime,
    survival_days: tuple[int, ...] = (30, 60, 90),
    proximity_days: int = 14,
    min_path_overlap: int = 1,
) -> list[dict[str, Any]]:
    edge_list = list(edges)
    merged = {
        number: parse_ts(pr.get("merged_at"))
        for number, pr in prs.items()
        if pr.get("merged_at")
    }

    # Code paths only: a later PR touching the same lockfile means nothing.
    code_paths: dict[int, set[str]] = {}
    introduced_paths: dict[int, set[str]] = {}
    for number, rows in files_by_pr.items():
        code: set[str] = set()
        added: set[str] = set()
        for row in rows:
            if row.get("is_test") or row.get("is_docs") or row.get("is_snapshot"):
                continue
            if row.get("is_lockfile") or row.get("is_generated") or row.get("is_vendor"):
                continue
            path = str(row.get("path") or "")
            if not path:
                continue
            code.add(path)
            if row.get("change_status") == "A":
                added.add(path)
        code_paths[number] = code
        introduced_paths[number] = added

    # Which files still exist at the analysed HEAD, and when each was last
    # touched, derived from the union of all PR diffs in the window.
    deleted_at: dict[str, dt.datetime] = {}
    for number, rows in files_by_pr.items():
        when = merged.get(number)
        if when is None:
            continue
        for row in rows:
            path = str(row.get("path") or "")
            if not path:
                continue
            if row.get("change_status") == "D":
                previous = deleted_at.get(path)
                if previous is None or when > previous:
                    deleted_at[path] = when
            elif path in deleted_at and when > deleted_at[path]:
                deleted_at.pop(path, None)   # re-added later

    # -- explicit revert / reapply edges ---------------------------------
    explicit_by_target: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for edge in edge_list:
        if edge.get("target_kind") != "pull_request":
            continue
        target = edge.get("target_number")
        if target is None:
            continue
        if edge.get("edge_type") in {"reverts", "reapplies", "supersedes"}:
            explicit_by_target[int(target)].append(dict(edge))

    # Reverts are also detectable from the merge commit subject, which is how
    # PostHog spells them ("revert(scope): <subject>") rather than git's form.
    subject_index: dict[str, list[int]] = defaultdict(list)
    for number, pr in prs.items():
        subject = (pr.get("title_subject") or "").strip().lower()
        if subject:
            subject_index[subject].append(number)

    fix_prs = sorted(
        (n for n, pr in prs.items()
         if pr.get("title_prefix") in FIX_PREFIXES and merged.get(n)),
        key=lambda n: merged[n],
    )

    out: list[dict[str, Any]] = []
    for number, pr in sorted(prs.items()):
        merged_at = merged.get(number)
        my_paths = code_paths.get(number, set())
        my_flags = flags_by_pr.get(number, set())
        my_issues = issues_by_pr.get(number, set())

        explicit: list[dict[str, Any]] = list(explicit_by_target.get(number, []))
        # A later PR titled "revert(x): <same subject>" reverts this one.
        my_subject = (pr.get("title_subject") or "").strip().lower()
        if my_subject:
            for other in subject_index.get(my_subject, []):
                other_pr = prs.get(other) or {}
                if other == number or other_pr.get("title_prefix") != "revert":
                    continue
                other_merged = merged.get(other)
                if merged_at and other_merged and other_merged > merged_at:
                    explicit.append(
                        {
                            "target_number": other, "edge_type": "reverted_by",
                            "strength": "strong",
                            "evidence": f"PR #{other} is revert(...) with the same subject",
                        }
                    )

        linked: list[dict[str, Any]] = []
        proximate: list[dict[str, Any]] = []

        if merged_at and my_paths:
            horizon = merged_at + dt.timedelta(days=proximity_days)
            for other in fix_prs:
                other_merged = merged[other]
                if other == number or other_merged <= merged_at:
                    continue
                if other_merged > horizon:
                    break     # fix_prs is time-ordered
                other_issues = issues_by_pr.get(other, set())
                other_flags = flags_by_pr.get(other, set())
                shared_issue = my_issues & other_issues
                shared_flag = my_flags & other_flags
                shared_paths = _overlap(my_paths, code_paths.get(other, set()))

                if shared_issue or shared_flag:
                    linked.append(
                        {
                            "target_number": other, "edge_type": "later_fix_linked",
                            "strength": "medium",
                            "evidence": (
                                f"PR #{other} ({prs[other].get('title_prefix')}) shares "
                                + (f"issue(s) {sorted(shared_issue)} " if shared_issue else "")
                                + (f"flag(s) {sorted(shared_flag)}" if shared_flag else "")
                            ).strip(),
                            "shared_path_count": len(shared_paths),
                            "days_after": round(
                                (other_merged - merged_at).total_seconds() / 86400, 2
                            ),
                        }
                    )
                elif len(shared_paths) >= min_path_overlap:
                    proximate.append(
                        {
                            "target_number": other, "edge_type": "later_fix_same_paths",
                            "strength": "weak",
                            "evidence": (
                                f"PR #{other} is a {prs[other].get('title_prefix')} touching "
                                f"{len(shared_paths)} of the same non-test file(s)"
                            ),
                            "shared_paths": sorted(shared_paths)[:10],
                            "shared_path_count": len(shared_paths),
                            "days_after": round(
                                (other_merged - merged_at).total_seconds() / 86400, 2
                            ),
                        }
                    )

        # -- corrective churn attribution --------------------------------
        churn: dict[str, int] = {
            "self_follow_up": 0, "collaborator_follow_up": 0,
            "revert": 0, "replacement": 0, "unknown": 0,
        }
        for candidate in [*explicit, *linked, *proximate]:
            other = candidate.get("target_number")
            other_pr = prs.get(int(other)) if other is not None else None
            if candidate.get("edge_type") in {"reverts", "reverted_by"}:
                churn["revert"] += 1
            elif candidate.get("edge_type") in {"supersedes", "reapplies"}:
                churn["replacement"] += 1
            elif other_pr is None:
                churn["unknown"] += 1
            elif other_pr.get("author_actor_id") == pr.get("author_actor_id"):
                churn["self_follow_up"] += 1
            elif other_pr.get("author_actor_id"):
                churn["collaborator_follow_up"] += 1
            else:
                churn["unknown"] += 1

        # -- durability ---------------------------------------------------
        survival: dict[str, Any] = {}
        introduced = introduced_paths.get(number, set())
        for days in survival_days:
            key = f"survival_{days}d"
            if merged_at is None or not introduced:
                survival[key] = None
                survival[f"{key}_reason"] = (
                    "no files introduced" if merged_at else "not merged"
                )
                continue
            checkpoint = merged_at + dt.timedelta(days=days)
            if checkpoint > window_end:
                # Honest "we cannot know yet" rather than a false True.
                survival[key] = None
                survival[f"{key}_reason"] = (
                    f"insufficient follow-up history: +{days}d falls after the window end"
                )
                continue
            gone = {
                p for p in introduced
                if p in deleted_at and deleted_at[p] <= checkpoint
            }
            survival[key] = round(1 - len(gone) / len(introduced), 4)
            survival[f"{key}_reason"] = None

        # Tests added after a reported breakage: this PR is a fix AND adds tests.
        my_files = files_by_pr.get(number, [])
        tests_added_in_fix = bool(
            pr.get("title_prefix") in {"fix", "revert"}
            and any(f.get("is_test") and f.get("change_status") in {"A", "M"} for f in my_files)
        )

        out.append(
            {
                "pr_number": number,
                "pr_id": pr.get("pr_id"),
                "merged_at": pr.get("merged_at"),
                # tiers, kept separate on purpose
                "explicit_regression_signals": explicit,
                "explicit_signal_count": len(explicit),
                "linked_fix_candidates": linked,
                "linked_candidate_count": len(linked),
                "proximate_fix_candidates": proximate,
                "proximate_candidate_count": len(proximate),
                "regression_evidence_tier": (
                    "explicit" if explicit
                    else "linked" if linked
                    else "proximate" if proximate
                    else "none"
                ),
                "requires_human_confirmation": bool(proximate and not explicit and not linked),
                "was_reverted": any(
                    e.get("edge_type") in {"reverts", "reverted_by"} for e in explicit
                ),
                # corrective churn breakdown
                "corrective_churn": churn,
                "corrective_churn_total": sum(churn.values()),
                # durability
                "files_introduced": len(introduced),
                **survival,
                "tests_added_with_fix": tests_added_in_fix,
                "regression_version": feature_version("regression"),
            }
        )
    return out


def summarise(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    tiers: dict[str, int] = {}
    for row in items:
        key = str(row.get("regression_evidence_tier"))
        tiers[key] = tiers.get(key, 0) + 1
    measurable = [
        r for r in items if r.get("survival_30d") is not None
    ]
    return {
        "pull_requests": len(items),
        "evidence_tier_distribution": dict(sorted(tiers.items())),
        "explicitly_reverted": sum(1 for r in items if r.get("was_reverted")),
        "requiring_human_confirmation": sum(
            1 for r in items if r.get("requires_human_confirmation")
        ),
        "survival_30d_measurable": len(measurable),
        "survival_30d_mean": (
            round(sum(r["survival_30d"] for r in measurable) / len(measurable), 4)
            if measurable else None
        ),
        "fixes_that_added_tests": sum(1 for r in items if r.get("tests_added_with_fix")),
    }
