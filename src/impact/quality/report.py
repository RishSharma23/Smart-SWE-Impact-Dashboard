"""Stage: validate -- run every quality gate and write ``_quality_report.json``.

This module is the *orchestrator*.  The actual checks live in
:mod:`impact.quality.invariants` (schema, null semantics, uniqueness, foreign
keys, version stamps) and :mod:`impact.quality.reconcile` (independent counts,
GitHub-vs-Git change size, commit coverage, window boundary).  What is added
here are the gates that need the whole dataset at once:

* **reproducibility**   -- recompute every table's content hash from the rows on
  disk and compare it against the sidecar written when the table was produced.
  A mismatch means the Parquet file and its metadata disagree, which is the
  cheap, always-available half of "two runs produce identical hashes".
* **resume-without-gaps** -- every request in the raw-page ledger either
  succeeded or has a recorded terminal reason; no shard is silently absent.
* **fixtures**          -- conventional-title and identity-clustering fixtures
  are asserted against the live parser/resolver, so a regression in either is
  caught without a network call.
* **stratified samples**  -- 30 PRs, 10 regression candidates and 10
  review-intervention candidates are drawn deterministically and written to
  ``reports/`` as *audit queues*.  The gate reports that the queue exists and
  is the right size; it does **not** claim a human has read it.  Whether the
  human verdicts have been recorded is reported separately as
  ``manual_audit_recorded``.
* **secret scan**       -- no generated artifact may contain something shaped
  like a token.

Every gate returns ``pass``/``warn``/``fail``/``skipped`` with a detail string.
A gate that cannot run is ``skipped``, never ``pass`` -- the phase spec is
explicit that absence of evidence must not read as success.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..config import Settings, iso
from ..hashing import content_hash
from ..ingest.runs import ExtractionRun
from ..normalize.title_parser import parse_title
from ..store import read_json, read_table, table_meta, write_json
from ..versions import FEATURE_VERSIONS
from . import invariants, reconcile

log = logging.getLogger("impact.quality")

UTC = dt.timezone.utc

NORMALIZED = (
    "actors", "pull_requests", "commits", "commit_parents", "pr_files",
    "reviews", "review_threads", "review_comments", "comments", "issues",
    "references", "feature_flags", "components", "path_map",
    "raw_pages", "extraction_runs",
)
DERIVED = (
    "pr_change_shape", "pr_blast_radius", "candidate_episode_edges",
    "candidate_episodes", "pr_regression_candidates",
    "review_intervention_candidates", "reviewer_intervention_rollup",
    "pr_anomalies", "dependency_edges", "module_nodes", "component_edges",
)

# Anything shaped like a credential must never reach an artifact.  Patterns are
# deliberately broad; a false positive costs one look, a false negative leaks.
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

# Titles whose parse is asserted on every validate run.  These encode the
# PostHog-specific exceptions found in phase 1 (conventional reverts, merge
# queue artifacts) so a parser change cannot silently undo them.
TITLE_FIXTURES: tuple[tuple[str, str, str | None], ...] = (
    ("feat(insights): add funnel breakdown", "feat", "insights"),
    ("fix: null check in ingestion", "fix", None),
    ("revert(billing): remove usage cap", "revert", "billing"),
    ("chore(deps): bump posthog-js", "chore", "deps"),
    ("trunk-merge/pr-83501/1a2b3c4d", None, None),
)


def _gate(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"gate": name, "status": status, "detail": detail, **extra}


def _stratified_sample(
    rows: Iterable[Mapping[str, Any]], *, strata_key, size: int
) -> list[dict[str, Any]]:
    """Deterministic stratified sample: sort inside each stratum, round-robin.

    No RNG: the same dataset must always produce the same audit queue, or a
    reviewer's recorded verdicts stop matching the rows they looked at.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(str(strata_key(row)), []).append(dict(row))
    for bucket in buckets.values():
        bucket.sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
    out: list[dict[str, Any]] = []
    index = 0
    while len(out) < size and any(len(b) > index for b in buckets.values()):
        for key in sorted(buckets):
            if len(buckets[key]) > index and len(out) < size:
                out.append(buckets[key][index])
        index += 1
    return out


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------


