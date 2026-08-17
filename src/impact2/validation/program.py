"""The ten-item validation programme.

Every item is run against the real PostHog dataset — there is no synthetic
fixture corpus, because a fixture that agrees with the code proves nothing.
Where an item requires a human (cluster audit, review causality, regression
links, finalist approval) the programme produces a deterministic, stratified
audit **queue** and reports honestly that the verdicts are outstanding.  A gate
whose human half is unfilled is ``pending``, never ``pass``.

    1  episode clustering audit          >= 30 stratified clusters + auto checks
    2  dimension rubric agreement        >= 25 episodes, two passes, kappa/alpha
    3  LLM stability                     >= 20 cases (queued without a provider)
    4  review causality verification      >= 15 candidates incl. false positives
    5  regression link verification       >= 15 candidates
    6  ranking sensitivity                weights, thresholds, discounts, coeffs
    7  adversarial tests                  five named attacks
    8  reproducibility                    same inputs -> identical content hashes
    9  claim audit                        zero orphan claims
   10  human review of the top five       explicit approval ledger

The programme writes ``reports/phase2/`` for humans and returns a machine
record that the export gate reads.  Export refuses to mark a package
``publishable`` while items 1, 4, 5 or 10 are outstanding.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..config import Phase2Config, iso, now
from ..ids import content_hash
from ..store import read_json, write_json
from ..versions import derivation_version
from . import adversarial, agreement

log = logging.getLogger("impact2.validation")

VERSION = derivation_version("validation")

REQUIREMENTS = {
    "cluster_audit": 30,
    "rubric_agreement": 25,
    "llm_stability": 20,
    "review_causality": 15,
    "regression_links": 15,
}

# Items whose human half must be recorded before a package may be published.
HUMAN_GATED = ("cluster_audit", "review_causality", "regression_links",
               "finalist_approval")


def _stratified(
    rows: Sequence[Mapping[str, Any]], *, key: Callable[[Mapping[str, Any]], Any],
    size: int,
) -> list[dict[str, Any]]:
    """Deterministic stratified sample — no RNG, so verdicts stay attached."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(key(row))].append(dict(row))
    for bucket in buckets.values():
        bucket.sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
    out: list[dict[str, Any]] = []
    index = 0
    while len(out) < size and any(len(b) > index for b in buckets.values()):
        for name in sorted(buckets):
            if len(buckets[name]) > index and len(out) < size:
                out.append(buckets[name][index])
        index += 1
    return out


def _load_verdicts(path: Path) -> dict[str, Any]:
    """Re-read a queue a human may have filled in, keyed by its id column."""
    existing = read_json(path, []) or []
    out: dict[str, Any] = {}
    for row in existing:
        key = row.get("episode_id") or row.get("intervention_id") or row.get(
            "candidate_id"
        ) or row.get("pr_number")
        if key is not None and row.get("human_verdict"):
            out[str(key)] = {
                "human_verdict": row.get("human_verdict"),
                "human_notes": row.get("human_notes"),
            }
    return out


def _queue(
    name: str, rows: Sequence[Mapping[str, Any]], *, required: int, path: Path,
    id_field: str, description: str,
) -> dict[str, Any]:
    """Write a queue, preserving any verdicts already recorded in it."""
    verdicts = _load_verdicts(path)
    enriched = [
        {**row, **verdicts.get(str(row.get(id_field)), {"human_verdict": None,
                                                        "human_notes": None})}
        for row in rows
    ]
    write_json(path, enriched)
    recorded = sum(1 for r in enriched if r.get("human_verdict"))
    # A reviewer may sign off in one action rather than item by item. That is a
    # legitimate operator decision, but it is a weaker record than 30 separate
    # judgements and the published package must not imply otherwise.
    bulk = sum(1 for r in enriched if r.get("attestation_mode") == "bulk")
    status = (
        "fail" if len(enriched) < required
        else "pass" if recorded >= required
        else "pending"
    )
    return {
        "item": name,
        "description": description,
        "status": status,
        "queued": len(enriched),
        "required": required,
        "human_verdicts_recorded": recorded,
        "bulk_attested": bulk,
        "attestation_mode": "bulk" if bulk and bulk == recorded else (
            "per_item" if recorded else None
        ),
        "queue_file": str(path.name),
        "note": (
            "The queue is generated deterministically; the verdicts are not. "
            "This item cannot pass until a human records them."
            + (" Signed off in bulk by the operator, not item by item."
               if bulk and bulk == recorded else "")
        ),
    }


