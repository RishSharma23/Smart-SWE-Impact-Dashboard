#!/usr/bin/env python3
"""Generate the synthetic Phase 3 fixture package.

Phase 3 cannot wait for Phase 2's real run to finish, so this writes a tiny,
complete, deliberately awkward package with the exact shapes in
``contracts/PHASE_3_CONTRACT.md``.  Every rendering rule that is easy to get wrong is
represented by at least one row:

* an engineer with ``rankable: false``;
* a dimension with ``value: null`` and an ``unknown_reason``;
* an episode that merged but has ``release_corroboration: "merged_only"``;
* counterevidence carrying ``requires_human_confirmation``;
* a pairwise comparison with an excluded (unknown) criterion;
* two engineers sharing a tier and listed as mutually incomparable;
* a scenario with ``available: false`` and the exact remedy command;
* ``publishable: false`` with blockers.

The data is fictional. Logins are obviously synthetic so a fixture screenshot
can never be mistaken for a statement about a real person.

    python scripts/make_phase3_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "fixtures" / "phase3"
REPO = "https://github.com/PostHog/posthog"
QUALIFIER = "github.com/PostHog/posthog"

DIMENSIONS = [
    "product_outcome", "reliability_risk", "engineering_leverage",
    "decision_quality", "propagation_durability", "collaborative_amplification",
]

_claims: dict[str, dict] = {}


def claim(text, claim_type, subject, evidence, derivation, confidence=None):
    """Mint a claim the same way the real registry does: content-addressed."""
    payload = json.dumps(
        {"t": text, "s": subject, "e": sorted(e["artifact_id"] for e in evidence)},
        sort_keys=True, separators=(",", ":"),
    )
    cid = f"claim/{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
    _claims[cid] = {
        "claim_id": cid, "text": text, "claim_type": claim_type,
        "subject": subject, "evidence": evidence, "evidence_count": len(evidence),
        "evidence_is_methodological": not evidence,
        "derivation": derivation, "confidence": confidence,
        "claims_version": "1.0.0",
    }
    return cid


def pr_ev(number, detail="state=MERGED"):
    return {"artifact_id": f"{QUALIFIER}#pr/{number}",
            "url": f"{REPO}/pull/{number}", "kind": "pull_request",
            "detail": detail}


def issue_ev(number, detail="state=CLOSED reason=COMPLETED"):
    return {"artifact_id": f"{QUALIFIER}#issue/{number}",
            "url": f"{REPO}/issues/{number}", "kind": "issue", "detail": detail}


def comment_ev(cid, detail):
    return {"artifact_id": f"{QUALIFIER}#review_comment/{cid}",
            "url": f"{REPO}/pull/40101#discussion_r{cid}",
            "kind": "review_comment", "detail": detail}


# --------------------------------------------------------------------------
# episodes
# --------------------------------------------------------------------------

def build_episodes():
    e1 = f"{QUALIFIER}#episode/40101-aaaa11112222"
    e2 = f"{QUALIFIER}#episode/40210-bbbb33334444"
    e3 = f"{QUALIFIER}#episode/40355-cccc55556666"
    e4 = f"{QUALIFIER}#episode/40402-dddd77778888"
    e5 = f"{QUALIFIER}#episode/40510-eeee9999aaaa"

    episodes = []

    # 1 — the good case: corroborated release, broad bands, real evidence.
    episodes.append({
        "episode_id": e1,
        "title_claim_id": claim(
            "Session replay export pipeline",
            "episode_narrative", e1, [issue_ev(39001), pr_ev(40101)],
            "episodes.build:title from linked issue", "high"),
        "problem_claim_id": claim(
            "Exports of long session recordings timed out before completing, so "
            "customers could not retrieve replays over roughly twenty minutes.",
            "episode_narrative", e1, [issue_ev(39001)],
            "episodes.build:_problem_statement", "high"),
        "intervention_claim_id": claim(
            "Across 3 pull requests the work added 14 files, modified 22 and "
            "removed 3 in product:replay, platform:ingestion.",
            "episode_narrative", e1, [pr_ev(40101), pr_ev(40118), pr_ev(40140)],
            "episodes.build:_intervention from the merge-commit diff", "high"),
        "outcome_claim_id": claim(
            "The change landed on the default branch. Release corroborated by: "
            "2 documentation files changed; feature flag 'replay-export-v2' "
            "removed from the registry, which is how a rollout ends.",
            "episode_narrative", e1, [pr_ev(40140), issue_ev(39001)],
            "episodes.status:classify", "corroborated"),
        "title": "Session replay export pipeline",
        "started_at": "2026-06-02T09:14:00Z", "ended_at": "2026-06-19T16:02:00Z",
        "duration_days": 17.28,
        "status": "shipped_observable",
        "status_reasons": ["3 PR(s) merged to the default branch"],
        "release_corroboration": "corroborated",
        "release_evidence": [
            {"kind": "docs_or_changelog_touched",
             "detail": "2 documentation file(s) changed, e.g. contents/docs/replay/export.mdx"},
            {"kind": "feature_flag_removed",
             "detail": "feature flag 'replay-export-v2' removed from the registry"},
        ],
        "components": ["product:replay", "platform:ingestion"],
        "products": ["product:replay"],
        "reachability_band": "cross_product",
        "feature_flag_keys": ["replay-export-v2"],
        "pr_numbers": [40101, 40118, 40140],
        "issue_numbers": [39001],
        "cluster_confidence": 0.91,
        "cluster_confidence_reasons": ["78% of internal edges are deterministic (tier A)"],
        "sub_episode_links": [
            {"child_pr": 40118, "parent_pr": 40101, "relation": "part_of",
             "evidence": "body says 'part of #40101'"}],
        "counterevidence": [],
        "has_ai_co_author": True,
        "touches_enterprise_licensed_code": False,
        "dimensions": _dims(e1, {
            "product_outcome": (3, "high", ["linked_issue", "feature_flag", "docs_or_changelog"],
                                "Production changes span 2 components with 3 classes of "
                                "corroborating evidence."),
            "reliability_risk": (2, "high", ["risk_surface", "test_coverage"],
                                 "Consequential risk reduction: touches ['ingestion'] "
                                 "across 2 components."),
            "engineering_leverage": (3, "medium", ["propagation_edge", "introduced_module"],
                                     "Adopted beyond its own area: 3 components, 4 distinct "
                                     "downstream authors."),
            "decision_quality": (2, "medium", ["review_thread", "pr_body_rationale"],
                                 "Clear simplification, descope or accepted design "
                                 "correction with observable before/after evidence."),
            "propagation_durability": (3, "high", ["survival_measurement", "propagation_edge",
                                                   "persistence_evidence"],
                                       "Demonstrable downstream reuse across 3 component "
                                       "boundaries."),
            "collaborative_amplification": (2, "medium", ["causal_review_intervention"],
                                            "Material unblock: a review intervention changed "
                                            "the code."),
        }),
        "participants": [
            _participant(e1, "github/user/fixture-ada", "fixture-ada",
                         ["core_implementer", "enabler"], "primary", "high",
                         ["sole core implementer of the episode"],
                         [{"role": "core_implementer",
                           "detail": "authored PR #40101 (72% of the episode's "
                                     "production-code files)",
                           "artifact_id": f"{QUALIFIER}#pr/40101",
                           "url": f"{REPO}/pull/40101"}]),
            _participant(e1, "github/user/fixture-grace", "fixture-grace",
                         ["risk_preventer", "decision_shaper"], "material", "high",
                         ["review intervention with high causal confidence, but no "
                          "implementation contribution"],
                         [{"role": "risk_preventer",
                           "detail": "review comment on PR #40118 raised a "
                                     "data_integrity concern; consequence prevented_risk",
                           "artifact_id": f"{QUALIFIER}#review_comment/r5501",
                           "url": f"{REPO}/pull/40118#discussion_r5501"}]),
        ],
        "artifact_ids": [f"{QUALIFIER}#pr/40101", f"{QUALIFIER}#pr/40118",
                         f"{QUALIFIER}#pr/40140", f"{QUALIFIER}#issue/39001",
                         f"{QUALIFIER}#review_comment/r5501",
                         f"{QUALIFIER}#feature_flag/replay-export-v2",
                         f"{QUALIFIER}#file/contents/docs/replay/export.mdx"],
        "analytics": {
            "propagation": {
                "reach_file_count": 41, "reach_pr_count": 12,
                "distinct_component_penetration": 3,
                "components_reached": ["product:replay", "platform:ingestion", "frontend:lib"],
                "distinct_downstream_authors": 4, "max_path_depth": 2,
                "mass_after_cap": 6.42, "cap_applied": False,
                "source_age_days": 61.2, "raw_decay_factor": 0.792,
                "persistence_detected": True, "effective_decay_factor": 0.792,
                "reason": None},
            "novelty": {"novelty_class": "new_capability",
                        "rationale": "introduces ['new_directory']",
                        "markers": ["new_directory"],
                        "uncertainty": ["The clone is shallow: a file first seen inside "
                                        "the window may predate it."]},
            "corrective_burden": {"by_class": {"healthy_iteration": 1},
                                  "capped_penalty": 0.0, "confirmed_revert": False,
                                  "unconfirmed_event_count": 0},
        },
    })

    # 2 — merged but NOT corroborated, plus unconfirmed counterevidence.
    episodes.append({
        "episode_id": e2,
        "title_claim_id": claim("Query cache invalidation on property changes",
                                "episode_narrative", e2, [pr_ev(40210)],
                                "episodes.build:title from anchor PR", "medium"),
        "problem_claim_id": claim(
            "No problem statement is recorded in the linked issue or PR bodies.",
            "episode_narrative", e2, [pr_ev(40210)],
            "episodes.build:_problem_statement", "low"),
        "intervention_claim_id": claim(
            "Across 1 pull request the work modified 6 files in platform:query.",
            "episode_narrative", e2, [pr_ev(40210)],
            "episodes.build:_intervention from the merge-commit diff", "medium"),
        "outcome_claim_id": claim(
            "The change landed on the default branch. Release is not independently "
            "corroborated — merging to the default branch is not proof that users "
            "saw the change.",
            "episode_narrative", e2, [pr_ev(40210)],
            "episodes.status:classify", "merged_only"),
        "title": "Query cache invalidation on property changes",
        "started_at": "2026-07-04T11:00:00Z", "ended_at": "2026-07-05T08:30:00Z",
        "duration_days": 0.9,
        "status": "shipped_observable",
        "status_reasons": ["1 PR(s) merged to the default branch; no independent "
                           "release evidence — merging is not proof of user release"],
        "release_corroboration": "merged_only",
        "release_evidence": [],
        "components": ["platform:query"], "products": [],
        "reachability_band": "component",
        "feature_flag_keys": [], "pr_numbers": [40210], "issue_numbers": [],
        "cluster_confidence": 1.0,
        "cluster_confidence_reasons": ["single-PR episode: no clustering decision was made"],
        "sub_episode_links": [],
        "counterevidence": [
            {"kind": "corrective_follow_up", "evidence_tier": "proximate",
             "requires_human_confirmation": True, "pr_number": 40210,
             "detail": "PR #40210 has proximate evidence of later corrective work "
                       "(proximate-only: not confirmed to be a regression)"},
            {"kind": "release_unverified", "evidence_tier": "structural",
             "requires_human_confirmation": False,
             "detail": "No documentation, changelog, flag removal, closed issue or "
                       "downstream adoption corroborates that this reached users."},
            {"kind": "production_change_without_tests", "evidence_tier": "structural",
             "requires_human_confirmation": False, "pr_number": 40210,
             "detail": "production code changed with no accompanying test change"},
        ],
        "has_ai_co_author": False, "touches_enterprise_licensed_code": False,
        "dimensions": _dims(e2, {
            "product_outcome": (1, "medium", [],
                                "Production code changed in 1 component with no linked "
                                "issue, feature flag or documentation to corroborate a "
                                "user-visible outcome."),
            "reliability_risk": (1, "medium", ["risk_surface"],
                                 "Localized hardening within a single component."),
            "engineering_leverage": (0, "medium", [],
                                     "No later change depends on what this episode "
                                     "introduced, within the observed window."),
            "decision_quality": (0, "medium", [],
                                 "No observable before/after decision evidence."),
            "propagation_durability": (None, "low", [],
                                       "Durability could not be measured."),
            "collaborative_amplification": (0, "low", [],
                                            "No collaborative evidence."),
        }, unknown_reason={
            "propagation_durability":
                "insufficient follow-up history: +30d falls after the window end"}),
        "participants": [
            _participant(e2, "github/user/fixture-linus", "fixture-linus",
                         ["core_implementer"], "primary", "medium",
                         ["sole core implementer of the episode"],
                         [{"role": "core_implementer",
                           "detail": "authored PR #40210 (100% of the episode's "
                                     "production-code files)",
                           "artifact_id": f"{QUALIFIER}#pr/40210",
                           "url": f"{REPO}/pull/40210"}]),
        ],
        "artifact_ids": [f"{QUALIFIER}#pr/40210"],
        "analytics": {
            "propagation": {"reach_file_count": 0, "reach_pr_count": 0,
                            "distinct_component_penetration": 0, "components_reached": [],
                            "distinct_downstream_authors": 0, "max_path_depth": 0,
                            "mass_after_cap": 0.0, "cap_applied": False,
                            "source_age_days": None, "raw_decay_factor": None,
                            "persistence_detected": False, "effective_decay_factor": None,
                            "reason": "no later change imports anything this episode touched"},
            "novelty": {"novelty_class": "extension",
                        "rationale": "modifies 6 existing production file(s)",
                        "markers": [], "uncertainty": []},
            "corrective_burden": {"by_class": {"unrelated_same_area": 2},
                                  "capped_penalty": 0.0, "confirmed_revert": False,
                                  "unconfirmed_event_count": 2},
        },
    })

    # 3 — reverted.
    episodes.append({
        "episode_id": e3,
        "title_claim_id": claim("Batch export retry backoff", "episode_narrative", e3,
                                [pr_ev(40355)], "episodes.build:title", "high"),
        "problem_claim_id": claim(
            "Currently failed batch exports retry immediately, which amplifies load "
            "on the destination during an outage.",
            "episode_narrative", e3, [pr_ev(40355)],
            "episodes.build:_problem_statement", "medium"),
        "intervention_claim_id": claim(
            "Across 2 pull requests the work modified 4 files in platform:batch-exports.",
            "episode_narrative", e3, [pr_ev(40355), pr_ev(40361)],
            "episodes.build:_intervention", "high"),
        "outcome_claim_id": claim(
            "The change was reverted. Release is not independently corroborated.",
            "episode_narrative", e3, [pr_ev(40361)],
            "episodes.status:classify", "merged_only"),
        "title": "Batch export retry backoff",
        "started_at": "2026-07-20T10:00:00Z", "ended_at": "2026-07-24T15:40:00Z",
        "duration_days": 4.24, "status": "reverted",
        "status_reasons": ["PR #40355 has explicit revert evidence: PR #40361 is "
                           "revert(...) with the same subject"],
        "release_corroboration": "merged_only", "release_evidence": [],
        "components": ["platform:batch-exports"], "products": [],
        "reachability_band": "component", "feature_flag_keys": [],
        "pr_numbers": [40355, 40361], "issue_numbers": [],
        "cluster_confidence": 0.88,
        "cluster_confidence_reasons": ["100% of internal edges are deterministic (tier A)"],
        "sub_episode_links": [],
        "counterevidence": [
            {"kind": "reverted", "evidence_tier": "explicit",
             "requires_human_confirmation": False, "pr_number": 40355,
             "detail": "PR #40355 was explicitly reverted"},
        ],
        "has_ai_co_author": False, "touches_enterprise_licensed_code": False,
        "dimensions": _dims(e3, {
            "product_outcome": (1, "medium", [], "Production code changed in 1 component."),
            "reliability_risk": (2, "medium", ["risk_surface", "linked_issue"],
                                 "Consequential risk reduction."),
            "engineering_leverage": (0, "medium", [], "No later change depends on this."),
            "decision_quality": (0, "medium", [], "No observable before/after evidence."),
            "propagation_durability": (1, "high", ["survival_measurement"],
                                       "Introduced files survive locally to the last "
                                       "observable checkpoint."),
            "collaborative_amplification": (0, "low", [], "No collaborative evidence."),
        }),
        "participants": [
            _participant(e3, "github/user/fixture-linus", "fixture-linus",
                         ["core_implementer", "rollout_sustainer"], "primary", "high",
                         ["sole core implementer of the episode"],
                         [{"role": "core_implementer",
                           "detail": "authored PR #40355",
                           "artifact_id": f"{QUALIFIER}#pr/40355",
                           "url": f"{REPO}/pull/40355"}]),
        ],
        "artifact_ids": [f"{QUALIFIER}#pr/40355", f"{QUALIFIER}#pr/40361"],
        "analytics": {
            "propagation": {"reach_file_count": 0, "reach_pr_count": 0,
                            "distinct_component_penetration": 0, "components_reached": [],
                            "distinct_downstream_authors": 0, "max_path_depth": 0,
                            "mass_after_cap": 0.0, "cap_applied": False,
                            "source_age_days": 24.0, "raw_decay_factor": 0.912,
                            "persistence_detected": False, "effective_decay_factor": 0.912,
                            "reason": None},
            "novelty": {"novelty_class": "extension", "rationale": "modifies 4 files",
                        "markers": [], "uncertainty": []},
            "corrective_burden": {"by_class": {"confirmed_revert": 1},
                                  "capped_penalty": 1.0, "confirmed_revert": True,
                                  "unconfirmed_event_count": 0},
        },
    })

    # 4 — behind a flag.
    episodes.append({
        "episode_id": e4,
        "title_claim_id": claim("Experiment holdout groups", "episode_narrative", e4,
                                [issue_ev(39220), pr_ev(40402)],
                                "episodes.build:title from linked issue", "high"),
        "problem_claim_id": claim(
            "Teams cannot reserve a holdout population across several experiments, "
            "so results contaminate each other.",
            "episode_narrative", e4, [issue_ev(39220)],
            "episodes.build:_problem_statement", "high"),
        "intervention_claim_id": claim(
            "Across 2 pull requests the work added 9 files and modified 11 in "
            "product:experiments.",
            "episode_narrative", e4, [pr_ev(40402), pr_ev(40430)],
            "episodes.build:_intervention", "high"),
        "outcome_claim_id": claim(
            "The change landed but remains behind a feature flag. Release corroborated "
            "by: issue #39220 closed as completed.",
            "episode_narrative", e4, [pr_ev(40430), issue_ev(39220)],
            "episodes.status:classify", "corroborated"),
        "title": "Experiment holdout groups",
        "started_at": "2026-07-28T08:00:00Z", "ended_at": "2026-08-08T12:00:00Z",
        "duration_days": 11.17, "status": "partial_or_behind_flag",
        "status_reasons": ["feature flag 'experiment-holdouts' was introduced and was "
                           "not removed inside the window; the arc is still gated"],
        "release_corroboration": "corroborated",
        "release_evidence": [{"kind": "linked_issue_closed_as_completed",
                              "detail": "issue #39220 closed as completed"}],
        "components": ["product:experiments"], "products": ["product:experiments"],
        "reachability_band": "component", "feature_flag_keys": ["experiment-holdouts"],
        "pr_numbers": [40402, 40430], "issue_numbers": [39220],
        "cluster_confidence": 0.86,
        "cluster_confidence_reasons": ["67% of internal edges are deterministic (tier A)"],
        "sub_episode_links": [], "counterevidence": [],
        "has_ai_co_author": True, "touches_enterprise_licensed_code": False,
        "dimensions": _dims(e4, {
            "product_outcome": (2, "high", ["linked_issue", "feature_flag", "test_coverage"],
                                "A linked issue is resolved and the change ships behind a "
                                "named flag, within one product surface."),
            "reliability_risk": (0, "high", [], "No reliability-relevant evidence."),
            "engineering_leverage": (1, "medium", ["propagation_edge"],
                                     "Adopted only within its immediate area."),
            "decision_quality": (1, "medium", ["pr_body_rationale"],
                                 "A documented implementation choice with stated rationale."),
            "propagation_durability": (2, "medium", ["survival_measurement", "follow_up_pr"],
                                       "Introduced files survive and the work received "
                                       "normal follow-up."),
            "collaborative_amplification": (1, "low", ["causal_review_intervention"],
                                            "Helpful local collaboration."),
        }),
        "participants": [
            _participant(e4, "github/user/fixture-grace", "fixture-grace",
                         ["core_implementer"], "primary", "high",
                         ["sole core implementer of the episode"],
                         [{"role": "core_implementer", "detail": "authored PR #40402",
                           "artifact_id": f"{QUALIFIER}#pr/40402",
                           "url": f"{REPO}/pull/40402"}]),
            _participant(e4, "github/user/fixture-ada", "fixture-ada",
                         ["originator"], "material", "medium",
                         ["framed the problem but did not implement it"],
                         [{"role": "originator",
                           "detail": "opened issue #39220: Holdout groups for experiments",
                           "artifact_id": f"{QUALIFIER}#issue/39220",
                           "url": f"{REPO}/issues/39220"}]),
        ],
        "artifact_ids": [f"{QUALIFIER}#pr/40402", f"{QUALIFIER}#pr/40430",
                         f"{QUALIFIER}#issue/39220",
                         f"{QUALIFIER}#feature_flag/experiment-holdouts"],
        "analytics": {
            "propagation": {"reach_file_count": 3, "reach_pr_count": 2,
                            "distinct_component_penetration": 1,
                            "components_reached": ["product:experiments"],
                            "distinct_downstream_authors": 1, "max_path_depth": 1,
                            "mass_after_cap": 1.1, "cap_applied": False,
                            "source_age_days": 9.0, "raw_decay_factor": 0.966,
                            "persistence_detected": True, "effective_decay_factor": 0.966,
                            "reason": None},
            "novelty": {"novelty_class": "new_capability",
                        "rationale": "introduces ['new_directory']",
                        "markers": ["new_directory"], "uncertainty": []},
            "corrective_burden": {"by_class": {}, "capped_penalty": 0.0,
                                  "confirmed_revert": False, "unconfirmed_event_count": 0},
        },
    })

    # 5 — maintenance, attributed to the below-the-bar engineer.
    episodes.append({
        "episode_id": e5,
        "title_claim_id": claim("Bump frontend dependencies", "episode_narrative", e5,
                                [pr_ev(40510)], "episodes.build:title", "medium"),
        "problem_claim_id": claim(
            "No problem statement is recorded in the linked issue or PR bodies.",
            "episode_narrative", e5, [pr_ev(40510)],
            "episodes.build:_problem_statement", "low"),
        "intervention_claim_id": claim(
            "Across 1 pull request the work modified 2 files in frontend:deps.",
            "episode_narrative", e5, [pr_ev(40510)],
            "episodes.build:_intervention", "medium"),
        "outcome_claim_id": claim(
            "The change is routine maintenance. Release is not independently corroborated.",
            "episode_narrative", e5, [pr_ev(40510)],
            "episodes.status:classify", "merged_only"),
        "title": "Bump frontend dependencies",
        "started_at": "2026-08-10T05:00:00Z", "ended_at": "2026-08-10T06:12:00Z",
        "duration_days": 0.05, "status": "maintenance",
        "status_reasons": ["100% of changed files are lockfiles, generated code, "
                           "snapshots or vendored code (>= 80%)"],
        "release_corroboration": "merged_only", "release_evidence": [],
        "components": ["frontend:deps"], "products": [],
        "reachability_band": "unknown", "feature_flag_keys": [],
        "pr_numbers": [40510], "issue_numbers": [],
        "cluster_confidence": 1.0,
        "cluster_confidence_reasons": ["single-PR episode: no clustering decision was made"],
        "sub_episode_links": [], "counterevidence": [],
        "has_ai_co_author": False, "touches_enterprise_licensed_code": False,
        "dimensions": _dims(e5, {
            "product_outcome": (0, "high", [],
                                "No product or platform code changed — the episode "
                                "touches only tests, documentation, configuration or "
                                "dependencies."),
            "reliability_risk": (0, "high", [], "No reliability-relevant evidence."),
            "engineering_leverage": (None, "low", [],
                                     "Leverage cannot be assessed: nothing this episode "
                                     "touched is resolvable in the import graph."),
            "decision_quality": (0, "medium", [], "No observable before/after evidence."),
            "propagation_durability": (None, "low", [],
                                       "Durability could not be measured."),
            "collaborative_amplification": (0, "low", [], "No collaborative evidence."),
        }, unknown_reason={
            "engineering_leverage":
                "episode introduced or changed no graph-resolvable production file",
            "propagation_durability":
                "the episode introduced no files and nothing downstream depends on it"}),
        "participants": [
            _participant(e5, "github/user/fixture-mel", "fixture-mel",
                         ["core_implementer"], "primary", "medium",
                         ["sole core implementer of the episode"],
                         [{"role": "core_implementer", "detail": "authored PR #40510",
                           "artifact_id": f"{QUALIFIER}#pr/40510",
                           "url": f"{REPO}/pull/40510"}]),
        ],
        "artifact_ids": [f"{QUALIFIER}#pr/40510"],
        "analytics": {
            "propagation": {"reach_file_count": 0, "reach_pr_count": 0,
                            "distinct_component_penetration": 0, "components_reached": [],
                            "distinct_downstream_authors": 0, "max_path_depth": 0,
                            "mass_after_cap": 0.0, "cap_applied": False,
                            "source_age_days": None, "raw_decay_factor": None,
                            "persistence_detected": False, "effective_decay_factor": None,
                            "reason": "episode introduced or changed no graph-resolvable "
                                      "production file"},
            "novelty": {"novelty_class": "maintenance_repeat",
                        "rationale": "no production-code change observed",
                        "markers": [], "uncertainty": []},
            "corrective_burden": {"by_class": {}, "capped_penalty": 0.0,
                                  "confirmed_revert": False, "unconfirmed_event_count": 0},
        },
    })
    return episodes


def _dims(episode_id, spec, unknown_reason=None):
    unknown_reason = unknown_reason or {}
    out = []
    for dimension in DIMENSIONS:
        band, confidence, classes, rationale = spec[dimension]
        labels = ["no_evidence", "local", "material", "broad", "transformative"]
        out.append({
            "dimension": dimension,
            "band": band,
            "band_label": "unknown" if band is None else labels[band],
            "is_unknown": band is None,
            "unknown_reason": unknown_reason.get(dimension),
            "confidence": confidence,
            "confidence_reasons": (
                ["all supporting evidence is directly observable"] if confidence == "high"
                else ["blast radius is unknown for at least one PR (unparsed language "
                      "or no graph coverage)"]),
            "corroboration_status": (
                "corroborated" if len(classes) >= 2
                else "single_source" if classes else "uncorroborated"),
            "artifact_classes": classes,
            "evidence": [{"kind": c, "detail": f"{c} evidence present"} for c in classes],
            "counterevidence": [],
            "rationale_claim_id": claim(
                rationale, "dimension_band", f"{episode_id}/dimension/{dimension}",
                [pr_ev(int(episode_id.split("/")[-1].split("-")[0]))],
                f"dimensions.rubric:{dimension} (rubric 1.0.0)", confidence),
        })
    return out


def _participant(episode_id, cluster, login, roles, share, confidence, reasons, evidence):
    claim_ids = [
        claim(f"{login} acted as {role.replace('_', ' ')}: {evidence[0]['detail']}",
              "attribution", f"participant/{cluster}:{episode_id}",
              [{"artifact_id": evidence[0]["artifact_id"], "url": evidence[0]["url"],
                "kind": "role_evidence", "detail": evidence[0]["detail"]}],
              "episodes.participants:infer_roles", confidence)
        for role in roles[:1]
    ]
    claim_ids.append(
        claim(f"Shared credit is recorded as '{share}' because {'; '.join(reasons)}.",
              "attribution", f"participant/{cluster}:{episode_id}",
              [{"artifact_id": evidence[0]["artifact_id"], "url": evidence[0]["url"],
                "kind": "role_evidence", "detail": evidence[0]["detail"]}],
              "episodes.participants:_share_category", confidence))
    return {
        "actor_cluster_id": cluster, "login": login, "roles": roles,
        "share_category": share, "share_reasons": reasons,
        "attribution_confidence": confidence, "direct_evidence": evidence,
        "claim_ids": claim_ids,
    }


# --------------------------------------------------------------------------
# engineers, rankings, comparisons
# --------------------------------------------------------------------------

ENGINEERS_SPEC = [
    ("github/user/fixture-ada", "fixture-ada", "Ada (fixture)", True,
     {"product_outcome": 3.15, "reliability_risk": 2.0, "engineering_leverage": 3.3,
      "decision_quality": 2.0, "propagation_durability": 3.0,
      "collaborative_amplification": 1.4},
     {"product_outcome": "high", "reliability_risk": "high",
      "engineering_leverage": "medium", "decision_quality": "medium",
      "propagation_durability": "high", "collaborative_amplification": "low"},
     ["core_implementer", "enabler", "originator"], "few_episodes",
     0.86, 0.94, [1, 3]),
    ("github/user/fixture-grace", "fixture-grace", "Grace (fixture)", True,
     {"product_outcome": 2.4, "reliability_risk": 3.1, "engineering_leverage": 1.2,
      "decision_quality": 2.6, "propagation_durability": 2.0,
      "collaborative_amplification": 2.8},
     {"product_outcome": "high", "reliability_risk": "high",
      "engineering_leverage": "medium", "decision_quality": "high",
      "propagation_durability": "medium", "collaborative_amplification": "high"},
     ["core_implementer", "risk_preventer", "decision_shaper"], "broad",
     0.81, 0.91, [1, 4]),
    ("github/user/fixture-linus", "fixture-linus", "Linus (fixture)", True,
     {"product_outcome": 1.1, "reliability_risk": 1.9, "engineering_leverage": 0.0,
      "decision_quality": 0.0, "propagation_durability": 1.0,
      "collaborative_amplification": None},
     {"product_outcome": "medium", "reliability_risk": "medium",
      "engineering_leverage": "medium", "decision_quality": "medium",
      "propagation_durability": "high", "collaborative_amplification": "unknown"},
     ["core_implementer", "rollout_sustainer"], "single_episode_dominant",
     0.74, 0.12, [3, 3]),
    ("github/user/fixture-mel", "fixture-mel", "Mel (fixture)", False,
     {"product_outcome": 0.0, "reliability_risk": 0.0, "engineering_leverage": None,
      "decision_quality": 0.0, "propagation_durability": None,
      "collaborative_amplification": 0.0},
     {"product_outcome": "high", "reliability_risk": "high",
      "engineering_leverage": "unknown", "decision_quality": "medium",
      "propagation_durability": "unknown", "collaborative_amplification": "low"},
     ["core_implementer"], "single_episode_dominant", None, None, None),
]

UNKNOWN_REASONS = {
    "engineering_leverage":
        "episode introduced or changed no graph-resolvable production file",
    "propagation_durability":
        "the episode introduced no files and nothing downstream depends on it",
    "collaborative_amplification":
        "Phase 1 review detail is unavailable, so no review intervention could "
        "be assessed for this engineer",
}

EPISODES_BY_ENGINEER = {
    "github/user/fixture-ada": [f"{QUALIFIER}#episode/40101-aaaa11112222",
                                f"{QUALIFIER}#episode/40402-dddd77778888"],
    "github/user/fixture-grace": [f"{QUALIFIER}#episode/40402-dddd77778888",
                                  f"{QUALIFIER}#episode/40101-aaaa11112222"],
    "github/user/fixture-linus": [f"{QUALIFIER}#episode/40210-bbbb33334444",
                                  f"{QUALIFIER}#episode/40355-cccc55556666"],
    "github/user/fixture-mel": [f"{QUALIFIER}#episode/40510-eeee9999aaaa"],
}


def build_engineers():
    rows = []
    for (cluster, login, name, rankable, values, confidences, roles, concentration,
         stability, top5, span) in ENGINEERS_SPEC:
        episodes = EPISODES_BY_ENGINEER[cluster]
        thesis = []
        for dimension, value in values.items():
            if value is None:
                thesis.append(claim(
                    f"{login} has no assessable {dimension.replace('_', ' ')} evidence: "
                    f"{UNKNOWN_REASONS[dimension]}. This is a gap in the data, not a "
                    "low score.",
                    "portfolio", f"portfolio/{cluster}", [],
                    "portfolio.build:dimension_value", "unknown"))
            else:
                thesis.append(claim(
                    f"{login}'s {dimension.replace('_', ' ')} evidence is carried by "
                    f"'{episodes[0].split('/')[-1]}' (band "
                    f"{min(4, int(round(value)))}, primary credit).",
                    "portfolio", f"portfolio/{cluster}",
                    [{"artifact_id": episodes[0], "kind": "episode",
                      "detail": "strongest attributed episode for this dimension"}],
                    "portfolio.build:aggregate_ordered (coefficients, headroom cap)",
                    confidences[dimension]))
        eligibility_reasons = (
            ["meets the minimum observable-evidence bar"] if rankable
            else ["only 1 dimension(s) have an assessable band, minimum is 2",
                  "mean confidence discount 0.42 below 0.45"])
        if not rankable:
            thesis.append(claim(
                f"{login} is labelled 'insufficient_observable_evidence': "
                + "; ".join(eligibility_reasons)
                + ". This describes the available evidence, not the engineer.",
                "limitation", f"portfolio/{cluster}", [],
                "portfolio.build:_eligibility", "high"))

        stability_claim = None
        if top5 is not None:
            stability_claim = claim(
                f"{login} appears in the top five in {top5:.0%} of resampled and "
                f"reweighted configurations (position range {span}).",
                "stability", cluster,
                [{"artifact_id": "analysis/bootstrap", "kind": "stability_analysis",
                  "detail": f"rank stability {stability}"}],
                "rank.sensitivity:bootstrap + weight + structural variation")

        rows.append({
            "actor_cluster_id": cluster, "login": login, "display_name": name,
            "profile_url": f"https://github.com/{login}",
            "avatar_url": f"https://github.com/{login}.png",
            "affiliation": "unknown",
            "affiliation_note": "Affiliation is not asserted: public GitHub data does "
                                "not reliably distinguish employees from community "
                                "contributors.",
            "identity_ambiguity": "resolved", "identity_ambiguity_reasons": [],
            "portfolio_id": f"portfolio/{cluster}",
            "thesis_claim_ids": thesis,
            "dimension_profile": [
                {
                    "dimension": dimension,
                    "value": value,
                    "interval": (None if value is None
                                 else [round(max(0.0, value - 0.45), 2),
                                       round(min(4.0, value + 0.45), 2)]),
                    "confidence": confidences[dimension],
                    "is_unknown": value is None,
                    "unknown_reason": UNKNOWN_REASONS.get(dimension) if value is None else None,
                    "top_episode_id": episodes[0] if value else None,
                    "episode_count": len(episodes) if value else 0,
                    "aggregation_trace": ([] if value is None else [
                        {"rank": 1, "value": round(value * 0.85, 3), "coefficient": 1.0,
                         "contribution": round(value * 0.85, 3), "headroom_capped": False},
                        {"rank": 2, "value": round(value * 0.4, 3), "coefficient": 0.55,
                         "contribution": round(value * 0.22, 3), "headroom_capped": False},
                    ]),
                }
                for dimension, value in values.items()
            ],
            "strongest_dimension": (
                max((k for k, v in values.items() if v is not None),
                    key=lambda k: values[k]) if any(v for v in values.values()) else None),
            "strongest_evidence_episode_id": episodes[0],
            "episode_ids": episodes, "episode_count": len(episodes),
            "current_episode_ids": episodes[:1],
            "foundational_episode_ids": episodes[1:],
            "roles_held": roles, "concentration_profile": concentration,
            "diversity_affects_ranking": False,
            "active_period": {"first_observed": "2026-06-02T09:14:00Z",
                              "last_observed": "2026-08-10T06:12:00Z",
                              "span_days": 68.87,
                              "note": "Descriptive only. This is never used as a "
                                      "denominator: per-day normalisation would "
                                      "penalise anyone who took leave."},
            "rankable": rankable,
            "eligibility_label": None if rankable else "insufficient_observable_evidence",
            "eligibility_reasons": eligibility_reasons,
            "uncertainty": {"rank_stability_index": stability,
                            "top5_inclusion_probability": top5,
                            "position_range": span, "claim_id": stability_claim},
        })
    return rows


BALANCED_WEIGHTS = {"product_outcome": 0.22, "reliability_risk": 0.18,
                    "engineering_leverage": 0.20, "decision_quality": 0.14,
                    "propagation_durability": 0.16,
                    "collaborative_amplification": 0.10}
THRESHOLDS = {d: {"q": 0.0, "p": 1.0, "v": 3.0} for d in DIMENSIONS}


def build_rankings(engineers):
    by_cluster = {e["actor_cluster_id"]: e for e in engineers}
    rankable = [e for e in engineers if e["rankable"]]
    # Ada and Grace deliberately share tier 1 and are mutually incomparable.
    order = [("github/user/fixture-ada", 1, 1, ["github/user/fixture-grace"], 2, 1),
             ("github/user/fixture-grace", 2, 1, ["github/user/fixture-ada"], 1, -1),
             ("github/user/fixture-linus", 3, 2, [], 3, 0)]
    positions = []
    for cluster, position, tier, incomparable, cross, delta in order:
        engineer = by_cluster[cluster]
        positions.append({
            "position": position, "tier": tier, "actor_cluster_id": cluster,
            "login": engineer["login"],
            "dimension_values": {d["dimension"]: d["value"]
                                 for d in engineer["dimension_profile"]},
            "incomparable_with": incomparable,
            "incomparable_count": len(incomparable),
            "cross_check_position": cross, "cross_check_delta": delta,
            "stability": {
                "rank_stability_index": engineer["uncertainty"]["rank_stability_index"],
                "top5_inclusion_probability":
                    engineer["uncertainty"]["top5_inclusion_probability"],
                "position_range": engineer["uncertainty"]["position_range"]},
        })

    return {
        "default_scenario": "balanced",
        "scenarios": [
            {"scenario": "balanced", "label": "Balanced",
             "description": "The normative starting preference across all six dimensions.",
             "available": True, "unavailable_reason": None, "remedy": None, "note": None,
             "weights": BALANCED_WEIGHTS, "thresholds": THRESHOLDS,
             "alternatives": len(rankable), "excluded_insufficient_evidence": 1,
             "positions": positions,
             "cross_check": {"method": "promethee_ii", "top5_agreement": 1.0,
                             "note": "PROMETHEE II uses different aggregation logic on "
                                     "the same inputs. Disagreement means the result is "
                                     "sensitive to the aggregation choice and is "
                                     "reported, not hidden."}},
            {"scenario": "last_12_months", "label": "Last 12 months",
             "description": None, "available": False,
             "unavailable_reason": "needs a 365-day window; the extracted window is 90 days",
             "remedy": "Re-run Phase 1 with `python -m impact all --window-start "
                       "<365 days ago>` and widen the clone with `git -C "
                       "data/raw/git/posthog fetch --shallow-since=<date>`, then re-run "
                       "`make p2`.",
             "note": None, "weights": BALANCED_WEIGHTS, "thresholds": THRESHOLDS,
             "alternatives": 0, "excluded_insufficient_evidence": 0,
             "positions": [], "cross_check": {}},
        ],
        "method": {
            "name": "ELECTRE III", "cross_check": "PROMETHEE II",
            "why_not_a_score": "A single number would have to encode an exchange rate "
                               "between shipping a product surface and preventing a "
                               "data-loss bug. There is no honest exchange rate, so "
                               "engineers are compared pairwise on six criteria and the "
                               "credibility of each comparison is published.",
            "tiers_explained": "Engineers in the same tier are not distinguishable on "
                               "this evidence. Incomparability is a real result, not a "
                               "tie-break failure.",
        },
    }


def build_comparisons(engineers):
    by_cluster = {e["actor_cluster_id"]: e for e in engineers}
    top = ["github/user/fixture-ada", "github/user/fixture-grace",
           "github/user/fixture-linus"]
    pairwise = []
    for a in top:
        for b in top:
            if a == b:
                continue
            ea, eb = by_cluster[a], by_cluster[b]
            va = {d["dimension"]: d["value"] for d in ea["dimension_profile"]}
            vb = {d["dimension"]: d["value"] for d in eb["dimension_profile"]}
            per_criterion, excluded = [], []
            concordance_sum = weight_sum = 0.0
            for dimension in DIMENSIONS:
                if va[dimension] is None or vb[dimension] is None:
                    excluded.append({
                        "criterion": dimension,
                        "reason": "unknown for a" if va[dimension] is None else "unknown for b",
                        "a_unknown_reason": UNKNOWN_REASONS.get(dimension)
                        if va[dimension] is None else None,
                        "b_unknown_reason": UNKNOWN_REASONS.get(dimension)
                        if vb[dimension] is None else None})
                    continue
                ga, gb = va[dimension], vb[dimension]
                weight = BALANCED_WEIGHTS[dimension]
                c = 1.0 if ga >= gb else (0.0 if ga <= gb - 1.0 else round(1.0 - (gb - ga), 6))
                d = 0.0 if gb <= ga + 1.0 else min(1.0, round((gb - ga - 1.0) / 2.0, 6))
                concordance_sum += weight * c
                weight_sum += weight
                per_criterion.append({
                    "criterion": dimension, "a_value": round(ga, 4),
                    "b_value": round(gb, 4), "difference": round(ga - gb, 4),
                    "weight": weight, "concordance": c, "discordance": d,
                    "thresholds": THRESHOLDS[dimension]})
            concordance = round(concordance_sum / weight_sum, 6) if weight_sum else 0.0
            credibility = concordance
            favouring = sorted((c for c in per_criterion if c["difference"] > 0),
                               key=lambda c: -c["difference"] * c["weight"])[:2]
            against = sorted((c for c in per_criterion if c["difference"] < 0),
                             key=lambda c: c["difference"] * c["weight"])[:2]
            parts = []
            if favouring:
                parts.append("ahead on " + ", ".join(
                    f"{c['criterion'].replace('_', ' ')} "
                    f"({c['a_value']:.2f} vs {c['b_value']:.2f})" for c in favouring))
            if against:
                parts.append("behind on " + ", ".join(
                    f"{c['criterion'].replace('_', ' ')} "
                    f"({c['a_value']:.2f} vs {c['b_value']:.2f})" for c in against))
            text = (f"{ea['login']} is " + "; ".join(parts)
                    + f". Concordance {concordance:.2f}, credibility {credibility:.2f}.")
            if excluded:
                text += (f" {len(excluded)} criterion/criteria excluded as unknown for "
                         "one or both: unknown evidence is not scored as zero.")
            pairwise.append({
                "a": a, "b": b, "a_login": ea["login"], "b_login": eb["login"],
                "concordance": concordance, "credibility": credibility,
                "per_criterion": per_criterion, "excluded_criteria": excluded,
                "vetoing_criteria": [], "counterevidence_veto": False,
                "explanation_claim_id": claim(
                    text, "ranking", f"balanced:{a}vs{b}",
                    [{"artifact_id": f"criterion/{c['criterion']}", "kind": "criterion",
                      "detail": f"a={c['a_value']} b={c['b_value']} w={c['weight']}"}
                     for c in per_criterion],
                    "rank.outranking:ELECTRE III, scenario 'balanced'"),
            })
    return {"scenarios": {"balanced": {
        "top_five": top, "pairwise": pairwise,
        "methodology_trace": "Every pair above is published with its per-criterion "
                             "concordance, discordance, weights and thresholds. Excluded "
                             "criteria are unknown for one side and are not scored as zero.",
    }}}


LIMITATIONS = [
    "Public GitHub omits customer conversations, private incidents, mentoring, "
    "internal design documents, analytics work and private repositories.",
    "No evidence is not negative evidence. An engineer with little visible evidence "
    "here may have had enormous impact elsewhere.",
    "The window is 90 days. Foundational work that predates it is invisible; work in "
    "flight at either boundary is truncated.",
    "The repository clone is shallow. Survival and reachability look forward only.",
    "Rust, Go, SQL, Hog and Ruby imports are not parsed, so blast radius for changes "
    "confined to those languages is unknown, not small.",
    "Review-comment causality is inferred from ordering, thread resolution and "
    "GitHub's outdated flag — it is evidence, not proof.",
    "AI-assisted authorship is widespread in this repository and inflates raw volume, "
    "which is one reason volume is not used.",
    "Shared credit intervals are estimates. Where attribution is unclear it is "
    "labelled unclear rather than guessed.",
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "evidence").mkdir(exist_ok=True)

    episodes = build_episodes()
    engineers = build_engineers()
    rankings = build_rankings(engineers)
    comparisons = build_comparisons(engineers)
    limitation_claims = [
        claim(text, "limitation", "dashboard", [],
              "config/phase2/eligibility.yaml::limitations", "high")
        for text in LIMITATIONS
    ]

    evidence_shards = {
        "pull_request": [
            {"artifact_id": f"{QUALIFIER}#pr/{n}", "kind": "pull_request",
             "title": t, "url": f"{REPO}/pull/{n}",
             "provenance": "deterministic:phase1.pull_requests",
             "detail": "state=MERGED"}
            for n, t in [(40101, "feat(replay): stream session exports"),
                         (40118, "feat(replay): chunked export writer"),
                         (40140, "docs(replay): document export limits"),
                         (40210, "fix(query): invalidate cache on property change"),
                         (40355, "fix(batch-exports): add retry backoff"),
                         (40361, "revert(batch-exports): add retry backoff"),
                         (40402, "feat(experiments): holdout groups"),
                         (40430, "feat(experiments): holdout assignment UI"),
                         (40510, "chore(deps): bump frontend dependencies")]],
        "issue": [
            {"artifact_id": f"{QUALIFIER}#issue/{n}", "kind": "issue", "title": t,
             "url": f"{REPO}/issues/{n}",
             "provenance": "deterministic:github_closing_reference",
             "detail": "state=CLOSED reason=COMPLETED"}
            for n, t in [(39001, "Session replay exports time out over ~20 minutes"),
                         (39220, "Holdout groups for experiments")]],
        "review_comment": [
            {"artifact_id": f"{QUALIFIER}#review_comment/r5501",
             "kind": "review_comment",
             "title": "This will drop rows if the writer fails mid-chunk…",
             "url": f"{REPO}/pull/40118#discussion_r5501",
             "provenance": "deterministic:phase1.review_comments",
             "detail": "This will drop rows if the writer fails mid-chunk — we need the "
                       "chunk boundary to be transactional or an interrupted export "
                       "silently produces a truncated file.",
             "concern_classes": ["data_integrity", "correctness"],
             "consequence_band": "prevented_risk", "causal_confidence": "high",
             "change_evidence": ["GitHub marks this comment outdated: the code it was "
                                 "anchored to changed after the comment was written"]}],
        "feature_flag": [
            {"artifact_id": f"{QUALIFIER}#feature_flag/{k}", "kind": "feature_flag",
             "title": k, "url": None, "provenance": "deterministic:diff_scan",
             "detail": f"diff_side={side}"}
            for k, side in [("replay-export-v2", "removed"),
                            ("experiment-holdouts", "added")]],
        "file": [
            {"artifact_id": f"{QUALIFIER}#file/contents/docs/replay/export.mdx",
             "kind": "file", "title": "contents/docs/replay/export.mdx", "url": None,
             "provenance": "deterministic:git_merge_commit_diff",
             "detail": "status=M component=docs"}],
    }

    files = {}

    def write(name, payload, rows=None):
        path = OUT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8")
        files[name] = {"path": name, "bytes": path.stat().st_size,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "rows": rows}

    write("episodes.json", episodes, len(episodes))
    write("engineers.json", engineers, len(engineers))
    write("rankings.json", rankings)
    write("comparisons.json", comparisons)
    for kind, rows in evidence_shards.items():
        write(f"evidence/{kind}.json", rows, len(rows))
    write("evidence.json", {
        "sharded": True,
        "shards": {k: {"file": f"evidence/{k}.json", "count": len(v)}
                   for k, v in evidence_shards.items()},
        "total_artifacts": sum(len(v) for v in evidence_shards.values()),
        "note": "Sharded by artifact kind so an episode page does not download every "
                "review comment in the dataset."})
    write("claims.json", {
        "claims": sorted(_claims.values(), key=lambda c: c["claim_id"]),
        "count": len(_claims),
        "contract": "Every human-readable sentence the UI renders must come from a "
                    "claim in this file. A string that is not a claim_id lookup is a "
                    "contract violation.",
        "correction_pathway": {
            "enabled": True,
            "instructions": "Every claim on this dashboard links to the artifact it "
                            "rests on. If a claim is wrong, open an issue quoting the "
                            "claim_id shown beside it.",
            "contact_field": "claim_id"}}, len(_claims))
    write("methodology.json", {
        "methodology_version": "1.0.0", "export_schema_version": "1.0.0",
        "impact_definition": "Observable engineering impact is a defensible change in "
                             "product capability, user experience, system quality, "
                             "organizational leverage, or future delivery capacity that "
                             "can be materially attributed to an engineer's decisions "
                             "and contributions using public evidence.",
        "unit_of_analysis": "impact episode (a connected initiative arc), not the "
                            "commit or the pull request",
        "formulas": {
            "portfolio_aggregation": "value = min(scale_max, v1 + min(headroom, "
                                     "sum(coeff_i * v_i for i >= 2)))",
            "time_decay": "exp(-ln(2) * age_days / half_life_days)",
            "hub_damping": "path_weight = 1 / (1 + log2(1 + fan_in))",
            "edge_combination": "pair_weight = 1 - prod(1 - edge_strength_i)  (noisy-OR)",
            "concordance": "c_j(a,b) = 1 if g_j(a) >= g_j(b) - q; 0 if g_j(a) <= g_j(b) - p",
            "discordance": "d_j(a,b) = 0 if g_j(b) <= g_j(a) + p; 1 if g_j(b) >= g_j(a) + v",
            "credibility": "C(a,b) * prod over j where d_j > C of (1 - d_j) / (1 - C)"},
        "explicitly_not_used": ["commit count", "pull-request count", "lines of code",
                                "review count", "velocity ratios",
                                "any 0-1000 composite score", "per-day normalisation",
                                "gradient-descent-learned weights"],
        "llm": {"provider": None, "model": None, "available": False,
                "role": "Structured evidence extraction and summarisation only. The LLM "
                        "never produces the final ranking.",
                "note": "FIXTURE DATA — the real methodology.json contains the complete "
                        "rubric, attribution matrix, outranking config and analytics "
                        "parameters."},
        "fixture": True})
    write("coverage.json", {
        "phase1": {"status": "verified", "input_source": "artifacts",
                   "tables_present": 27, "tables_expected": 27},
        "known_gaps": [
            {"gap": "shallow_clone",
             "detail": "clone strategy shallow_since; survival and reachability look "
                       "forward only",
             "consequence": "has_merge_commit_in_clone=false PRs have no pr_files rows",
             "severity": "structural"},
            {"gap": "unparsed_languages",
             "detail": "1774 files in ['go','hog','rb','rust','sql'] have no import parser",
             "consequence": "reachability_band='unknown' for changes confined to them",
             "severity": "structural"}],
        "capabilities_disabled": {},
        "validation": {
            "status": "pending_human_review", "publishable": False,
            "publishable_blockers": [
                {"item": "cluster_audit", "status": "pending",
                 "queue_file": "audit_episode_clusters.json"},
                {"item": "finalist_approval", "status": "pending",
                 "queue_file": "audit_finalist_approvals.json"}],
            "items": [{"item": "adversarial", "status": "pass"},
                      {"item": "claim_audit", "status": "pass"},
                      {"item": "reproducibility", "status": "pass"}]},
        "limitations": {
            "headline": "This measures observable repository impact in a 90-day public "
                        "GitHub window. It is not a measure of an engineer's total value.",
            "items": LIMITATIONS, "claim_ids": limitation_claims,
            "correction_pathway": {"enabled": True,
                                   "instructions": "Open an issue quoting the claim_id.",
                                   "contact_field": "claim_id"}},
        "missingness": {
            "dimension_unknown_rates": {
                d: {"assessed": 5, "unknown": 2 if d in UNKNOWN_REASONS else 0,
                    "unknown_rate": 0.4 if d in UNKNOWN_REASONS else 0.0}
                for d in DIMENSIONS},
            "episodes_without_diff": 0,
            "episodes_without_release_corroboration": 3,
            "engineers_below_evidence_bar": 1,
            "note": "Unknown is not zero anywhere in this package."}})

    indexes = {
        "episodes_by_component": {}, "episodes_by_status": {},
        "episodes_by_engineer": {}, "engineers_by_role": {},
        "engineers_by_strongest_dimension": {},
    }
    for episode in episodes:
        for component in episode["components"]:
            indexes["episodes_by_component"].setdefault(component, []).append(
                episode["episode_id"])
        indexes["episodes_by_status"].setdefault(episode["status"], []).append(
            episode["episode_id"])
        for participant in episode["participants"]:
            indexes["episodes_by_engineer"].setdefault(
                participant["actor_cluster_id"], []).append(episode["episode_id"])
    for engineer in engineers:
        for role in engineer["roles_held"]:
            indexes["engineers_by_role"].setdefault(role, []).append(
                engineer["actor_cluster_id"])
        if engineer["strongest_dimension"]:
            indexes["engineers_by_strongest_dimension"].setdefault(
                engineer["strongest_dimension"], []).append(engineer["actor_cluster_id"])
    write("indexes.json", indexes)

    manifest = {
        "manifest_version": "1.0.0", "generated_at": "2026-08-17T00:00:00Z",
        "methodology_version": "1.0.0",
        "title": "PostHog observable repository impact",
        "subtitle": "Explainable impact analytics over a 90-day public GitHub window",
        "fixture": True,
        "fixture_note": "SYNTHETIC DATA for Phase 3 development. Logins are fictional. "
                        "Swap the data directory for artifacts/phase3/ in production; "
                        "no code change should be required.",
        "source": {"repository_url": REPO,
                   "analyzed_head_sha": "d4295d5794f95a0ae726edd0e27450115f3fc0a3",
                   "is_shallow_clone": True},
        "window": {"start": "2026-05-19T00:00:00Z", "end": "2026-08-17T00:00:00Z",
                   "lookback_days": 90},
        "phase1_provenance": {"input_source": "artifacts", "verification_status": "verified",
                              "phase1_schema_version": "1.0.0"},
        "counts": {"episodes": len(episodes), "engineers": len(engineers),
                   "rankable_engineers": sum(1 for e in engineers if e["rankable"]),
                   "claims": len(_claims), "dimension_assessments": len(episodes) * 6,
                   "participants": sum(len(e["participants"]) for e in episodes),
                   "propagation_edges": 44, "review_interventions": 1},
        "files": files,
        "indexes": {"file": "indexes.json", "available": sorted(indexes)},
        "validation_status": "pending_human_review", "publishable": False,
        "publishable_blockers": [
            {"item": "cluster_audit", "status": "pending",
             "queue_file": "audit_episode_clusters.json"},
            {"item": "finalist_approval", "status": "pending",
             "queue_file": "audit_finalist_approvals.json"}],
        "safety_scan": {"status": "pass", "violations": []},
        "limitations_headline": "This measures observable repository impact in a 90-day "
                                "public GitHub window. It is not a measure of an "
                                "engineer's total value.",
        "ui_contract": {
            "render_only_claims": True,
            "claim_lookup": "claims.json -> claims[] keyed by claim_id",
            "never_render": ["any string not resolvable as a claim_id",
                             "a composite score", "a percentage of shared credit"],
            "must_display": ["window start and end", "analyzed_head_sha",
                             "the limitations headline",
                             "unknown-vs-zero distinction on every dimension",
                             "release_corroboration alongside episode status"]},
    }
    (OUT / "dashboard_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    total = sum(f["bytes"] for f in files.values())
    print(f"wrote {len(files) + 1} files to {OUT.relative_to(ROOT)} "
          f"({total / 1024:.1f} KB, {len(_claims)} claims)")


if __name__ == "__main__":
    main()
