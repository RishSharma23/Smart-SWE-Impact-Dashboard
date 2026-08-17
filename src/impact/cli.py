"""Command line entry point.

    python -m impact <stage> [options]

Stages run in dependency order and each is independently rerunnable:

    ingest-git      clone/verify the source, extract commits + diffs
    ingest-github   discover PRs, fetch PR core / review detail / issues
    normalize       raw -> normalized entity tables
    ingest-web      fetch PostHog docs/changelog pages referenced from PRs
    graph           build the language-aware module dependency graph
    features        normalized -> deterministic evidence features
    validate        invariants, reconciliation, quality report
    export          Phase 2 artifact package + run manifest
    all             everything above, in order
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any

from .config import load_settings, iso


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-24s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="impact", description=__doc__)
    parser.add_argument(
        "stage",
        choices=[
            "ingest-git", "ingest-github", "normalize", "ingest-web", "graph",
            "features", "validate", "export", "all",
        ],
    )
    parser.add_argument("--window-start", help="ISO-8601 UTC override")
    parser.add_argument("--window-end", help="ISO-8601 UTC override")
    parser.add_argument("--force-clone", action="store_true",
                        help="delete and re-clone the analysis source")
    parser.add_argument("--offline", action="store_true",
                        help="serve every API request from the raw cache; fail if absent")
    parser.add_argument("--skip-detail", action="store_true",
                        help="skip the review/comment detail pass (cheap smoke run)")
    parser.add_argument("--no-patches", action="store_true",
                        help="do not store commit patch text")
    parser.add_argument("--workers", type=int, default=2,
                        help="concurrent GitHub requests (default 2; see DEFAULT_WORKERS)")
    parser.add_argument("--limit", type=int,
                        help="cap the number of PRs fetched (smoke tests only)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    log = logging.getLogger("impact.cli")

    settings = load_settings(
        window_start=args.window_start, window_end=args.window_end
    )
    log.info(
        "repository=%s window=%s .. %s",
        settings.qualifier, iso(settings.window.start), iso(settings.window.end),
    )

    stages = (
        ["ingest-git", "ingest-github", "normalize", "ingest-web", "graph",
         "features", "validate", "export"]
        if args.stage == "all"
        else [args.stage]
    )

    results: dict[str, Any] = {}
    for stage in stages:
        started = time.monotonic()
        log.info("=== stage %s ===", stage)
        results[stage] = _dispatch(stage, settings, args)
        log.info("=== stage %s done in %.1fs ===", stage, time.monotonic() - started)
    return 0


def _dispatch(stage: str, settings: Any, args: argparse.Namespace) -> Any:
    if stage == "ingest-git":
        from .ingest import run_git

        return run_git.run(
            settings,
            force_clone=args.force_clone,
            with_patches=not args.no_patches,
        )
    if stage == "ingest-github":
        from .ingest import run_github

        return run_github.run(
            settings,
            offline=args.offline,
            skip_detail=args.skip_detail,
            limit=args.limit,
            workers=args.workers,
        )
    if stage == "ingest-web":
        from .ingest import web_artifacts

        return web_artifacts.run(settings, offline=args.offline)
    if stage == "normalize":
        from .normalize import build

        return build.run(settings)
    if stage == "graph":
        from .graph import build

        return build.run(settings)
    if stage == "features":
        from .features import build

        return build.run(settings)
    if stage == "validate":
        from .quality import report

        return report.run(settings, offline=args.offline)
    if stage == "export":
        from . import export

        return export.run(settings)
    raise SystemExit(f"unknown stage {stage}")


if __name__ == "__main__":
    raise SystemExit(main())