# ==========================================================================
# item 1 — episode clustering
# ==========================================================================


def item_cluster_audit(pipeline: Any, reports: Path) -> dict[str, Any]:
    """Stratified across the error categories the phase spec names."""
    from ..graph import clustering as clustering_mod

    episodes = pipeline.episodes
    pair_index = clustering_mod.index_pairs_by_pr(pipeline.pair_edges)
    edge_types_by_episode: dict[str, set[str]] = defaultdict(set)
    for episode in episodes:
        members = [int(n) for n in episode["pr_numbers"]]
        for pair in clustering_mod.internal_pairs(
            members, pipeline.pair_edges, pair_index
        ):
            for edge in pipeline.pair_edges[pair]:
                if edge.get("usable_for_clustering"):
                    edge_types_by_episode[str(episode["episode_id"])].add(
                        str(edge["edge_type"])
                    )

    def stratum(episode: Mapping[str, Any]) -> str:
        eid = str(episode["episode_id"])
        types = edge_types_by_episode.get(eid, set())
        if int(episode["pr_count"]) == 1:
            return "single_pr"
        if episode.get("feature_flag_keys"):
            return "feature_flag_arc"
        if types & {"follow_up", "part_of", "stacked_branch", "depends_on"}:
            return "explicit_follow_up"
        if types & {"semantic_similarity"}:
            return "semantic_supported"
        if str(episode.get("status")) == "maintenance":
            return "generic_component_fix"
        if int(episode["pr_count"]) > 6:
            return "long_arc"
        return "other"

    multi = [e for e in episodes if int(e["pr_count"]) > 1]
    # Bias the queue towards multi-PR clusters: a single-PR "cluster" involved
    # no clustering decision and auditing it teaches nothing.
    sample = _stratified(multi, key=stratum, size=REQUIREMENTS["cluster_audit"])
    if len(sample) < REQUIREMENTS["cluster_audit"]:
        sample += _stratified(
            [e for e in episodes if int(e["pr_count"]) == 1],
            key=stratum, size=REQUIREMENTS["cluster_audit"] - len(sample),
        )

    rows = [
        {
            "episode_id": str(e["episode_id"]),
            "stratum": stratum(e),
            "title": e.get("title"),
            "pr_count": e["pr_count"],
            "pr_numbers": e["pr_numbers"],
            "pr_urls": [
                f"{pipeline.inputs.repository_url}/pull/{n}" for n in e["pr_numbers"]
            ],
            "cluster_confidence": e.get("cluster_confidence"),
            "confidence_reasons": e.get("cluster_confidence_reasons"),
            "internal_edge_types": sorted(edge_types_by_episode.get(str(e["episode_id"]), set())),
            "status": e.get("status"),
            "automated_flags": _cluster_flags(e, edge_types_by_episode),
            "question": (
                "Do these PRs belong to ONE initiative? Answer: correct | "
                "over_merged | under_split | wrong_members | unclear"
            ),
        }
        for e in sample
    ]

    result = _queue(
        "cluster_audit", rows, required=REQUIREMENTS["cluster_audit"],
        path=reports / "audit_episode_clusters.json", id_field="episode_id",
        description="Manual audit of >= 30 clusters across split initiatives, "
                    "feature-flag arcs, generic fixes and explicit follow-ups",
    )

    # Automated precision-oriented checks that need no human.
    flagged = [r for r in rows if r["automated_flags"]]
    categories: dict[str, int] = defaultdict(int)
    for row in rows:
        for flag in row["automated_flags"]:
            categories[flag] += 1
    result["automated_error_categories"] = dict(sorted(categories.items()))
    result["automatically_flagged"] = len(flagged)
    result["strata_covered"] = sorted({r["stratum"] for r in rows})
    return result


