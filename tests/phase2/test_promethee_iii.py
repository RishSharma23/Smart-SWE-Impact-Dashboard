"""PROMETHEE III, the interval-order cross-check.

The point of this method is what it declines to do. PROMETHEE II sorts by net
flow and therefore always emits a strict total order, breaking ties it has no
basis to break; every shared tier ELECTRE III reports comes back from it as a
disagreement whether or not the evidence supports one. PROMETHEE III puts an
interval around the same net flow and calls two alternatives indifferent when
those intervals overlap, so it can agree with a shared tier.

These tests pin the properties that make it that method rather than a second
sorted list: the net flow is PROMETHEE II's exactly, alpha=0 degenerates to a
strict order, indifference is symmetric, and widening alpha only ever adds
indifference.
"""

from __future__ import annotations

import random

import pytest

from impact2.config import load_config
from impact2.rank.outranking import promethee_ii, promethee_iii


@pytest.fixture(scope="module")
def weights() -> dict[str, float]:
    return load_config().criterion_weights("balanced")


@pytest.fixture(scope="module")
def thresholds(weights) -> dict[str, dict[str, float]]:
    return {c: {"q": 0.0, "p": 1.0, "v": 3.0} for c in weights}


def _portfolios(values: list[float], dims: list[str]) -> list[dict]:
    return [
        {
            "actor_cluster_id": f"a{i:02d}",
            "rankable": True,
            "dimension_values": {d: v for d in dims},
        }
        for i, v in enumerate(values)
    ]


@pytest.fixture
def separated_and_clustered(weights) -> list[dict]:
    """Three clearly separated alternatives, then nine near-ties.

    A method worth having separates the first three and declines to separate
    the rest. A total order pretends to separate all twelve.
    """
    dims = list(weights)
    random.seed(20260819)
    values = [4.0, 3.3, 2.6] + [1.2 + i * 0.01 for i in range(9)]
    rows = _portfolios(values, dims)
    for row in rows:  # a little noise so no two are bit-identical
        row["dimension_values"] = {
            d: max(0.0, v + random.uniform(-0.02, 0.02))
            for d, v in row["dimension_values"].items()
        }
    return rows


# -- the invariant that keeps the two methods honest -------------------------

def test_net_flow_is_exactly_promethee_ii(separated_and_clustered, weights, thresholds):
    """III must differ from II only in admitting indifference, never in the
    underlying quantity. If these drift apart, the agreement figures stop being
    comparable and the cross-check means nothing."""
    ii = {r["actor_cluster_id"]: r["net_flow"] for r in promethee_ii(separated_and_clustered, weights, thresholds)}
    iii = {
        r["actor_cluster_id"]: r["net_flow"]
        for r in promethee_iii(separated_and_clustered, weights, thresholds, alpha=0.15)
    }
    assert ii == iii


def test_alpha_zero_degenerates_to_a_strict_order(separated_and_clustered, weights, thresholds):
    rows = promethee_iii(separated_and_clustered, weights, thresholds, alpha=0.0)
    assert len({r["position"] for r in rows}) == len(rows)
    assert all(r["indifferent_count"] == 0 for r in rows)


def test_widening_alpha_only_ever_adds_indifference(separated_and_clustered, weights, thresholds):
    counts = []
    for alpha in (0.0, 0.1, 0.25, 0.5, 1.0, 2.0):
        rows = promethee_iii(separated_and_clustered, weights, thresholds, alpha=alpha)
        counts.append(sum(r["indifferent_count"] for r in rows))
    assert counts == sorted(counts), f"indifference is not monotone in alpha: {counts}"


# -- the properties of an interval order -------------------------------------

def test_indifference_is_symmetric(separated_and_clustered, weights, thresholds):
    rows = promethee_iii(separated_and_clustered, weights, thresholds, alpha=0.5)
    # indifferent_with is truncated for display, so rebuild it from the intervals.
    bounds = {r["actor_cluster_id"]: tuple(r["interval"]) for r in rows}
    for a, (xa, ya) in bounds.items():
        for b, (xb, yb) in bounds.items():
            if a == b:
                continue
            a_pref_b = xa > yb
            b_pref_a = xb > ya
            assert not (a_pref_b and b_pref_a), f"{a} and {b} strictly beat each other"


