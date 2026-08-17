"""Stage: ingest the Git source into the raw layer."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from ..config import Settings
from ..store import RawStore, write_json
from ..versions import EXTRACTOR_VERSION
from . import git_source as G
from .runs import ExtractionRun

log = logging.getLogger("impact.stage.git")


def run(
    settings: Settings,
    *,
    force_clone: bool = False,
    with_patches: bool = True,
    patch_limit: int = 6000,
) -> dict[str, Any]:
    run_rec = ExtractionRun.start(settings, "ingest_git")
    raw = RawStore(settings.path("raw", "git_extract"))

    clone_info = G.ensure_clone(settings, force=force_clone)
    log.info(
        "clone ready: %s @ %s (%d commits available, shallow=%s)",
        clone_info["repository_url"], clone_info["analyzed_head_sha"][:12],
        clone_info["commit_count_available"], clone_info["is_shallow"],
    )
    if not clone_info["linear_history"]:
        run_rec.note(
            "history is NOT linear; the 'one commit == one squash-merged PR' "
            "shortcut does not hold and pr_files must be rebuilt from merge ranges"
        )

    head = clone_info["analyzed_head_sha"]
    snapshots = G.snapshot_config_files(settings, head)
    raw.write("config_snapshots", "head", snapshots)
    run_rec.set(
        "config_snapshots",
        {
            "present": sum(1 for s in snapshots if s["status"] == "present"),
            "missing": sum(1 for s in snapshots if s["status"] == "missing"),
            "missing_paths": [
                s["path"] for s in snapshots if s["status"] == "missing"
            ][:20],
        },
    )

    # A buffer before window_start keeps commits belonging to PRs that opened
    # earlier but merged inside the window.
    buffer_days = int(settings.clone.get("shallow_since_buffer_days", 30))
    since = settings.window.start - dt.timedelta(days=buffer_days)

    commits = list(G.iter_commit_metadata(settings, since=since))
    by_month: dict[str, list[dict[str, Any]]] = {}
    for commit in commits:
        month = (commit["committed_at"] or "unknown")[:7]
        by_month.setdefault(month, []).append(commit)
    for month, rows in by_month.items():
        raw.write("commits", month, rows)
    log.info("extracted %d commits across %d months", len(commits), len(by_month))

    files = list(G.iter_commit_files(settings, since=since))
    commit_month = {c["commit_sha"]: (c["committed_at"] or "unknown")[:7] for c in commits}
    files_by_month: dict[str, list[dict[str, Any]]] = {}
    orphan_files = 0
    for record in files:
        month = commit_month.get(record.get("commit_sha") or "")
        if month is None:
            orphan_files += 1
            month = "unattributed"
        files_by_month.setdefault(month, []).append(record)
    for month, rows in files_by_month.items():
        raw.write("commit_files", month, rows)
    log.info("extracted %d commit-file records", len(files))

    run_rec.set("commit_count", len(commits))
    run_rec.set("commit_file_count", len(files))
    run_rec.set("orphan_file_records", orphan_files)
    run_rec.set(
        "binary_file_records", sum(1 for f in files if f.get("is_binary"))
    )
    run_rec.set(
        "rename_records",
        sum(1 for f in files if f.get("change_status") in {"R", "C"}),
    )
    run_rec.set(
        "commits_with_pr_suffix",
        sum(1 for c in commits if c.get("pr_number_from_subject")),
    )
    run_rec.set(
        "commits_with_co_authors", sum(1 for c in commits if c["co_author_count"] > 0)
    )
    run_rec.set("reverts", sum(1 for c in commits if c["is_revert"]))

    # Feature-flag evidence needs diff text, but only from commits that touch a
    # flag. One filtered pass instead of patching every commit.
    flag_diffs = list(G.iter_flag_diffs(settings, since=settings.window.start))
    raw.write("flag_diffs", "window", flag_diffs)
    run_rec.set(
        "flag_diffs",
        {
            "commits_touching_flag_references": len(flag_diffs),
            "truncated": sum(1 for d in flag_diffs if d["truncated"]),
            "pattern": G.FLAG_DIFF_PATTERN,
        },
    )
    log.info("flag diffs: %d commits touch a feature-flag reference", len(flag_diffs))

    if with_patches:
        # Patch text is only worth storing for commits inside the window, and
        # only up to a cap so the raw layer stays laptop-sized.
        window_shas = [
            c["commit_sha"]
            for c in sorted(commits, key=lambda c: c["committed_at"] or "")
            if c["committed_at"] and c["committed_at"] >= (
                settings.window.start.isoformat().replace("+00:00", "Z")
            )
        ][:patch_limit]
        patches = list(G.collect_patches(settings, window_shas))
        raw.write("commit_patches", "window", patches)
        stored = sum(1 for p in patches if p["patch_text"])
        run_rec.set(
            "patches",
            {
                "attempted": len(patches),
                "stored": stored,
                "unavailable": len(patches) - stored,
                "cap_applied": len(window_shas) >= patch_limit,
            },
        )

    write_json(settings.path("raw", "git_extract", "clone_info.json"), clone_info)
    run_rec.set("clone", clone_info)
    run_rec.set("extractor_version", EXTRACTOR_VERSION)
    run_rec.finish("ok")
    run_rec.append_to(settings.path("raw", "extraction_runs.json"))
    return run_rec.as_row()