def _cluster_flags(
    episode: Mapping[str, Any], edge_types: Mapping[str, set[str]]
) -> list[str]:
    flags: list[str] = []
    eid = str(episode["episode_id"])
    types = edge_types.get(eid, set())
    if types and types <= {"semantic_similarity"}:
        flags.append("semantic_only_join")
    if types and types <= {"shared_feature_flag"}:
        flags.append("flag_only_join")
    if float(episode.get("cluster_confidence") or 1.0) < 0.55:
        flags.append("low_cluster_confidence")
    if int(episode.get("pr_count") or 0) > 12:
        flags.append("oversized_cluster")
    if (episode.get("duration_days") or 0) > 60:
        flags.append("long_span")
    if len(episode.get("components") or []) > 6:
        flags.append("many_components")
    return flags


# ==========================================================================
# item 2 — rubric agreement between two independent passes
# ==========================================================================


def item_rubric_agreement(pipeline: Any, reports: Path) -> dict[str, Any]:
    """Two machine passes with different thresholds, plus a human queue.

    Honesty note recorded in the output: pass B is a *rule variant*, not a
    second human. This measures how sensitive the bands are to the thresholds
    we chose — a real inter-rater kappa needs the human queue this item also
    emits.
    """
    from ..dimensions import rubric as rubric_mod
    from ..analytics import review_causal

    variant = pipeline.config.with_overrides(
        {
            "rubric.corroboration.band_3_min_artifact_classes": 1,
            "rubric.corroboration.band_4_min_artifact_classes": 2,
            "rubric.dimensions.engineering_leverage.thresholds": {
                "band_2_min_downstream_files": 1,
                "band_3_min_downstream_components": 3,
                "band_3_min_downstream_authors": 3,
                "band_4_min_downstream_components": 5,
                "band_4_min_downstream_authors": 6,
            },
            "rubric.dimensions.collaborative_amplification.thresholds": {
                "band_3_min_distinct_authors_helped": 2,
                "band_3_min_distinct_components": 1,
                "band_4_min_distinct_authors_helped": 6,
                "band_4_min_distinct_components": 3,
            },
        }
    )
    episode_prs = {
        str(e["episode_id"]): [int(n) for n in e["pr_numbers"]]
        for e in pipeline.episodes
    }
    evaluator_b = rubric_mod.RubricEvaluator(
        variant,
        prs=pipeline.prs, files_by_pr=pipeline.files_by_pr, issues=pipeline.issues,
        change_shape=pipeline.change_shape, blast=pipeline.blast,
        regression=pipeline.regression, threads_by_pr=pipeline.threads_by_pr,
        propagation={str(r["episode_id"]): r for r in pipeline.propagation_summary},
        novelty={str(r["episode_id"]): r for r in pipeline.novelty},
        corrective={str(r["episode_id"]): r for r in pipeline.corrective},
        interventions=review_causal.by_episode(pipeline.interventions, episode_prs),
        window_end=pipeline.inputs.window_end,
    )

    ranked = [e for e in pipeline.episodes if e.get("ranked")]
    sample = _stratified(
        ranked, key=lambda e: str(e.get("status")),
        size=max(REQUIREMENTS["rubric_agreement"], 25),
    )
    pass_b = evaluator_b.evaluate_all(sample)

    a_by_key = {
        f"{r['episode_id']}|{r['dimension']}": r.get("band")
        for r in pipeline.dimensions
    }
    b_by_key = {
        f"{r['episode_id']}|{r['dimension']}": r.get("band") for r in pass_b
    }
    shared = {k: a_by_key[k] for k in b_by_key if k in a_by_key}

    kappa = agreement.weighted_cohens_kappa(shared, b_by_key)
    alpha = agreement.krippendorff_alpha(
        {k: [shared[k], b_by_key[k]] for k in shared}
    )
    per_dimension = {}
    for dimension in rubric_mod.DIMENSIONS:
        subset_a = {k: v for k, v in shared.items() if k.endswith(f"|{dimension}")}
        subset_b = {k: b_by_key[k] for k in subset_a}
        per_dimension[dimension] = agreement.weighted_cohens_kappa(subset_a, subset_b)

    # The human half: blind band assignment for the same episodes.
    human_rows = [
        {
            "episode_id": str(e["episode_id"]),
            "title": e.get("title"),
            "problem": e.get("problem"),
            "intervention": e.get("intervention"),
            "observable_outcome": e.get("observable_outcome"),
            "pr_urls": [
                f"{pipeline.inputs.repository_url}/pull/{n}" for n in e["pr_numbers"]
            ],
            "counterevidence": [c.get("detail") for c in (e.get("counterevidence") or [])][:4],
            "machine_bands": "[hidden until you record yours]",
            "your_bands": {d: None for d in rubric_mod.DIMENSIONS},
            "question": "Assign each dimension a band 0-4, or null for unknown.",
        }
        for e in sample
    ]
    write_json(reports / "audit_rubric_blind_review.json", human_rows)

    write_json(
        reports / "rubric_pass_b_variant.json",
        {
            "overrides": {
                "band_3_min_artifact_classes": 1,
                "band_4_min_artifact_classes": 2,
                "leverage_and_collaboration_thresholds": "shifted",
            },
            "assessments": pass_b,
        },
    )

    return {
        "item": "rubric_agreement",
        "description": "Two independent rubric passes over >= 25 episodes",
        "status": (
            "pass" if (kappa.get("kappa") or 0) >= 0.67
            else "warn" if (kappa.get("kappa") or 0) >= 0.40
            else "fail"
        ),
        "episodes_compared": len(sample),
        "assessments_compared": len(shared),
        "weighted_cohens_kappa": kappa,
        "krippendorff_alpha": alpha,
        "per_dimension_kappa": per_dimension,
        "confusion_matrix": agreement.confusion(shared, b_by_key),
        "pass_b_description": (
            "A rule variant with relaxed corroboration requirements and shifted "
            "leverage/collaboration thresholds."
        ),
        "honesty_note": (
            "Both passes are machine passes. This measures the bands' "
            "sensitivity to the thresholds we chose, NOT human inter-rater "
            "agreement. The blind human queue for that is "
            "audit_rubric_blind_review.json and is unfilled."
        ),
        "human_queue_file": "audit_rubric_blind_review.json",
        "human_verdicts_recorded": 0,
    }


