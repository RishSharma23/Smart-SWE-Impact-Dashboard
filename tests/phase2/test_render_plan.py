"""The export projection: what ships, what does not, and what is said about it.

The rule these test used to live in TypeScript, where the export could not see
it. Two implementations of one priority order is one implementation too many, so
it moved to Python and the site now reads the decision out of the manifest.
These are the properties that make that move safe:

* the priority order really is top-five-first, so the two-click evidence path
  survives the cap,
* a record is included whole or omitted whole, never trimmed,
* an omission is counted, so a reader can tell what they are not holding,
* `full` mode is still full.
"""

from __future__ import annotations

import pytest

from impact2 import render_plan
from impact2.render_plan import RenderBudget


def engineer(actor, *, strongest=None, current=(), foundational=(), tops=(),
             episodes=None):
    return {
        "actor_cluster_id": actor,
        "strongest_evidence_episode_id": strongest,
        "dimension_profile": [{"dimension": f"d{i}", "top_episode_id": t}
                              for i, t in enumerate(tops)],
        "current_episode_ids": list(current),
        "foundational_episode_ids": list(foundational),
        "episode_ids": list(
            episodes
            if episodes is not None
            else [*( [strongest] if strongest else [] ), *current, *foundational]
        ),
        "thesis_claim_ids": [f"claim/thesis-{actor}"],
        "uncertainty": {"claim_id": f"claim/stability-{actor}"},
    }


def scenario(name, actors, *, available=True):
    return {
        "scenario": name,
        "available": available,
        "positions": [
            {"position": i + 1, "actor_cluster_id": a} for i, a in enumerate(actors)
        ],
    }


# ------------------------------------------------------------ featured order

def test_featured_order_is_the_profile_order_and_deduplicates():
    row = engineer(
        "a", strongest="e1", tops=("e2", "e1"), current=("e3",), foundational=("e2",)
    )
    assert render_plan.featured_episode_ids(row) == ["e1", "e2", "e3"]


def test_featured_skips_nulls_rather_than_emitting_them():
    row = engineer("a", strongest=None, tops=(None, "e9"), current=(), foundational=())
    assert render_plan.featured_episode_ids(row) == ["e9"]


# ------------------------------------------------------------- page priority

def test_top_five_of_every_available_scenario_come_first():
    engineers = [engineer(a, strongest=f"ep-{a}") for a in "abcdefgh"]
    scenarios = [
        scenario("balanced", list("abcdefgh")),
        scenario("leverage_emphasis", ["h", "g", "f", "e", "d", "c", "b", "a"]),
    ]
    order = render_plan.episode_page_order(
        engineers, scenarios,
        budget=RenderBudget(),
        known_episode_ids=[f"ep-{a}" for a in "abcdefgh"],
    )
    # The top five of the first scenario, then the top five of the second (of
    # which e and d are already in), then everyone who only appears further down
    # a ranking. Nobody is lost, and the order is the evidence path.
    assert order == [
        "ep-a", "ep-b", "ep-c", "ep-d", "ep-e",   # balanced, positions 1 to 5
        "ep-h", "ep-g", "ep-f",                   # leverage_emphasis, 1 to 3
    ]


def test_an_unavailable_scenario_does_not_get_priority():
    engineers = [engineer(a, strongest=f"ep-{a}") for a in "abc"]
    scenarios = [
        scenario("last_12_months", ["c"], available=False),
        scenario("balanced", ["a", "b", "c"]),
    ]
    order = render_plan.episode_page_order(
        engineers, scenarios, budget=RenderBudget(),
        known_episode_ids=["ep-a", "ep-b", "ep-c"],
    )
    assert order[0] == "ep-a"


def test_pages_are_capped_and_the_overflow_is_counted():
    engineers = [engineer(f"a{i}", strongest=f"ep-{i}") for i in range(10)]
    scenarios = [scenario("balanced", [f"a{i}" for i in range(10)])]
    plan = render_plan.build(
        engineers, scenarios,
        budget=RenderBudget(episode_pages=4),
        known_episode_ids=[f"ep-{i}" for i in range(10)],
    )
    assert plan.episode_page_ids == ["ep-0", "ep-1", "ep-2", "ep-3"]
    assert plan.episode_pages_truncated == 6


def test_an_episode_the_package_never_had_is_not_promised_a_page():
    engineers = [engineer("a", strongest="ep-missing", current=("ep-real",))]
    scenarios = [scenario("balanced", ["a"])]
    plan = render_plan.build(
        engineers, scenarios, budget=RenderBudget(), known_episode_ids=["ep-real"],
    )
    assert plan.episode_page_ids == ["ep-real"]
    assert "ep-missing" not in plan.episode_ids