def test_interval_is_centred_on_the_net_flow(separated_and_clustered, weights, thresholds):
    for row in promethee_iii(separated_and_clustered, weights, thresholds, alpha=0.3):
        low, high = row["interval"]
        assert low <= row["net_flow"] <= high
        assert row["dispersion"] >= 0.0
        assert high - low == pytest.approx(2 * 0.3 * row["dispersion"], abs=1e-5)


def test_position_counts_only_strictly_better_alternatives(
    separated_and_clustered, weights, thresholds
):
    rows = promethee_iii(separated_and_clustered, weights, thresholds, alpha=0.15)
    bounds = {r["actor_cluster_id"]: tuple(r["interval"]) for r in rows}
    for row in rows:
        xa, ya = bounds[row["actor_cluster_id"]]
        beaten_by = sum(1 for b, (xb, _) in bounds.items() if b != row["actor_cluster_id"] and xb > ya)
        assert row["position"] == beaten_by + 1


# -- the behaviour the method exists for -------------------------------------

def test_it_separates_the_separable_and_not_the_rest(
    separated_and_clustered, weights, thresholds
):
    rows = {
        r["actor_cluster_id"]: r
        for r in promethee_iii(separated_and_clustered, weights, thresholds, alpha=0.15)
    }
    # The alternative that beats the whole field stands alone, and is the only
    # one that does. Note what this does *not* assert: a01 and a02 are also well
    # clear of the cluster on net flow, but each loses decisively to someone
    # above it, so their intervals are wide and they overlap each other. That is
    # the method working, not a defect. See the dispersion test below.
    assert rows["a00"]["indifferent_count"] == 0
    assert rows["a00"]["position"] == 1
    # The nine near-ties are not pretended apart.
    assert rows["a03"]["indifferent_count"] >= 5
    # And a total order would have claimed to separate all twelve.
    assert len({r["position"] for r in rows.values()}) < len(rows)


def test_dispersion_reflects_a_mixed_record_not_just_a_low_position(
    separated_and_clustered, weights, thresholds
):
    """The interval width is the dispersion of an alternative's pairwise
    comparisons, so it says how *firm* a position is rather than how high.

    An alternative that beat everyone by the same margin has a narrow interval.
    One that beat most of the field decisively but lost decisively to someone
    has a wide one, and is genuinely harder to place. Losing this property would
    reduce PROMETHEE III to PROMETHEE II with arbitrary padding.
    """
    rows = {
        r["actor_cluster_id"]: r
        for r in promethee_iii(separated_and_clustered, weights, thresholds, alpha=0.15)
    }
    dominant = rows["a00"]        # beats all eleven, uniformly
    mixed = rows["a02"]           # beats nine, loses to two
    assert dominant["dispersion"] < mixed["dispersion"]
    assert dominant["net_flow"] > mixed["net_flow"]


def test_a_uniform_field_is_entirely_indifferent(weights, thresholds):
    """Twelve identical portfolios carry no information about who is better.
    PROMETHEE II still ranks them 1 to 12; this must not."""
    rows = promethee_iii(_portfolios([2.0] * 12, list(weights)), weights, thresholds, alpha=0.15)
    assert {r["position"] for r in rows} == {1}
    assert all(r["indifferent_count"] == 11 for r in rows)


# -- edge cases --------------------------------------------------------------

@pytest.mark.parametrize("size", [0, 1])
def test_degenerate_sizes_do_not_raise(size, weights, thresholds):
    rows = promethee_iii(_portfolios([1.0] * size, list(weights)), weights, thresholds, alpha=0.15)
    assert len(rows) == size
    assert all(r["position"] == 1 for r in rows)


def test_unknown_dimensions_do_not_become_zero(weights, thresholds):
    """A pair with nothing comparable contributes no preference either way. It
    must not read as a tie at the bottom, and it must not raise."""
    dims = list(weights)
    rows = promethee_iii(
        [
            {"actor_cluster_id": "known", "rankable": True,
             "dimension_values": {d: 3.0 for d in dims}},
            {"actor_cluster_id": "unknown", "rankable": True,
             "dimension_values": {d: None for d in dims}},
        ],
        weights, thresholds, alpha=0.15,
    )
    assert len(rows) == 2
    assert all(r["net_flow"] == 0.0 for r in rows)