# ==========================================================================
# items 4 and 5 — human verification queues
# ==========================================================================


def item_review_causality(pipeline: Any, reports: Path) -> dict[str, Any]:
    """Stratified to include likely false positives, not only confident hits."""
    rows = pipeline.interventions
    if not rows:
        return {
            "item": "review_causality", "status": "skipped",
            "reason": "no review-intervention candidates in the dataset "
                      "(Phase 1 review detail may not have been extracted yet)",
            "queued": 0, "required": REQUIREMENTS["review_causality"],
        }

    def stratum(row: Mapping[str, Any]) -> str:
        return f"{row.get('causal_confidence')}|{row.get('consequence_band')}"

    sample = _stratified(rows, key=stratum, size=REQUIREMENTS["review_causality"])
    queue = [
        {
            "intervention_id": str(r["intervention_id"]),
            "url": r.get("url"),
            "pr_number": r.get("pr_number"),
            "path": r.get("path"),
            "comment": str(r.get("body_excerpt") or "")[:800],
            "machine_concern_classes": r.get("concern_classes"),
            "machine_consequence_band": r.get("consequence_band"),
            "machine_causal_confidence": r.get("causal_confidence"),
            "change_evidence": r.get("change_evidence"),
            "comment_is_outdated": r.get("comment_is_outdated"),
            "thread_is_resolved": r.get("thread_is_resolved"),
            "question": (
                "Did this comment cause a change? Answer: caused_change | "
                "no_change | cannot_tell | false_positive"
            ),
        }
        for r in sample
    ]
    result = _queue(
        "review_causality", queue, required=REQUIREMENTS["review_causality"],
        path=reports / "audit_review_causality.json", id_field="intervention_id",
        description="Manual verification of >= 15 intervention candidates, "
                    "stratified to include likely false positives",
    )
    result["strata_sampled"] = sorted({stratum(r) for r in sample})
    result["includes_low_confidence"] = any(
        r.get("causal_confidence") == "low" for r in sample
    )
    return result


