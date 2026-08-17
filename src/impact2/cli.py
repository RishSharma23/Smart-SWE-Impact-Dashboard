"""Phase 2 command line entry point.

    python -m impact2 <stage> [options]

Stages, in dependency order, each independently rerunnable:

    verify-inputs   verify the Phase 1 manifest, row counts and hashes
    graph           tiered artifact graph (deterministic / structural / semantic)
    episodes        community detection, constraints, episode records
    analytics       propagation, decay, novelty, corrective burden, causality
    attribute       role-aware participants and shared credit
    dimensions      six evidence-banded impact dimensions
    portfolios      per-engineer portfolios (ordered weighted aggregation)
    rank            ELECTRE III outranking across every available scenario
    llm             optional semantic layer (cached, replayable, never required)
    validate        the ten-item validation programme
    export          the static package Phase 3 consumes
    all             everything above, in order

State is persisted to ``data/phase2`` between stages, so a stage can be re-run
alone.  ``all`` keeps everything in memory and is the fast path.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any

from .config import load_config
from .inputs import InputError, load_inputs
from .store import read_json, write_json, write_table
from .versions import METHODOLOGY_VERSION

log = logging.getLogger("impact2.cli")

STAGES = (
    "verify-inputs", "graph", "episodes", "analytics", "attribute",
    "dimensions", "portfolios", "rank", "llm", "validate", "export",
)

# Tables persisted between stages. Value is the sort key.
PERSISTED = {
    "artifact_edges": ["edge_uid"],
    "artifact_nodes": ["node_kind", "node_key"],
    "impact_episodes": ["episode_id"],
    "episode_artifacts": ["episode_id", "artifact_id", "relationship"],
    "episode_participants": ["participant_id"],
    "episode_dimensions": ["dimension_record_id"],
    "propagation_edges": ["propagation_edge_id"],
    "propagation_summary": ["episode_id"],
    "episode_novelty": ["episode_id"],
    "episode_corrective_burden": ["episode_id"],
    "review_interventions": ["intervention_id"],
    "engineer_portfolios": ["actor_cluster_id"],
}
JSON_COLUMNS = {
    "impact_episodes": [
        "status_reasons", "release_evidence", "pr_numbers", "issue_numbers",
        "components", "component_histogram", "products", "feature_flag_keys",
        "cluster_confidence_reasons", "sub_episode_links", "counterevidence",
        "title_evidence", "problem_evidence", "intervention_evidence",
        "outcome_evidence", "ranking_eligible_prs",
    ],
    "episode_participants": [
        "roles", "role_evidence", "direct_evidence", "share_reasons",
        "attribution_factors", "identity_ambiguity_reasons",
        "factor_scaled_dimensions",
    ],
    "episode_dimensions": [
        "evidence", "counterevidence", "confidence_reasons", "artifact_classes",
        "products_touched", "components_touched", "risk_surfaces_touched",
        "survival_unmeasurable_reasons",
    ],
    "propagation_summary": ["components_reached", "depth_histogram"],
    "episode_novelty": [
        "markers", "evidence", "distinctive_title_tokens", "uncertainty",
    ],
    "episode_corrective_burden": ["events", "by_class"],
    "review_interventions": [
        "concern_classes", "consequential_classes", "concern_terms_matched",
        "change_evidence", "causal_reasons",
    ],
    "engineer_portfolios": [
        "episode_ids", "roles_held", "share_categories", "dimension_values",
        "dimension_detail", "dimension_confidence", "dimension_intervals",
        "unknown_dimensions", "current_episode_ids", "foundational_episode_ids",
        "active_period", "eligibility_reasons", "identity_ambiguity_reasons",
    ],
    "artifact_edges": ["guards_applied"],
    "artifact_nodes": [],
    "episode_artifacts": [],
    "propagation_edges": [],
}


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="impact2", description=__doc__)
    parser.add_argument("stage", choices=[*STAGES, "all"])
    parser.add_argument(
        "--allow-unexported", action="store_true",
        help="read data/normalized and data/derived when artifacts/ has not been "
             "exported yet; results are marked PROVISIONAL and cannot be published",
    )
    parser.add_argument(
        "--verify-content-hashes", action="store_true",
        help="re-hash every Phase 1 table's rows and compare to the manifest "
             "(slow, and the honest check)",
    )
    parser.add_argument(
        "--llm", action="store_true",
        help="enable the optional semantic layer during `all`",
    )
    parser.add_argument(
        "--replay", action="store_true",
        help="serve every LLM task from cache; never call a provider",
    )
    parser.add_argument(
        "--max-calls", type=int,
        help="stop the LLM stage cleanly after N provider calls. The free tier "
             "allows 50 requests/day, so a daily job passes --max-calls 45 and "
             "resumes tomorrow: the cache makes already-done work free.",
    )
    parser.add_argument(
        "--llm-stability", action="store_true",
        help="run the LLM repeatability/order-reversal tests (60 calls). Off by "
             "default because it costs more than a day of free-tier quota.",
    )
    parser.add_argument(
        "--skip-sensitivity", action="store_true",
        help="skip bootstrap and sensitivity analysis (much faster; the export "
             "records that stability is unmeasured)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    config = load_config()
    config.paths.ensure()
    log.info(
        "phase 2 methodology %s | config %s",
        METHODOLOGY_VERSION, sorted(config.file_hashes),
    )

    try:
        inputs = load_inputs(
            config.paths,
            allow_unexported=args.allow_unexported,
            verify_content_hashes=args.verify_content_hashes,
        )
    except InputError as exc:
        log.error("%s", exc)
        return 2

    write_json(
        config.paths.work / "_input_verification.json", inputs.verification_report()
    )
    if args.stage == "verify-inputs":
        report = inputs.verification_report()
        log.info(
            "inputs %s: %d/%d tables present, %d capabilities disabled",
            report["status"], report["tables_present"], report["tables_expected"],
            len(report["capabilities_disabled"]),
        )
        for gap in inputs.known_gaps:
            log.info("known gap: %s — %s", gap.get("gap"), str(gap.get("detail"))[:120])
        return 0

    from .pipeline import Phase2Pipeline

    pipeline = Phase2Pipeline(config=config, inputs=inputs)

    stages = list(STAGES[1:]) if args.stage == "all" else [args.stage]
    if args.stage == "all" and not args.llm:
        stages = [s for s in stages if s != "llm"]

    state: dict[str, Any] = {}
    for stage in stages:
        started = time.monotonic()
        log.info("=== %s ===", stage)
        _dispatch(stage, pipeline, args, state)
        log.info("=== %s done in %.1fs ===", stage, time.monotonic() - started)

    _persist(pipeline, config)
    write_json(config.paths.work / "_run_summary.json", pipeline.manifest_block())
    return 0


def _dispatch(stage: str, pipeline: Any, args: argparse.Namespace,
              state: dict[str, Any]) -> None:
    # Stages depend on the ones before them; running one alone re-derives the
    # cheap prerequisites in memory rather than reading a half-stale table.
    if stage == "graph":
        pipeline.stage_graph()
        return
    if stage == "episodes":
        _ensure(pipeline, "graph")
        pipeline.stage_episodes()
        return
    if stage == "analytics":
        _ensure(pipeline, "episodes")
        pipeline.stage_analytics()
        return
    if stage == "attribute":
        _ensure(pipeline, "analytics")
        pipeline.stage_attribute()
        return
    if stage == "dimensions":
        _ensure(pipeline, "attribute")
        pipeline.stage_dimensions()
        return
    if stage == "portfolios":
        _ensure(pipeline, "dimensions")
        pipeline.stage_portfolios()
        return
    if stage == "rank":
        _ensure(pipeline, "dimensions")
        pipeline.stage_rank()
        return
    if stage == "llm":
        _ensure(pipeline, "dimensions")
        state["llm"] = _run_llm(pipeline, replay=args.replay, args=args)
        return
    if stage in {"validate", "export"}:
        _ensure(pipeline, "rank")
        _finalise(pipeline, args, state, do_export=stage == "export")
        return
    raise SystemExit(f"unknown stage {stage}")


def _ensure(pipeline: Any, through: str) -> None:
    """Run any prerequisite stage that has not run in this process."""
    order = ["graph", "episodes", "analytics", "attribute", "dimensions", "rank"]
    needed = order[: order.index(through) + 1]
    for stage in needed:
        if stage == "graph" and not pipeline.edges:
            pipeline.stage_graph()
        elif stage == "episodes" and not pipeline.episodes:
            pipeline.stage_episodes()
        elif stage == "analytics" and not pipeline.novelty:
            pipeline.stage_analytics()
        elif stage == "attribute" and not pipeline.participants:
            pipeline.stage_attribute()
        elif stage == "dimensions" and not pipeline.dimensions:
            pipeline.stage_dimensions()
        elif stage == "rank" and not pipeline.ranking_runs:
            pipeline.stage_rank()


def _run_llm(pipeline: Any, *, replay: bool, args: Any = None) -> dict[str, Any]:
    from .llm.provider import LLMClient
    from .llm.tasks import SemanticLayer, pending_queue

    client = LLMClient.build(pipeline.config, replay_only=replay or None)
    max_calls = getattr(args, "max_calls", None)
    if max_calls:
        # Free tier is 50 requests/day. Stopping at the cap leaves everything
        # else in the pending queue; tomorrow's run serves today's work from
        # cache and continues where this one stopped.
        client.max_calls = int(max_calls)
        log.info("LLM call budget for this run: %d", max_calls)
    layer = SemanticLayer(client, pipeline.config)
    rubric_text = str(pipeline.config.get("rubric.dimensions"))[:6000]

    artifacts_by_episode: dict[str, list[dict[str, Any]]] = {}
    for row in pipeline.episode_artifacts:
        artifacts_by_episode.setdefault(str(row["episode_id"]), []).append(row)

    extractions: list[dict[str, Any]] = []
    ranked = [e for e in pipeline.episodes if e.get("ranked")]
    for episode in ranked[:120]:
        eid = str(episode["episode_id"])
        text = "\n".join(
            f"TITLE #{n}: {(pipeline.prs.get(n) or {}).get('title_raw')}\n"
            f"BODY: {str((pipeline.prs.get(n) or {}).get('body_text') or '')[:1200]}\n"
            f"FILES: {[f.get('path') for f in pipeline.files_by_pr.get(n, [])][:25]}"
            for n in episode["pr_numbers"][:6]
        )
        extractions.append(
            layer.episode_extraction(
                episode, artifacts_by_episode.get(eid, []), text,
                {"status": episode.get("status"),
                 "components": episode.get("components")},
            )
        )

    consequences = [
        layer.review_consequence(row)
        for row in pipeline.interventions[:150]
        if row.get("is_consequential")
    ]

    if not getattr(args, "llm_stability", False):
        stability = {
            "status": "skipped",
            "reason": (
                "Operator elected to skip LLM repeatability testing to conserve "
                "free-tier quota (60 of ~155 calls, more than a day of the "
                "50/day allowance). The deterministic bands are authoritative "
                "and unaffected; what is unmeasured is how reproducible the "
                "LLM's own narrative output is."
            ),
            "required_cases": 20,
            "cases_run": 0,
            "enable_with": "--llm-stability",
        }
        write_json(pipeline.config.paths.work / "llm_stability.json", stability)
        return _llm_finish(pipeline, client, layer, extractions, consequences,
                           stability)

    stability_cases = [
        {
            "assessment": assessment,
            "artifacts": artifacts_by_episode.get(str(assessment["episode_id"]), []),
            "text": "\n".join(
                f"TITLE #{n}: {(pipeline.prs.get(n) or {}).get('title_raw')}"
                for n in (
                    next(
                        (e["pr_numbers"] for e in pipeline.episodes
                         if str(e["episode_id"]) == str(assessment["episode_id"])),
                        [],
                    )
                )[:6]
            ),
            "features": {"band": assessment.get("band")},
        }
        for assessment in pipeline.dimensions[:20]
    ]
    stability = layer.stability_tests(stability_cases, rubric_text)
    return _llm_finish(pipeline, client, layer, extractions, consequences, stability)


def _llm_finish(pipeline: Any, client: Any, layer: Any, extractions: list,
                consequences: list, stability: dict) -> dict[str, Any]:
    from .llm.tasks import pending_queue

    write_json(
        pipeline.config.paths.work / "llm_episode_extractions.json", extractions
    )
    write_json(
        pipeline.config.paths.work / "llm_review_consequences.json", consequences
    )
    write_json(pipeline.config.paths.work / "llm_stability.json", stability)
    write_json(
        pipeline.config.paths.work / "LLM_PENDING.json",
        pending_queue(client, pipeline.config),
    )
    report = client.report()
    write_json(pipeline.config.paths.work / "llm_report.json", report)
    log.info(
        "llm: provider=%s calls=%d cache_hits=%d pending=%d",
        report["provider"], report["usage"]["calls"], report["usage"]["cache_hits"],
        report["pending_tasks"],
    )
    return {
        "report": report,
        "pending": pending_queue(client, pipeline.config),
        "stability": stability,
        "disagreements": layer.disagreements,
    }


def _finalise(
    pipeline: Any, args: argparse.Namespace, state: dict[str, Any], *, do_export: bool
) -> None:
    """Claims -> sensitivity -> validation -> export. Shared by both stages."""
    from .claims import ClaimRegistry
    from .export import Exporter
    from .rank import sensitivity as sensitivity_mod
    from .validation import program as validation_program

    config = pipeline.config
    registry = ClaimRegistry(repo_url=pipeline.inputs.repository_url)
    claim_index: dict[str, Any] = {
        "episodes": {}, "dimensions": {}, "participants": {}, "portfolios": {},
        "comparisons": {}, "stability": {}, "limitations": [],
    }

    for episode in pipeline.episodes:
        claim_index["episodes"][str(episode["episode_id"])] = registry.from_episode(episode)
    for assessment in pipeline.dimensions:
        identifier = registry.from_dimension(assessment)
        if identifier:
            claim_index["dimensions"][str(assessment["dimension_record_id"])] = identifier
    for participant in pipeline.participants:
        if participant.get("has_any_evidence"):
            claim_index["participants"][str(participant["participant_id"])] = (
                registry.from_participant(participant)
            )
    for portfolio in pipeline.portfolios:
        claim_index["portfolios"][str(portfolio["actor_cluster_id"])] = (
            registry.from_portfolio(portfolio)
        )
    for run in pipeline.ranking_runs:
        if not run.get("available"):
            continue
        top = {str(r["actor_cluster_id"]) for r in (run.get("ranking") or [])[:5]}
        for comparison in run.get("comparisons") or []:
            if str(comparison.get("a")) in top and str(comparison.get("b")) in top:
                identifier = registry.from_comparison(
                    comparison, scenario=str(run["scenario"])
                )
                if identifier:
                    claim_index["comparisons"][
                        f"{run['scenario']}:{comparison['a']}vs{comparison['b']}"
                    ] = identifier
    claim_index["limitations"] = registry.limitations(
        config.get("eligibility.limitations.items"),
        derivation="config/phase2/eligibility.yaml::limitations",
    )

    # -- stability ---------------------------------------------------------
    if args.skip_sensitivity:
        sensitivity: dict[str, Any] = {
            "engineers": [], "analyses": [],
            "skipped": True,
            "reason": "--skip-sensitivity was passed; rank stability is UNMEASURED",
        }
    else:
        base_weights = config.criterion_weights("balanced")
        episodes_by_actor = pipeline.episodes_by_actor()
        bootstrap = sensitivity_mod.bootstrap_stability(
            config,
            actors=sorted(episodes_by_actor),
            episodes_by_actor=episodes_by_actor,
            rebuild=lambda sample: pipeline.rank_once(
                participants=pipeline.participants_for_sample(sample),
                dimensions=pipeline.dimensions_for_sample(sample),
                episodes=pipeline.episodes_for_sample(sample),
            ),
        )
        weights = sensitivity_mod.weight_sensitivity(
            config, base_weights=base_weights,
            rank_with=lambda w, t: pipeline.rank_once(weights=w, thresholds=t),
        )
        structural = sensitivity_mod.structural_sensitivity(
            config, base_weights=base_weights,
            rank_with_variant=lambda v: pipeline.rank_once(
                config=(
                    config.with_overrides(
                        {k: val for k, val in v["overrides"].items()
                         if not k.startswith("_")}
                    )
                ),
                thresholds=v["overrides"].get("thresholds"),
                decay_mode=v["overrides"].get("_decay_mode", "decayed"),
            ),
        )
        sensitivity = sensitivity_mod.combine(bootstrap, weights, structural)

    for record in sensitivity.get("engineers") or []:
        portfolio = next(
            (p for p in pipeline.portfolios
             if str(p["actor_cluster_id"]) == str(record["actor_cluster_id"])),
            {},
        )
        identifier = registry.from_stability(record, login=portfolio.get("login"))
        if identifier:
            claim_index["stability"][str(record["actor_cluster_id"])] = identifier

    write_json(config.paths.work / "sensitivity.json", sensitivity)

    # -- validation ----------------------------------------------------------
    balanced = next(
        (r for r in pipeline.ranking_runs
         if r.get("scenario") == "balanced" and r.get("available")),
        None,
    )
    top_five = (balanced or {}).get("ranking", [])[:5]
    llm_state = state.get("llm") or {}
    validation = validation_program.run(
        pipeline,
        claims=registry.all(),
        claim_references=_claim_references(claim_index),
        sensitivity=sensitivity,
        llm_stability=llm_state.get("stability") or {
            "status": "pending",
            "reason": "the LLM stage was not run in this invocation",
        },
        top_five=top_five,
    )
    write_json(config.paths.work / "claims.json", registry.all())
    write_json(config.paths.work / "claims_summary.json", registry.summarise())

    if not do_export:
        return

    exporter = Exporter(
        config, pipeline,
        claims=registry.all(), claim_index=claim_index, validation=validation,
        sensitivity=sensitivity,
        llm_report=llm_state.get("report") or {
            "provider": None, "available": False,
            "note": "the LLM stage was not run in this invocation",
        },
        llm_pending=llm_state.get("pending") or {"status": "not_run", "queued": 0},
    )
    manifest = exporter.run()
    log.info(
        "export: %d episodes, %d engineers, %d claims -> %s (publishable=%s)",
        manifest["counts"]["episodes"], manifest["counts"]["engineers"],
        manifest["counts"]["claims"], config.paths.export.name,
        manifest["publishable"],
    )


def _claim_references(claim_index: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for value in claim_index.values():
        if isinstance(value, list):
            out.extend(str(v) for v in value if v)
        elif isinstance(value, dict):
            for inner in value.values():
                if isinstance(inner, list):
                    out.extend(str(v) for v in inner if v)
                elif isinstance(inner, dict):
                    out.extend(str(v) for v in inner.values() if v)
                elif inner:
                    out.append(str(inner))
    return out


def _persist(pipeline: Any, config: Any) -> None:
    """Mirror in-memory state to data/phase2 so a stage can be re-run alone."""
    tables = {
        "artifact_edges": pipeline.edges,
        "artifact_nodes": pipeline.nodes,
        "impact_episodes": pipeline.episodes,
        "episode_artifacts": pipeline.episode_artifacts,
        "episode_participants": pipeline.participants,
        "episode_dimensions": pipeline.dimensions,
        "propagation_edges": pipeline.propagation_edges,
        "propagation_summary": pipeline.propagation_summary,
        "episode_novelty": pipeline.novelty,
        "episode_corrective_burden": pipeline.corrective,
        "review_interventions": pipeline.interventions,
        "engineer_portfolios": pipeline.portfolios,
    }
    for name, rows in tables.items():
        if not rows:
            continue
        try:
            write_table(
                config.paths.work / f"{name}.parquet", rows,
                sort_keys=PERSISTED[name],
                json_columns=JSON_COLUMNS.get(name, []),
            )
        except Exception as exc:  # noqa: BLE001 - persistence must not lose a run
            log.warning("could not persist %s: %s", name, str(exc)[:200])
    for name, payload in (
        ("cluster_audit_log", pipeline.cluster_audit),
        ("episode_review_queue", pipeline.episode_review_queue),
        ("ranking_runs", pipeline.ranking_runs),
        ("scenarios", pipeline.scenarios),
    ):
        if payload:
            write_json(config.paths.work / f"{name}.json", payload)


if __name__ == "__main__":
    raise SystemExit(main())
