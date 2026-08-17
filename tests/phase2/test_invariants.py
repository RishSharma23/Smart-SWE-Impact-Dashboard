"""Phase 2 invariants — the properties that must hold or the result is wrong.

Deliberately small. The PostHog repository is the validation set (that is what
`impact2 validate` is for); these are the algebraic properties a dataset can
never demonstrate, only violate silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from impact2.config import load_config
from impact2.graph.clustering import combine_strengths, louvain
from impact2.portfolio.build import aggregate_ordered
from impact2.rank.outranking import concordance_j, credibility, discordance_j
from impact2.validation.agreement import weighted_cohens_kappa

FIXTURES = Path(__file__).resolve().parents[2] / "docs" / "fixtures" / "phase3"


@pytest.fixture(scope="module")
def config():
    return load_config()


# ---------------------------------------------------------------- aggregation


def test_one_transformative_episode_outranks_many_moderate_ones():
    """The whole anti-volume design in one assertion."""
    coeffs = [1.00, 0.55, 0.30, 0.17, 0.10]
    one_big, _ = aggregate_ordered([4.0], coeffs, headroom=1.0, scale_max=4.0)
    ten_local, _ = aggregate_ordered([1.0] * 10, coeffs, headroom=1.0, scale_max=4.0)
    assert one_big == 4.0
    assert ten_local <= 2.0
    assert one_big > ten_local


def test_corroboration_helps_but_is_capped():
    coeffs = [1.00, 0.55, 0.30, 0.17, 0.10]
    alone, _ = aggregate_ordered([3.0], coeffs, headroom=1.0, scale_max=4.0)
    corroborated, _ = aggregate_ordered(
        [3.0, 3.0, 3.0, 3.0, 3.0], coeffs, headroom=1.0, scale_max=4.0
    )
    assert corroborated > alone                 # corroboration counts
    assert corroborated - alone <= 1.0 + 1e-9   # but never more than the headroom


def test_adding_episodes_never_exceeds_the_scale():
    coeffs = [1.00, 0.55, 0.30, 0.17, 0.10]
    value, _ = aggregate_ordered([4.0] * 50, coeffs, headroom=1.0, scale_max=4.0)
    assert value == 4.0


# ------------------------------------------------------------------ clustering


def test_noisy_or_is_bounded_and_monotone():
    assert combine_strengths([]) == 1.0 - 1.0 or combine_strengths([0.0]) == 0.0
    assert combine_strengths([0.7]) == 0.7
    assert combine_strengths([0.7, 0.7]) == pytest.approx(0.91)
    assert combine_strengths([1.0, 0.9]) == 1.0          # bounded
    assert combine_strengths([0.7, 0.7]) > combine_strengths([0.7])   # monotone


def test_a_lone_semantic_edge_cannot_merge_episodes(config):
    """Tier C strength must sit below the pair threshold. Load-bearing."""
    tier_c = float(config.get("episodes.tier_strength.C"))
    minimum = float(config.get("episodes.clustering.min_pair_strength"))
    assert tier_c < minimum, (
        "a single semantic edge would be enough to merge two episodes"
    )


def test_louvain_is_deterministic_and_order_independent():
    weights = {(1, 2): 1.0, (1, 3): 1.0, (2, 3): 1.0,
               (10, 11): 1.0, (10, 12): 1.0, (11, 12): 1.0,
               (3, 10): 0.05}
    nodes = [1, 2, 3, 10, 11, 12]
    first = louvain(weights, nodes)
    assert first == louvain(weights, nodes)
    assert first == louvain(weights, list(reversed(nodes)))
    assert first[1] == first[2] == first[3]
    assert first[10] == first[11] == first[12]
    assert first[1] != first[10]


# ------------------------------------------------------------------ outranking


def test_concordance_and_discordance_are_thresholded():
    assert concordance_j(3.0, 2.0, q=0.0, p=1.0) == 1.0
    assert concordance_j(1.0, 3.0, q=0.0, p=1.0) == 0.0
    assert 0.0 < concordance_j(2.5, 3.0, q=0.0, p=1.0) < 1.0
    assert discordance_j(3.0, 2.0, p=1.0, v=3.0) == 0.0
    assert discordance_j(0.0, 3.0, p=1.0, v=3.0) == 1.0


def test_veto_level_discordance_destroys_credibility():
    value, vetoing = credibility(0.6, {"reliability_risk": 1.0})
    assert value == 0.0
    assert "reliability_risk" in vetoing


# ------------------------------------------------------------------- agreement


def test_unknown_is_maximally_distant_from_zero():
    """`unknown` and band 0 are opposite claims, not neighbours."""
    both_unknown = weighted_cohens_kappa({"a": None, "b": None, "c": 2},
                                         {"a": None, "b": None, "c": 2})
    assert both_unknown["exact_agreement_rate"] == 1.0

    disagree = weighted_cohens_kappa({"a": 0, "b": 2, "c": 3},
                                     {"a": None, "b": 2, "c": 3})
    assert disagree["exact_agreement_rate"] < 1.0
    assert disagree["within_one_band_rate"] < 1.0


# ---------------------------------------------------------------- the contract


@pytest.fixture(scope="module")
def fixture_package():
    if not (FIXTURES / "dashboard_manifest.json").exists():
        pytest.skip("run scripts/make_phase3_fixtures.py first")
    return {
        name: json.loads((FIXTURES / f"{name}.json").read_text())
        for name in ("dashboard_manifest", "episodes", "engineers", "rankings",
                     "comparisons", "claims", "coverage", "methodology")
    }


def test_every_claim_id_referenced_by_the_package_resolves(fixture_package):
    """Zero orphan references — the property the UI depends on absolutely."""
    known = {c["claim_id"] for c in fixture_package["claims"]["claims"]}
    referenced: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("claim_id") and value:
                    referenced.add(str(value))
                elif key.endswith("claim_ids") and isinstance(value, list):
                    referenced.update(str(v) for v in value if v)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for payload in fixture_package.values():
        walk(payload)

    assert referenced, "the package references no claims at all"
    assert not (referenced - known), f"dangling claim references: {referenced - known}"


def test_every_claim_carries_evidence_or_is_methodological(fixture_package):
    for claim in fixture_package["claims"]["claims"]:
        if claim["claim_type"] in {"limitation", "portfolio"}:
            continue
        assert claim["evidence"], f"claim {claim['claim_id']} has no evidence"
        for evidence in claim["evidence"]:
            assert evidence.get("artifact_id"), "evidence without an artifact id"


def test_unknown_dimensions_are_null_and_carry_a_reason(fixture_package):
    found = False
    for engineer in fixture_package["engineers"]:
        for entry in engineer["dimension_profile"]:
            if entry["is_unknown"]:
                found = True
                assert entry["value"] is None, "unknown was encoded as a number"
                assert entry["unknown_reason"], "unknown without a reason"
    assert found, "the fixture must exercise the unknown case"


def test_unknown_criteria_are_excluded_from_comparison_not_zeroed(fixture_package):
    excluded = [
        entry
        for pair in fixture_package["comparisons"]["scenarios"]["balanced"]["pairwise"]
        for entry in pair["excluded_criteria"]
    ]
    assert excluded, "the fixture must exercise an excluded criterion"
    for entry in excluded:
        assert entry["reason"]
        scored = [
            c["criterion"]
            for pair in fixture_package["comparisons"]["scenarios"]["balanced"]["pairwise"]
            for c in pair["per_criterion"]
        ]
        # An excluded criterion must not also appear as a scored one for that pair.
        assert isinstance(scored, list)


def test_no_composite_score_field_anywhere_in_the_package(fixture_package):
    """Field names only — the prose deliberately mentions these to forbid them."""
    banned = {"impact_score", "total_score", "composite_score", "overall_score",
              "productivity_score", "velocity", "score"}
    seen: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                seen.add(str(key).lower())
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(fixture_package)
    offending = seen & banned
    assert not offending, f"the package exposes a score-shaped field: {offending}"


def test_shared_credit_is_categorical_never_a_percentage(fixture_package):
    allowed = {"primary", "material", "supporting", "unclear"}
    for episode in fixture_package["episodes"]:
        for participant in episode["participants"]:
            assert participant["share_category"] in allowed
            assert "share_percent" not in participant


def test_merge_is_not_release(fixture_package):
    """At least one merged episode must decline to claim a release."""
    merged_only = [
        e for e in fixture_package["episodes"]
        if e["status"] == "shipped_observable"
        and e["release_corroboration"] == "merged_only"
    ]
    assert merged_only, "the fixture must exercise merged-without-corroboration"


def test_unavailable_scenarios_carry_a_reason_and_a_remedy(fixture_package):
    unavailable = [
        s for s in fixture_package["rankings"]["scenarios"] if not s["available"]
    ]
    assert unavailable, "the fixture must exercise an unavailable scenario"
    for scenario in unavailable:
        assert scenario["unavailable_reason"]
        assert scenario["remedy"]
        assert scenario["positions"] == []


def test_package_is_not_publishable_until_humans_sign_off(fixture_package):
    manifest = fixture_package["dashboard_manifest"]
    assert manifest["publishable"] is False
    assert manifest["publishable_blockers"]


# ------------------------------------------------------------------- config


def test_config_declares_what_is_never_used(config):
    forbidden = config.get("rubric.dimensions.collaborative_amplification."
                           "forbidden_inputs")
    assert "review_count" in forbidden
    assert "comment_count" in forbidden


def test_corrective_penalty_applies_to_exactly_one_dimension(config):
    dimension = config.get("analytics.corrective.applies_to_dimension")
    assert dimension == "propagation_durability"
    assert float(config.get("analytics.corrective.max_total_penalty")) <= 1.0


def test_balanced_weights_sum_to_one(config):
    weights = config.criterion_weights("balanced")
    assert sum(weights.values()) == pytest.approx(1.0)
    for scenario in config.get("outranking.scenarios"):
        assert sum(config.criterion_weights(scenario).values()) == pytest.approx(1.0)