# ------------------------------------------------------------ listing budget

def test_listings_are_included_up_to_the_caps_the_profile_renders_with():
    row = engineer(
        "a",
        strongest="f0",
        current=[f"c{i}" for i in range(10)],
        foundational=[f"n{i}" for i in range(10)],
    )
    known = ["f0"] + [f"c{i}" for i in range(10)] + [f"n{i}" for i in range(10)]
    plan = render_plan.build(
        [row], [scenario("balanced", ["a"])],
        budget=RenderBudget(episode_pages=2, featured=3, current=2, foundational=2,
                            other=0),
        known_episode_ids=known,
    )
    # featured[:3] is f0, c0, c1; current[:2] is c0, c1; foundational[:2] is n0, n1.
    assert plan.episode_ids == {"f0", "c0", "c1", "n0", "n1"}


def test_the_listing_is_deduplicated_because_callers_count_over_it():
    """The lists overlap by design. A repeated id counts a collaborator twice."""
    row = engineer("a", strongest="e1", current=("e1", "e2"), foundational=("e2",))
    ids = render_plan.listing_ids(row, RenderBudget())
    assert ids == list(dict.fromkeys(ids))
    assert ids == ["e1", "e2"]


def test_an_episode_attributed_but_never_listed_is_omitted():
    row = engineer("a", strongest="f0", episodes=["f0", "extra"])
    plan = render_plan.build(
        [row], [scenario("balanced", ["a"])],
        budget=RenderBudget(featured=1, current=0, foundational=0, other=0),
        known_episode_ids=["f0", "extra"],
    )
    assert plan.episode_ids == {"f0"}


def test_the_other_table_is_what_is_attributed_but_not_featured():
    row = engineer("a", strongest="f0", episodes=["f0", "x1", "x2", "x3"])
    plan = render_plan.build(
        [row], [scenario("balanced", ["a"])],
        budget=RenderBudget(featured=1, current=0, foundational=0, other=2),
        known_episode_ids=["f0", "x1", "x2", "x3"],
    )
    assert plan.episode_ids == {"f0", "x1", "x2"}


# ---------------------------------------------------------------- claim scope

EPISODE = {
    "episode_id": "ep-1",
    "title_claim_id": "claim/title",
    "problem_claim_id": "claim/problem",
    "intervention_claim_id": "claim/intervention",
    "outcome_claim_id": "claim/outcome",
    "dimensions": [{"dimension": "product_outcome",
                    "rationale_claim_id": "claim/dimension"}],
    "participants": [{"actor_cluster_id": "a", "claim_ids": ["claim/attribution"]}],
    "artifact_ids": ["pr/1", "commit/abc"],
}


def test_an_episode_with_a_page_carries_every_claim_that_page_renders():
    assert render_plan.claim_ids_for_episode(EPISODE, has_page=True) == {
        "claim/title", "claim/problem", "claim/intervention", "claim/outcome",
        "claim/dimension", "claim/attribution",
    }


def test_an_episode_in_a_listing_carries_only_the_sentence_the_listing_shows():
    assert render_plan.claim_ids_for_episode(EPISODE, has_page=False) == {"claim/title"}


def test_a_profile_always_carries_its_thesis_and_its_stability_sentence():
    assert render_plan.claim_ids_for_engineer(engineer("a")) == {
        "claim/thesis-a", "claim/stability-a",
    }


def test_evidence_follows_the_episode_that_resolves_it():
    assert render_plan.artifact_ids_for_episode(EPISODE) == {"pr/1", "commit/abc"}


# ----------------------------------------------------------------- the budget

def test_the_budget_comes_from_config_and_falls_back_to_the_shipped_defaults():
    budget = RenderBudget.from_config(
        {"episode_pages": 12, "per_engineer": {"featured": 2}}
    )
    assert budget.episode_pages == 12
    assert budget.featured == 2
    assert budget.current == RenderBudget().current


def test_the_budget_is_published_in_the_shape_the_site_reads():
    block = render_plan.RenderPlan(
        budget=RenderBudget(), episode_page_ids=["ep-1"], episode_pages_truncated=3,
    ).manifest_block()
    assert block["episode_page_ids"] == ["ep-1"]
    assert block["episode_pages_truncated"] == 3
    assert block["per_engineer"]["featured"] == 8
    assert "rule" in block and len(block["rule"]) > 80
