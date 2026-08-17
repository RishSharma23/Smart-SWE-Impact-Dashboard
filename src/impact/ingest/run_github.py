"""Stage: ingest GitHub PR / review / issue data into the raw layer."""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings
from ..store import RawStore, write_json
from . import github_source as S
from .github_client import GitHubClient
from .runs import ExtractionRun

log = logging.getLogger("impact.stage.github")


def run(
    settings: Settings,
    *,
    offline: bool = False,
    skip_detail: bool = False,
    limit: int | None = None,
    workers: int = 2,
) -> dict[str, Any]:
    run_rec = ExtractionRun.start(settings, "ingest_github")
    client = GitHubClient.build(settings, offline=offline, workers=workers)
    run_rec.set("concurrency_workers", workers)

    repo_meta = S.fetch_repository(settings, client, run_rec)
    run_rec.set(
        "repository",
        {
            "nameWithOwner": repo_meta.get("nameWithOwner"),
            "defaultBranch": (repo_meta.get("defaultBranchRef") or {}).get("name"),
            "headOid": (
                (repo_meta.get("defaultBranchRef") or {}).get("target") or {}
            ).get("oid"),
            "diskUsageKb": repo_meta.get("diskUsage"),
            "licenseSpdx": (repo_meta.get("licenseInfo") or {}).get("spdxId"),
        },
    )

    discovery = S.discover(settings, client, run_rec)
    log.info("discovery: %s", discovery)

    index = RawStore(settings.path("raw", "github")).read("pr_index", "index")
    # Only PRs anchored IN the window: merged in it, or opened in it. A PR that
    # was merely updated during the window can predate it by years, and
    # fetching those would make "90 days of data" untrue.
    in_window = [
        row for row in index
        if (row.get("in_window") or {}).get("merged")
        or (row.get("in_window") or {}).get("created")
    ]
    skipped = len(index) - len(in_window)
    if skipped:
        run_rec.note(
            f"skipped {skipped} discovered PRs that are neither merged nor created "
            f"inside the window (touched-only); they remain in raw discovery data"
        )
    run_rec.set("pr_index_total", len(index))
    run_rec.set("pr_index_in_window", len(in_window))
    run_rec.set("pr_index_out_of_window_skipped", skipped)
    numbers = [int(row["number"]) for row in in_window]
    if limit:
        # Deterministic subset for smoke tests: newest N PRs.
        numbers = sorted(numbers)[-limit:]
        run_rec.note(f"LIMIT applied: only {limit} newest PRs fetched")

    cores = S.fetch_pr_core(settings, client, run_rec, numbers)
    log.info("pr_core: %d records", len(cores))

    if not skip_detail:
        S.fetch_pr_detail(settings, client, run_rec, cores)

    issue_numbers = S.referenced_issue_numbers(cores)
    issue_index = RawStore(settings.path("raw", "github")).read("issue_index", "index")
    issue_numbers |= {int(r["number"]) for r in issue_index if r.get("number")}
    if limit:
        issue_numbers = set(sorted(issue_numbers)[-limit:])
    S.fetch_issues(settings, client, run_rec, issue_numbers)

    run_rec.set("rate_limit", client.state.as_dict())
    client.flush_ledger()
    write_json(
        settings.path("raw", "github", "_rate_limit_summary.json"),
        client.state.as_dict(),
    )

    run_rec.finish("ok")
    run_rec.append_to(settings.path("raw", "extraction_runs.json"))
    log.info("github ingest done: %s", client.state.as_dict())
    return run_rec.as_row()