def gate_reproducibility(settings: Settings) -> dict[str, Any]:
    """Recompute each table's content hash and compare to its sidecar."""
    checked: list[dict[str, Any]] = []
    mismatched: list[str] = []
    for table in NORMALIZED + DERIVED:
        layer = "normalized" if table in NORMALIZED else "derived"
        path = settings.path(layer, f"{table}.parquet")
        meta = table_meta(path)
        if not path.exists() or not meta:
            continue
        rows = read_table(path)
        recomputed = content_hash(rows, exclude=meta.get("hash_excludes", ()))
        agrees = recomputed == meta.get("content_sha256")
        if not agrees:
            mismatched.append(table)
        checked.append(
            {
                "table": table,
                "rows": len(rows),
                "recorded": meta.get("content_sha256"),
                "recomputed": recomputed,
                "agrees": agrees,
            }
        )
    if not checked:
        return _gate("reproducibility", "skipped", "no tables on disk")
    return _gate(
        "reproducibility",
        "pass" if not mismatched else "fail",
        f"{len(checked)} tables rehashed from disk; {len(mismatched)} disagree "
        "with their sidecar",
        tables_checked=len(checked),
        mismatched=mismatched,
        note=(
            "This proves stored bytes and stored metadata agree. The stronger "
            "two-full-runs comparison is `impact all` twice with the same "
            "--window-start; the content hashes here are what it compares."
        ),
    )


def gate_resume_without_gaps(settings: Settings) -> dict[str, Any]:
    ledger = read_json(settings.path("raw", "github") / "_ledger.json", {}) or {}
    entries = list(ledger.values()) if isinstance(ledger, dict) else list(ledger)
    if not entries:
        return _gate("resume_without_gaps", "skipped", "raw-page ledger is empty")
    bad = [
        e for e in entries
        if str(e.get("status")) not in {"ok", "200", "cached"}
        and not e.get("terminal_reason")
    ]
    return _gate(
        "resume_without_gaps",
        "pass" if not bad else "fail",
        f"{len(entries)} ledger entries; {len(bad)} neither succeeded nor "
        "carry a terminal reason",
        ledger_entries=len(entries),
        unexplained=len(bad),
        examples=[e.get("request_hash") for e in bad[:10]],
    )


def gate_title_fixtures() -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for title, want_prefix, want_scope in TITLE_FIXTURES:
        parsed = parse_title(title, "")
        got_prefix = parsed.prefix_normalized
        got_scope = parsed.scope
        if title.startswith("trunk-merge/"):
            if parsed.title_class != "merge_queue_artifact" or parsed.confidence != 0.0:
                failures.append(
                    {"title": title, "expected": "merge_queue_artifact@0.0",
                     "got": f"{parsed.title_class}@{parsed.confidence}"}
                )
            continue
        if got_prefix != want_prefix or (want_scope and got_scope != want_scope):
            failures.append(
                {"title": title,
                 "expected": f"{want_prefix}({want_scope})",
                 "got": f"{got_prefix}({got_scope})"}
            )
    return _gate(
        "conventional_title_fixtures",
        "pass" if not failures else "fail",
        f"{len(TITLE_FIXTURES)} fixtures; {len(failures)} failed",
        failures=failures,
    )


def gate_identity_fixtures(actors: list[dict[str, Any]]) -> dict[str, Any]:
    """Display name must never be a merge key; shared emails must be flagged."""
    if not actors:
        return _gate("identity_fixtures", "skipped", "actors table is empty")
    by_cluster: dict[str, list[dict[str, Any]]] = {}
    for actor in actors:
        by_cluster.setdefault(str(actor.get("identity_cluster_id")), []).append(actor)

    # A cluster may only exist because of a login, an email or a noreply id --
    # never because two people share a display name.
    name_only_merges = []
    for cluster, members in by_cluster.items():
        if len(members) < 2:
            continue
        logins = {m.get("login") for m in members if m.get("login")}
        emails: set[str] = set()
        overlapping = False
        for member in members:
            member_emails = set(member.get("emails") or [])
            if emails & member_emails:
                overlapping = True
            emails |= member_emails
        if len(logins) > 1 and not overlapping:
            name_only_merges.append(cluster)

    shared_email_flagged = sum(
        1 for a in actors
        if any(str(r).startswith("shared_email:") for r in (a.get("ambiguity_reasons") or []))
    )
    return _gate(
        "identity_fixtures",
        "pass" if not name_only_merges else "fail",
        f"{len(by_cluster)} clusters; {len(name_only_merges)} merged two logins "
        f"without shared-email evidence; {shared_email_flagged} actors carry a "
        "shared_email ambiguity flag",
        clusters=len(by_cluster),
        suspicious_clusters=name_only_merges[:10],
        shared_email_flagged=shared_email_flagged,
    )


def gate_secret_scan(settings: Settings) -> dict[str, Any]:
    roots = [settings.project_root / "artifacts", settings.path("derived"),
             settings.path("normalized"), settings.project_root / "reports"]
    hits: list[dict[str, Any]] = []
    scanned = 0
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            scanned += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    hits.append({"file": str(path.relative_to(settings.project_root)),
                                 "pattern": pattern.pattern})
    return _gate(
        "secret_scan",
        "pass" if not hits else "fail",
        f"{scanned} JSON artifacts scanned; {len(hits)} credential-shaped matches",
        matches=hits[:10],
    )


