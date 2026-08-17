"""Stage: normalized -> deterministic evidence features.

Six derived tables, all model-free and all reproducible from the normalized
layer plus the dependency graph.  None of them contains a score, a weight, or a
ranking: that is Phase 2's job, and the contract in
``docs/PHASE_2_CONTRACT.md`` says so explicitly.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from typing import Any

from ..config import Settings, iso
from ..graph.build import load_graph, reverse_adjacency
from ..ingest.runs import ExtractionRun
from ..store import read_table, write_json, write_table
from . import anomaly, blast_radius, change_shape, episodes, regression
from . import review_intervention as RI

log = logging.getLogger("impact.features")

UTC = dt.timezone.utc


def run(settings: Settings) -> dict[str, Any]:
    run_rec = ExtractionRun.start(settings, "features")
    norm = settings.path("normalized")
    out = settings.path("derived")
    out.mkdir(parents=True, exist_ok=True)

    def load(name: str) -> list[dict[str, Any]]:
        return read_table(norm / f"{name}.parquet")

    prs_rows = load("pull_requests")
    prs = {int(p["pr_number"]): p for p in prs_rows}
    actors = {str(a["actor_id"]): a for a in load("actors")}
    commits = load("commits")
    commits_by_sha = {str(c["commit_sha"]): c for c in commits}
    references = load("references")
    flags = load("feature_flags")
    review_comments = load("review_comments")
    threads = {str(t["thread_id"]): t for t in load("review_threads")}

    files_by_pr: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in load("pr_files"):
        files_by_pr[int(row["pr_number"])].append(row)

    commits_by_pr: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for commit in commits:
        if commit.get("pr_number") is not None:
            commits_by_pr[int(commit["pr_number"])].append(commit)

    flags_by_pr: dict[int, set[str]] = defaultdict(set)
    for row in flags:
        if row.get("pr_number") is not None and row.get("flag_key"):
            flags_by_pr[int(row["pr_number"])].add(str(row["flag_key"]))

    issues_by_pr: dict[int, set[int]] = defaultdict(set)
    for ref in references:
        if (
            ref.get("source_kind") == "pull_request"
            and ref.get("reference_subtype") in {"github_closing_reference", "closing"}
            and str(ref.get("reference_value", "")).isdigit()
        ):
            issues_by_pr[int(ref["source_number"])].add(int(ref["reference_value"]))

    adjacency, nodes = load_graph(settings)
    reverse = reverse_adjacency(adjacency)
    log.info(
        "loaded %d PRs, %d graph nodes, %d review comments",
        len(prs), len(nodes), len(review_comments),
    )

    eligible_numbers = {
        int(p["pr_number"]) for p in prs_rows if p.get("ranking_eligible")
    }

    entropy_base = float(settings.param("change_shape", "entropy_base", 2))
    max_depth = int(settings.param("blast_radius", "max_reachability_depth", 3))

    # -- 1. change shape --------------------------------------------------
    shape_rows = [
        change_shape.compute(pr, files_by_pr.get(number, []), entropy_base=entropy_base)
        for number, pr in sorted(prs.items())
    ]

    # -- 2. blast radius ---------------------------------------------------
    blast_rows = [
        blast_radius.compute(
            pr, files_by_pr.get(number, []),
            nodes=nodes, reverse_adjacency=reverse, max_depth=max_depth,
        )
        for number, pr in sorted(prs.items())
    ]

    # -- 3. episodes --------------------------------------------------------
    edge_rows = episodes.build_edges(
        prs=prs, references=references, feature_flags=flags
    )
    episode_rows = episodes.build_episodes(edge_rows)

    # -- 4. regression / durability ----------------------------------------
    regression_rows = regression.compute(
        prs=prs,
        files_by_pr=files_by_pr,
        edges=edge_rows,
        flags_by_pr=flags_by_pr,
        issues_by_pr=issues_by_pr,
        window_end=settings.window.end,
        survival_days=tuple(settings.param("regression", "survival_days", [30, 60, 90])),
        proximity_days=int(settings.param("regression", "fix_proximity_days", 14)),
        min_path_overlap=int(settings.param("regression", "min_path_overlap", 1)),
    )

    # -- 5. review interventions -------------------------------------------
    classifier = RI.ReviewClassifier(
        (settings.features.get("parameters") or {}).get("review_intervention") or {}
    )
    intervention_rows = RI.compute_candidates(
        prs=prs,
        review_comments=review_comments,
        threads=threads,
        commits_by_pr=commits_by_pr,
        files_by_pr=files_by_pr,
        actors=actors,
        classifier=classifier,
    )
    reviewer_rows = RI.summarise_by_actor(intervention_rows)

    # -- 6. anomalies --------------------------------------------------------
    anomaly_rows = anomaly.compute(
        prs=prs,
        files_by_pr=files_by_pr,
        actors=actors,
        commits_by_sha=commits_by_sha,
        window_start=settings.window.start,
        window_end=settings.window.end,
    )

    written: dict[str, Any] = {}

    def emit(name: str, rows: list[dict[str, Any]], keys: list[str]) -> None:
        written[name] = write_table(out / f"{name}.parquet", rows, sort_keys=keys)
        log.info("wrote %-26s %7d rows", name, len(rows))

    emit("pr_change_shape", shape_rows, ["pr_number"])
    emit("pr_blast_radius", blast_rows, ["pr_number"])
    emit("candidate_episode_edges", edge_rows,
         ["source_pr_number", "edge_type", "target_number"])
    emit("candidate_episodes", episode_rows, ["episode_id"])
    emit("pr_regression_candidates", regression_rows, ["pr_number"])
    emit("review_intervention_candidates", intervention_rows, ["candidate_id"])
    emit("reviewer_intervention_rollup", reviewer_rows, ["actor_id"])
    emit("pr_anomalies", anomaly_rows, ["pr_number"])

    summary = {
        "computed_at": iso(dt.datetime.now(UTC)),
        "change_shape": change_shape.summarise(shape_rows),
        "blast_radius": blast_radius.summarise(blast_rows, eligible=eligible_numbers),
        "episodes": episodes.summarise(edge_rows, episode_rows),
        "regression": regression.summarise(regression_rows),
        "review_intervention": RI.summarise(intervention_rows),
        "anomaly": anomaly.summarise(anomaly_rows),
        "completeness_by_month": anomaly.completeness_by_month(prs_rows),
    }
    write_json(out / "_feature_summary.json", summary)

    run_rec.set("tables", {k: v["row_count"] for k, v in written.items()})
    run_rec.set("summary", summary)
    run_rec.finish("ok")
    run_rec.append_to(settings.path("raw", "extraction_runs.json"))
    return run_rec.as_row()