def item_regression_links(pipeline: Any, reports: Path) -> dict[str, Any]:
    events = [
        {**event, "episode_id": record["episode_id"]}
        for record in pipeline.corrective
        for event in (record.get("events") or [])
    ]
    if not events:
        return {
            "item": "regression_links", "status": "skipped",
            "reason": "no corrective events in the dataset",
            "queued": 0, "required": REQUIREMENTS["regression_links"],
        }
    sample = _stratified(
        events, key=lambda e: f"{e.get('evidence_tier')}|{e.get('corrective_class')}",
        size=REQUIREMENTS["regression_links"],
    )
    queue = [
        {
            "candidate_id": f"{e.get('source_pr_number')}->{e.get('corrective_pr_number')}",
            "episode_id": e.get("episode_id"),
            "source_pr_url": f"{pipeline.inputs.repository_url}/pull/{e.get('source_pr_number')}",
            "corrective_pr_url": (
                f"{pipeline.inputs.repository_url}/pull/{e.get('corrective_pr_number')}"
            ),
            "evidence_tier": e.get("evidence_tier"),
            "machine_class": e.get("corrective_class"),
            "detail": e.get("detail"),
            "days_after": e.get("days_after"),
            "requires_human_confirmation": e.get("requires_human_confirmation"),
            "question": (
                "Is the second PR correcting the first? Answer: regression | "
                "healthy_iteration | unrelated | cannot_tell"
            ),
        }
        for e in sample
    ]
    result = _queue(
        "regression_links", queue, required=REQUIREMENTS["regression_links"],
        path=reports / "audit_regression_links.json", id_field="candidate_id",
        description="Manual verification of >= 15 regression-link candidates",
    )
    result["proximate_share"] = round(
        sum(1 for e in sample if e.get("evidence_tier") == "proximate") / len(sample), 4
    )
    return result


# ==========================================================================
# item 8 — reproducibility
# ==========================================================================


def item_reproducibility(pipeline: Any, work: Path) -> dict[str, Any]:
    """Re-derive the ranking-relevant tables and compare content hashes.

    The stronger test — two full processes from raw inputs — is the CLI's
    ``--verify-content-hashes`` path. This one proves the in-process
    computation is deterministic, which is where nondeterminism actually
    creeps in (dict ordering, set iteration, unstable sorts).
    """
    first = content_hash(pipeline.dimensions)
    second_pipeline = pipeline.clone()
    second_pipeline.stage_dimensions()
    second = content_hash(second_pipeline.dimensions)

    rank_first = [r["actor_cluster_id"] for r in pipeline.rank_once()]
    rank_second = [r["actor_cluster_id"] for r in pipeline.rank_once()]

    episode_ids_first = sorted(str(e["episode_id"]) for e in pipeline.episodes)
    reclustered = pipeline.clone()
    reclustered.stage_episodes()
    episode_ids_second = sorted(str(e["episode_id"]) for e in reclustered.episodes)

    checks = [
        {"check": "dimension_bands", "identical": first == second,
         "first": first[:16], "second": second[:16]},
        {"check": "ranking_order", "identical": rank_first == rank_second},
        {"check": "episode_ids", "identical": episode_ids_first == episode_ids_second,
         "first_count": len(episode_ids_first), "second_count": len(episode_ids_second)},
    ]
    return {
        "item": "reproducibility",
        "description": "Same inputs and config produce identical outputs",
        "status": "pass" if all(c["identical"] for c in checks) else "fail",
        "checks": checks,
        "note": (
            "Episode IDs are content-addressed over their member PRs, so an "
            "identical clustering necessarily produces identical IDs."
        ),
    }


# ==========================================================================
# item 10 — finalist approval ledger
# ==========================================================================


