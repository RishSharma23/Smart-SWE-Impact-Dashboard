"""Table invariants: schema, null semantics, uniqueness, foreign keys.

Every check returns a structured result rather than raising, so one failure
does not hide the other twenty. A check that cannot run (missing table) is
reported as ``skipped``, never as ``pass``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence


@dataclass
class CheckResult:
    name: str
    table: str
    status: str           # pass | fail | warn | skipped
    detail: str
    offenders: list[Any] = field(default_factory=list)
    count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.name,
            "table": self.table,
            "status": self.status,
            "detail": self.detail,
            "offending_count": self.count,
            "offending_examples": self.offenders[:10],
        }


# Column -> the invariant it must satisfy, per table.
#   required : never null
#   unique   : primary key (may be composite, given as a tuple)
#   fk       : (column, target_table, target_column)
#   nonneg   : numeric and >= 0 when present
TABLE_RULES: dict[str, dict[str, Any]] = {
    "actors": {
        "unique": [("actor_id",)],
        "required": ["actor_id", "account_type", "bot_probability", "ambiguity_status"],
        "range": [("bot_probability", 0.0, 1.0)],
    },
    "pull_requests": {
        "unique": [("pr_number",), ("pr_id",)],
        "required": ["pr_number", "pr_id", "state", "created_at", "ranking_eligible"],
        "fk": [("author_actor_id", "actors", "actor_id")],
        "nonneg": ["github_additions", "github_deletions", "github_changed_files",
                   "review_count", "comment_count", "git_file_count"],
        "range": [("title_parser_confidence", 0.0, 1.0)],
    },
    "commits": {
        "unique": [("commit_sha",)],
        "required": ["commit_sha", "commit_id", "authored_at", "committed_at"],
        "fk": [("author_actor_id", "actors", "actor_id")],
    },
    "commit_parents": {
        "unique": [("commit_sha", "parent_position")],
        "required": ["commit_sha", "parent_sha"],
        "fk": [("commit_sha", "commits", "commit_sha")],
    },
    "pr_files": {
        "unique": [("pr_number", "path")],
        "required": ["pr_number", "path", "change_status", "component"],
        "fk": [("pr_number", "pull_requests", "pr_number")],
        "nonneg": ["additions", "deletions"],
    },
    "reviews": {
        "unique": [("review_id",)],
        "required": ["review_id", "pr_number", "state"],
        "fk": [("pr_number", "pull_requests", "pr_number")],
    },
    "review_threads": {
        "unique": [("thread_id",)],
        "required": ["thread_id", "pr_number"],
        "fk": [("pr_number", "pull_requests", "pr_number")],
    },
    "review_comments": {
        "unique": [("comment_id",)],
        "required": ["comment_id", "pr_number", "thread_id"],
        "fk": [("thread_id", "review_threads", "thread_id"),
               ("pr_number", "pull_requests", "pr_number")],
    },
    "comments": {
        "unique": [("comment_id",)],
        "required": ["comment_id", "parent_kind", "parent_number"],
    },
    "issues": {
        "unique": [("issue_number",)],
        "required": ["issue_number", "state"],
    },
    "references": {
        "required": ["source_kind", "source_number", "reference_kind",
                     "reference_value", "strength"],
        "enum": [("strength", {"strong", "medium", "weak"})],
    },
    "feature_flags": {
        "required": ["pr_number", "flag_key", "detection"],
        "fk": [("pr_number", "pull_requests", "pr_number")],
    },
    "components": {
        "unique": [("component",)],
        "required": ["component", "platform", "source_rule"],
    },
    "path_map": {
        "unique": [("path",)],
        "required": ["path", "component", "component_rule_priority"],
    },
    "pr_change_shape": {
        "unique": [("pr_number",)],
        "required": ["pr_number", "file_count", "change_shape_version"],
        "fk": [("pr_number", "pull_requests", "pr_number")],
        "nonneg": ["file_count", "code_file_count", "test_file_count"],
    },
    "pr_blast_radius": {
        "unique": [("pr_number",)],
        "required": ["pr_number", "reachability_band", "blast_radius_version"],
        "fk": [("pr_number", "pull_requests", "pr_number")],
        "enum": [("reachability_band",
                  {"local", "component", "cross_product", "platform_wide", "unknown"})],
    },
    "candidate_episode_edges": {
        "required": ["source_pr_number", "edge_type", "strength", "evidence"],
        "fk": [("source_pr_number", "pull_requests", "pr_number")],
        "enum": [("strength", {"strong", "medium", "weak"})],
    },
    "candidate_episodes": {
        "unique": [("episode_id",)],
        "required": ["episode_id", "pr_count"],
    },
    "pr_regression_candidates": {
        "unique": [("pr_number",)],
        "required": ["pr_number", "regression_evidence_tier"],
        "fk": [("pr_number", "pull_requests", "pr_number")],
        "enum": [("regression_evidence_tier",
                  {"explicit", "linked", "proximate", "none"})],
        "range": [("survival_30d", 0.0, 1.0), ("survival_60d", 0.0, 1.0),
                  ("survival_90d", 0.0, 1.0)],
    },
    "review_intervention_candidates": {
        "unique": [("candidate_id",)],
        "required": ["candidate_id", "pr_number", "substance_class"],
        "fk": [("pr_number", "pull_requests", "pr_number")],
        "enum": [("substance_class",
                  {"substantive", "nit", "acknowledgement", "short", "bot", "empty"})],
    },
    "reviewer_intervention_rollup": {
        "unique": [("actor_id",)],
        "required": ["actor_id", "substantive_comments"],
    },
    "pr_anomalies": {
        "unique": [("pr_number",)],
        "required": ["pr_number", "anomaly_count"],
        "fk": [("pr_number", "pull_requests", "pr_number")],
    },
    "module_nodes": {
        "unique": [("path",)],
        "required": ["path", "language", "parse_status"],
        "nonneg": ["fan_in", "fan_out"],
    },
    "dependency_edges": {
        "required": ["source_path", "target_path", "resolution"],
    },
    "component_edges": {
        "unique": [("source_component", "target_component")],
        "required": ["source_component", "target_component", "edge_count"],
    },
}


def _key(row: Mapping[str, Any], columns: Sequence[str]) -> tuple:
    return tuple(row.get(c) for c in columns)


def check_table(
    name: str,
    rows: list[dict[str, Any]],
    tables: Mapping[str, list[dict[str, Any]]],
) -> list[CheckResult]:
    rules = TABLE_RULES.get(name)
    if rules is None:
        return [CheckResult("rules_defined", name, "warn",
                            "no invariant rules defined for this table")]
    if not rows:
        return [CheckResult("row_count", name, "skipped", "table is empty or absent")]

    results: list[CheckResult] = [
        CheckResult("row_count", name, "pass", f"{len(rows)} rows", count=len(rows))
    ]
    columns = set(rows[0])

    for column in rules.get("required", []):
        if column not in columns:
            results.append(
                CheckResult("schema_column_present", name, "fail",
                            f"required column '{column}' is missing")
            )
            continue
        nulls = [r for r in rows if r.get(column) is None]
        results.append(
            CheckResult(
                f"not_null:{column}", name,
                "pass" if not nulls else "fail",
                f"{len(nulls)} null values in required column '{column}'",
                offenders=[_key(r, ["pr_number"] if "pr_number" in columns else [column])
                           for r in nulls[:10]],
                count=len(nulls),
            )
        )

    for key_columns in rules.get("unique", []):
        missing = [c for c in key_columns if c not in columns]
        if missing:
            results.append(
                CheckResult(f"unique:{'+'.join(key_columns)}", name, "fail",
                            f"key columns missing: {missing}")
            )
            continue
        seen: dict[tuple, int] = {}
        for row in rows:
            k = _key(row, key_columns)
            seen[k] = seen.get(k, 0) + 1
        dupes = {k: v for k, v in seen.items() if v > 1}
        results.append(
            CheckResult(
                f"unique:{'+'.join(key_columns)}", name,
                "pass" if not dupes else "fail",
                f"{len(dupes)} duplicated key(s)",
                offenders=[list(k) for k in list(dupes)[:10]],
                count=len(dupes),
            )
        )

    for column, target_table, target_column in rules.get("fk", []):
        target_rows = tables.get(target_table) or []
        if column not in columns:
            results.append(
                CheckResult(f"fk:{column}->{target_table}", name, "fail",
                            f"column '{column}' missing")
            )
            continue
        if not target_rows:
            results.append(
                CheckResult(f"fk:{column}->{target_table}", name, "skipped",
                            f"target table '{target_table}' is empty or absent")
            )
            continue
        valid = {r.get(target_column) for r in target_rows}
        # A null FK means "not recorded" and is checked by not_null, not here.
        orphans = [
            r for r in rows
            if r.get(column) is not None and r.get(column) not in valid
        ]
        results.append(
            CheckResult(
                f"fk:{column}->{target_table}.{target_column}", name,
                "pass" if not orphans else "fail",
                f"{len(orphans)} rows reference a missing {target_table} row",
                offenders=[r.get(column) for r in orphans[:10]],
                count=len(orphans),
            )
        )

    for column in rules.get("nonneg", []):
        if column not in columns:
            continue
        bad = [
            r for r in rows
            if isinstance(r.get(column), (int, float)) and r[column] < 0
        ]
        results.append(
            CheckResult(
                f"nonneg:{column}", name, "pass" if not bad else "fail",
                f"{len(bad)} negative values in '{column}'", count=len(bad),
            )
        )

    for column, low, high in rules.get("range", []):
        if column not in columns:
            continue
        bad = [
            r for r in rows
            if isinstance(r.get(column), (int, float)) and not (low <= r[column] <= high)
        ]
        results.append(
            CheckResult(
                f"range:{column}[{low},{high}]", name,
                "pass" if not bad else "fail",
                f"{len(bad)} values outside [{low},{high}] in '{column}'",
                count=len(bad),
            )
        )

    for column, allowed in rules.get("enum", []):
        if column not in columns:
            continue
        bad = [
            r for r in rows
            if r.get(column) is not None and r.get(column) not in allowed
        ]
        results.append(
            CheckResult(
                f"enum:{column}", name, "pass" if not bad else "fail",
                f"{len(bad)} values outside {sorted(allowed)}",
                offenders=sorted({str(r.get(column)) for r in bad})[:10],
                count=len(bad),
            )
        )

    return results


def null_semantics_checks(
    tables: Mapping[str, list[dict[str, Any]]]
) -> list[CheckResult]:
    """Assert that "unavailable" is never silently encoded as zero.

    Principle 5 of the spec: missing, unknown, not-applicable and zero are
    different values. These checks are the teeth behind that claim.
    """
    out: list[CheckResult] = []

    files = tables.get("pr_files") or []
    if files:
        bad = [
            f for f in files
            if f.get("is_binary") and (f.get("additions") is not None
                                       or f.get("deletions") is not None)
        ]
        out.append(CheckResult(
            "null_semantics:binary_line_counts", "pr_files",
            "pass" if not bad else "fail",
            f"{len(bad)} binary files carry numeric line counts instead of NULL",
            count=len(bad),
        ))
        bad2 = [
            f for f in files
            if f.get("additions") is None
            and not f.get("line_counts_unavailable_reason")
        ]
        out.append(CheckResult(
            "null_semantics:unavailable_reason_present", "pr_files",
            "pass" if not bad2 else "fail",
            f"{len(bad2)} files have NULL additions with no unavailable reason",
            count=len(bad2),
        ))

    regressions = tables.get("pr_regression_candidates") or []
    if regressions:
        bad = [
            r for r in regressions
            if r.get("survival_30d") is None and not r.get("survival_30d_reason")
        ]
        out.append(CheckResult(
            "null_semantics:survival_reason_present", "pr_regression_candidates",
            "pass" if not bad else "fail",
            f"{len(bad)} rows have NULL survival_30d with no stated reason",
            count=len(bad),
        ))

    prs = tables.get("pull_requests") or []
    if prs:
        bad = [
            p for p in prs
            if not p.get("ranking_eligible") and not p.get("ranking_ineligible_reason")
        ]
        out.append(CheckResult(
            "null_semantics:ineligible_reason_present", "pull_requests",
            "pass" if not bad else "fail",
            f"{len(bad)} ineligible PRs carry no reason",
            count=len(bad),
        ))
    return out


def version_stamp_checks(
    tables: Mapping[str, list[dict[str, Any]]], expected: Mapping[str, str]
) -> list[CheckResult]:
    """Every derived row must carry the version of the code that produced it."""
    pairs = [
        ("pr_change_shape", "change_shape_version", "change_shape"),
        ("pr_blast_radius", "blast_radius_version", "blast_radius"),
        ("candidate_episode_edges", "episode_edges_version", "episode_edges"),
        ("pr_regression_candidates", "regression_version", "regression"),
        ("review_intervention_candidates", "review_intervention_version",
         "review_intervention"),
        ("pr_anomalies", "anomaly_version", "anomaly"),
        ("module_nodes", "dependency_graph_version", "dependency_graph"),
    ]
    out: list[CheckResult] = []
    for table, column, family in pairs:
        rows = tables.get(table) or []
        if not rows:
            out.append(CheckResult(f"version_stamp:{table}", table, "skipped",
                                   "table empty or absent"))
            continue
        want = expected.get(family)
        seen = {r.get(column) for r in rows}
        ok = seen == {want}
        out.append(CheckResult(
            f"version_stamp:{table}", table, "pass" if ok else "fail",
            f"expected all rows stamped {want!r}, found {sorted(map(str, seen))}",
            count=0 if ok else len(rows),
        ))
    return out


def run_all(
    tables: Mapping[str, list[dict[str, Any]]], feature_versions: Mapping[str, str]
) -> list[dict[str, Any]]:
    results: list[CheckResult] = []
    for name in TABLE_RULES:
        results.extend(check_table(name, tables.get(name) or [], tables))
    results.extend(null_semantics_checks(tables))
    results.extend(version_stamp_checks(tables, feature_versions))
    return [r.as_dict() for r in results]