def gate_audit_queue(
    name: str, rows: list[dict[str, Any]], *, required: int, path: Path
) -> dict[str, Any]:
    """Report queue size and whether human verdicts have been recorded."""
    write_json(path, rows)
    verdicts = sum(1 for r in rows if r.get("human_verdict"))
    status = "pass" if len(rows) >= required else "warn"
    return _gate(
        f"audit_queue:{name}",
        status,
        f"{len(rows)}/{required} rows queued at {path.name}; "
        f"{verdicts} carry a recorded human verdict",
        queued=len(rows),
        required=required,
        manual_audit_recorded=verdicts,
        file=str(path.name),
        note="Queue existence is automated; the verdicts are not.",
    )


# --------------------------------------------------------------------------
# stage entry point
# --------------------------------------------------------------------------


def run(settings: Settings, *, offline: bool = False) -> dict[str, Any]:
    run_rec = ExtractionRun.start(settings, "validate")
    reports = settings.project_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    tables: dict[str, list[dict[str, Any]]] = {}
    for table in NORMALIZED:
        tables[table] = read_table(settings.path("normalized", f"{table}.parquet"))
    for table in DERIVED:
        tables[table] = read_table(settings.path("derived", f"{table}.parquet"))

    prs = tables.get("pull_requests") or []
    log.info("validating %d tables (%d PRs)", len(tables), len(prs))

    checks = invariants.run_all(tables, FEATURE_VERSIONS)

    gates: list[dict[str, Any]] = [
        gate_reproducibility(settings),
        gate_resume_without_gaps(settings),
        gate_title_fixtures(),
        gate_identity_fixtures(tables.get("actors") or []),
    ]

    # -- window boundary + reconciliation ---------------------------------
    boundary = reconcile.window_boundary_report(prs, settings)
    gates.append(_gate("window_boundary", boundary["status"],
                       f"{boundary['merged_in_window']} merged-in-window PRs, "
                       f"{boundary['boundary_violations']} boundary violations",
                       detail_record=boundary))

    coverage = reconcile.commit_coverage(prs)
    gates.append(_gate("merge_commit_coverage", coverage.get("status", "skipped"),
                       json.dumps({k: v for k, v in coverage.items() if k != "note"}),
                       detail_record=coverage))

    size = reconcile.reconcile_change_size(tables.get("pr_anomalies") or [])
    gates.append(_gate("change_size_reconciliation", size.get("status", "skipped"),
                       f"agreement_rate={size.get('agreement_rate')}",
                       detail_record=size))

    independent: dict[str, Any] = {"available": False, "reason": "offline"}
    if not offline:
        try:
            from ..ingest.github_client import GitHubClient

            client = GitHubClient.build(settings, offline=False, workers=1)
            independent = reconcile.independent_pr_count(settings, client)
        except Exception as exc:  # noqa: BLE001 - reconciliation must not abort
            independent = {"available": False, "reason": str(exc)[:200]}
    counts = reconcile.reconcile_counts(
        sum(1 for p in prs if p.get("merged_in_window")), independent
    )
    gates.append(_gate("independent_pr_count", counts.get("status", "skipped"),
                       json.dumps({k: v for k, v in counts.items() if k != "note"}),
                       detail_record=counts))

    # -- audit queues ------------------------------------------------------
    eligible = [p for p in prs if p.get("ranking_eligible")]
    gates.append(
        gate_audit_queue(
            "stratified_prs",
            [
                {
                    "pr_number": p.get("pr_number"),
                    "url": p.get("url"),
                    "title": p.get("title_raw"),
                    "title_prefix": p.get("title_prefix"),
                    "author_login": p.get("author_login"),
                    "author_is_bot": p.get("author_is_bot"),
                    "git_file_count": p.get("git_file_count"),
                    "human_verdict": None,
                }
                for p in _stratified_sample(
                    eligible, strata_key=lambda r: r.get("title_prefix"), size=30
                )
            ],
            required=30,
            path=reports / "audit_stratified_prs.json",
        )
    )
    regressions = [
        r for r in (tables.get("pr_regression_candidates") or [])
        if r.get("regression_evidence_tier") != "none"
    ]
    gates.append(
        gate_audit_queue(
            "regression_candidates",
            [
                {
                    "pr_number": r.get("pr_number"),
                    "tier": r.get("regression_evidence_tier"),
                    "requires_human_confirmation": r.get("requires_human_confirmation"),
                    "explicit": r.get("explicit_regression_signals"),
                    "linked": r.get("linked_fix_candidates"),
                    "proximate": (r.get("proximate_fix_candidates") or [])[:3],
                    "human_verdict": None,
                }
                for r in _stratified_sample(
                    regressions,
                    strata_key=lambda r: r.get("regression_evidence_tier"),
                    size=10,
                )
            ],
            required=10,
            path=reports / "audit_regression_candidates.json",
        )
    )
    interventions = [
        r for r in (tables.get("review_intervention_candidates") or [])
        if r.get("is_intervention_candidate")
    ]
    gates.append(
        gate_audit_queue(
            "review_interventions",
            [
                {
                    "candidate_id": r.get("candidate_id"),
                    "url": r.get("url"),
                    "commenter_login": r.get("commenter_login"),
                    "substance_class": r.get("substance_class"),
                    "safety_categories": r.get("safety_categories"),
                    "followed_by_change_in_path": r.get("followed_by_change_in_path"),
                    "body_text": (r.get("body_text") or "")[:600],
                    "human_verdict": None,
                }
                for r in _stratified_sample(
                    interventions,
                    strata_key=lambda r: (r.get("safety_categories") or ["none"])[0],
                    size=10,
                )
            ],
            required=10,
            path=reports / "audit_review_interventions.json",
        )
    )

    graph_cov = read_json(settings.path("derived", "_graph_coverage.json"), {}) or {}
    resolution = (graph_cov.get("typescript_javascript") or {}).get("resolution_rate")
    gates.append(
        _gate(
            "graph_parser_coverage",
            "pass" if graph_cov else "skipped",
            f"{graph_cov.get('graph_nodes')} nodes, "
            f"{graph_cov.get('internal_edges')} edges, "
            f"{graph_cov.get('not_parsed_files')} files with no parser",
            detail_record=graph_cov,
        )
    )

    gates.append(gate_secret_scan(settings))

    # -- known gaps (contract §7): structural, always reported -------------
    clone_info = read_json(settings.path("raw", "git_extract", "clone_info.json"), {}) or {}
    known_gaps = [
        {
            "gap": "shallow_clone",
            "detail": (
                f"clone strategy {clone_info.get('clone_strategy')}; oldest available "
                f"commit {clone_info.get('oldest_available_commit_at')}. Survival and "
                "reachability look forward only."
            ),
            "consequence": "has_merge_commit_in_clone=false PRs have no pr_files rows",
        },
        {
            "gap": "unparsed_languages",
            "detail": f"{graph_cov.get('not_parsed_files')} files in "
                      f"{graph_cov.get('not_parsed_languages')} have no import parser",
            "consequence": "reachability_band='unknown' for changes confined to them",
        },
        {
            "gap": "dynamic_imports_invisible",
            "detail": "module_nodes.has_dynamic_imports flags affected files",
            "consequence": "graph reach is a lower bound",
        },
        {
            "gap": "review_thread_pagination",
            "detail": f"{sum(1 for t in (tables.get('review_threads') or []) if t.get('comments_truncated'))} "
                      "threads truncated at the pagination cap",
            "consequence": "comment-level evidence incomplete on those threads",
        },
        {
            "gap": "survival_beyond_window",
            "detail": "survival_* is NULL with a reason when the checkpoint falls "
                      "after the window end",
            "consequence": "never read a NULL survival as 0",
        },
        {
            "gap": "review_comment_semantics",
            "detail": "semantic consequence of review comments is Phase 2 work",
            "consequence": "Phase 1 emits candidates only",
        },
    ]

    failed = [g for g in gates if g["status"] == "fail"]
    failed_checks = [c for c in checks if c["status"] == "fail"]
    status = (
        "fail" if failed or failed_checks
        else "warn" if any(g["status"] == "warn" for g in gates)
        else "pass"
    )

    report = {
        "generated_at": iso(dt.datetime.now(UTC)),
        "status": status,
        "gates": gates,
        "invariant_checks": checks,
        "invariant_summary": {
            s: sum(1 for c in checks if c["status"] == s)
            for s in ("pass", "fail", "warn", "skipped")
        },
        "known_gaps": known_gaps,
        "tables": {name: len(rows) for name, rows in tables.items()},
    }
    write_json(settings.path("derived", "_quality_report.json"), report)
    write_json(reports / "quality_report.json", report)

    run_rec.set("status", status)
    run_rec.set("gates_failed", [g["gate"] for g in failed])
    run_rec.set("invariant_failures", len(failed_checks))
    run_rec.finish("ok" if status != "fail" else "failed")
    run_rec.append_to(settings.path("raw", "extraction_runs.json"))
    log.info(
        "validate: %s (%d gates, %d invariant checks, %d failures)",
        status, len(gates), len(checks), len(failed) + len(failed_checks),
    )
    return run_rec.as_row()