def item_finalist_approval(
    pipeline: Any, reports: Path, top_five: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    path = reports / "audit_finalist_approvals.json"
    existing = {
        str(r.get("actor_cluster_id")): r for r in (read_json(path, []) or [])
    }
    episodes_by_id = {str(e["episode_id"]): e for e in pipeline.episodes}
    portfolios = {str(p["actor_cluster_id"]): p for p in pipeline.portfolios}

    rows: list[dict[str, Any]] = []
    for entry in top_five:
        actor = str(entry["actor_cluster_id"])
        portfolio = portfolios.get(actor) or {}
        previous = existing.get(actor, {})
        episode_summaries = []
        for eid in (portfolio.get("episode_ids") or [])[:8]:
            episode = episodes_by_id.get(eid) or {}
            episode_summaries.append(
                {
                    "episode_id": eid,
                    "title": episode.get("title"),
                    "observable_outcome": episode.get("observable_outcome"),
                    "status": episode.get("status"),
                    "counterevidence": [
                        c.get("detail") for c in (episode.get("counterevidence") or [])
                    ][:4],
                    "pr_urls": [
                        f"{pipeline.inputs.repository_url}/pull/{n}"
                        for n in (episode.get("pr_numbers") or [])
                    ],
                }
            )
        rows.append(
            {
                "actor_cluster_id": actor,
                "login": entry.get("login"),
                "position": entry.get("position"),
                "tier": entry.get("tier"),
                "episodes": episode_summaries,
                "approved": previous.get("approved"),
                "approved_by": previous.get("approved_by"),
                "approved_at": previous.get("approved_at"),
                "notes": previous.get("notes"),
                "question": (
                    "Are every summary and every counterevidence statement here "
                    "accurate and fair? Set approved to true/false."
                ),
            }
        )
    write_json(path, rows)
    approved = sum(1 for r in rows if r.get("approved") is True)
    return {
        "item": "finalist_approval",
        "description": "Human approval of every top-five episode summary and "
                       "counterevidence statement before export",
        "status": "pass" if rows and approved == len(rows) else "pending",
        "finalists": len(rows),
        "approved": approved,
        "queue_file": path.name,
        "blocking": True,
        "note": (
            "The export package is marked publishable=false until every "
            "finalist here is approved. This is deliberate."
        ),
    }


# ==========================================================================
# orchestration
# ==========================================================================


def run(
    pipeline: Any,
    *,
    claims: Sequence[Mapping[str, Any]],
    claim_references: Sequence[str],
    sensitivity: Mapping[str, Any],
    llm_stability: Mapping[str, Any],
    top_five: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    from ..claims import audit as claim_audit

    reports = pipeline.config.paths.reports
    reports.mkdir(parents=True, exist_ok=True)
    work = pipeline.config.paths.work

    items: list[dict[str, Any]] = [
        item_cluster_audit(pipeline, reports),
        item_rubric_agreement(pipeline, reports),
        {
            "item": "llm_stability",
            "description": "Repeatability, order reversal, identity blinding and "
                           "ablation over >= 20 cases",
            "status": (
                "pending" if llm_stability.get("status") == "pending"
                else "pass" if (llm_stability.get("mean_repeat_agreement") or 0) >= 0.8
                else "warn"
            ),
            **{k: v for k, v in llm_stability.items() if k != "results"},
            "required_cases": REQUIREMENTS["llm_stability"],
        },
        item_review_causality(pipeline, reports),
        item_regression_links(pipeline, reports),
        {
            "item": "ranking_sensitivity",
            "description": "Weights, thresholds, confidence discounts, episode "
                           "coefficients and time treatment varied",
            "status": "pass" if sensitivity.get("engineers") else "fail",
            "analyses": sensitivity.get("analyses"),
            "engineers_tracked": len(sensitivity.get("engineers") or []),
            "min_top5_inclusion_probability": min(
                (
                    e.get("min_top5_inclusion_probability") or 0.0
                    for e in (sensitivity.get("engineers") or [])[:5]
                ),
                default=None,
            ),
        },
        adversarial.run_all(pipeline) | {
            "item": "adversarial",
            "description": "Episode splitting, generated migration, trivial "
                           "approvals, hidden counts, bot activity",
        },
        item_reproducibility(pipeline, work),
        claim_audit(claims, claim_references) | {
            "item": "claim_audit",
            "description": "Every dashboard claim resolves to source evidence",
        },
        item_finalist_approval(pipeline, reports, top_five),
    ]

    blocking = [
        i for i in items
        if i.get("item") in HUMAN_GATED and i.get("status") not in {"pass", "skipped"}
    ]
    failures = [i for i in items if i.get("status") == "fail"]
    status = (
        "fail" if failures
        else "pending_human_review" if blocking
        else "pass"
    )

    record = {
        "generated_at": iso(now()),
        "status": status,
        "publishable": status == "pass",
        "publishable_blockers": [
            {"item": i["item"], "status": i["status"],
             "queue_file": i.get("queue_file")}
            for i in blocking
        ],
        "failed_items": [i["item"] for i in failures],
        "items": items,
        "requirements": REQUIREMENTS,
        "validation_version": VERSION,
    }
    write_json(reports / "validation_report.json", record)
    log.info(
        "validation: %s (%d items, %d failed, %d awaiting a human)",
        status, len(items), len(failures), len(blocking),
    )
    return record
