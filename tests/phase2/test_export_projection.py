"""The exporter writes the projection it advertises, and nothing else changes.

The package is the only thing a reader ever sees, so the properties worth
holding are about the package: it carries whole records, it carries the ones
its own plan names, it counts what it left out, and `full` still means full.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from impact2.config import load_config
from impact2.export import Exporter


class FakeInputs:
    repository_url = "https://github.com/acme/widget"
    head_sha = "d4295d5794f9"
    is_shallow = True
    known_gaps: list[dict[str, Any]] = []
    capabilities_disabled: dict[str, str] = {}
    manifest = {"window": {"start": "2026-05-21T00:00:00Z",
                           "end": "2026-08-19T00:00:00Z", "lookback_days": 90}}

    def verification_report(self) -> dict[str, Any]:
        return {"status": "ok", "tables_present": 27, "tables_expected": 27,
                "capabilities_disabled": {}}

    def provenance(self) -> dict[str, Any]:
        return {"run_id": "test"}


def episode(index: int, actor: str) -> dict[str, Any]:
    return {
        "episode_id": f"ep-{index}",
        "title": f"Episode {index}",
        "status": "shipped",
        "components": ["core"],
        "started_at": "2026-06-01T00:00:00Z",
        "ended_at": "2026-06-08T00:00:00Z",
        "file_count": 3,
        "release_corroboration": "corroborated",
        "pr_numbers": [1000 + index],
    }


class FakePipeline:
    """Just enough of the pipeline for the exporter to write a package."""

    def __init__(self, episode_count: int = 6, per_engineer: int = 3) -> None:
        self.episodes = [episode(i, "actor/a") for i in range(episode_count)]
        self.dimensions = [
            {
                "episode_id": f"ep-{i}",
                "dimension": "product_outcome",
                "dimension_record_id": f"dim-{i}",
                "band": 2,
                "band_label": "moderate",
                "is_unknown": False,
                "evidence": [{"artifact_id": f"pr/{i}"}],
            }
            for i in range(episode_count)
        ]
        self.participants = [
            {
                "episode_id": f"ep-{i}",
                "participant_id": f"part-{i}",
                "actor_cluster_id": "actor/a",
                "login": "ada",
                "roles": ["author"],
                "has_any_evidence": True,
                "direct_evidence": [],
            }
            for i in range(episode_count)
        ]
        self.episode_artifacts = [
            {
                "episode_id": f"ep-{i}",
                "artifact_id": f"pr/{i}",
                "artifact_kind": "pull_request",
                "title": f"PR {i}",
                "url": f"https://github.com/acme/widget/pull/{i}",
                "evidence_provenance": "deterministic:phase1.pull_requests",
                "detail": None,
            }
            for i in range(episode_count)
        ]
        self.propagation_summary: list[dict[str, Any]] = []
        self.novelty: list[dict[str, Any]] = []
        self.corrective: list[dict[str, Any]] = []
        self.interventions: list[dict[str, Any]] = []
        self.propagation_edges: list[dict[str, Any]] = []
        self.summaries: dict[str, Any] = {}
        self.inputs = FakeInputs()
        self.portfolios = [
            {
                "actor_cluster_id": "actor/a",
                "login": "ada",
                "display_name": "Ada",
                "portfolio_id": "pf-a",
                "dimension_values": {"product_outcome": 2.0},
                "dimension_intervals": {},
                "dimension_confidence": {},
                "dimension_detail": {
                    "product_outcome": {"top_episode_id": "ep-0", "episode_count": 1}
                },
                "unknown_dimensions": [],
                "strongest_dimension": "product_outcome",
                "strongest_evidence_episode_id": "ep-0",
                "episode_ids": [f"ep-{i}" for i in range(episode_count)],
                "episode_count": episode_count,
                "current_episode_ids": [f"ep-{i}" for i in range(per_engineer)],
                "foundational_episode_ids": [],
                "roles_held": ["author"],
                "rankable": True,
            }
        ]
        self.scenarios = [{"scenario": "balanced", "label": "Balanced"}]
        self.ranking_runs = [
            {
                "scenario": "balanced",
                "available": True,
                "ranking": [
                    {"position": 1, "tier": 1, "actor_cluster_id": "actor/a",
                     "login": "ada"}
                ],
                "comparisons": [],
                "cross_check": {},
            }
        ]


def claims_for(episode_count: int) -> list[dict[str, Any]]:
    rows = []
    for i in range(episode_count):
        for kind in ("title", "problem", "dimension", "attribution"):
            rows.append(
                {
                    "claim_id": f"claim/{kind}-{i}",
                    "claim_type": "episode_narrative",
                    "text": f"{kind} sentence for episode {i}.",
                    "evidence": [],
                    "evidence_count": 0,
                }
            )
    rows.append({"claim_id": "claim/thesis", "claim_type": "portfolio",
                 "text": "Ada's thesis.", "evidence": [], "evidence_count": 0})
    return rows


def claim_index_for(episode_count: int) -> dict[str, Any]:
    return {
        "episodes": {
            f"ep-{i}": {"title": f"claim/title-{i}", "problem": f"claim/problem-{i}"}
            for i in range(episode_count)
        },
        "dimensions": {f"dim-{i}": f"claim/dimension-{i}" for i in range(episode_count)},
        "participants": {
            f"part-{i}": [f"claim/attribution-{i}"] for i in range(episode_count)
        },
        "portfolios": {"actor/a": ["claim/thesis"]},
        "comparisons": {},
        "stability": {},
        "limitations": [],
    }


def build(tmp_path, *, mode: str, episode_pages: int = 2, featured: int = 1,
          current: int = 2, foundational: int = 0, other: int = 0,
          episode_count: int = 6):
    config = load_config()
    config = replace(
        config,
        paths=replace(config.paths, export=tmp_path / "phase3",
                      reports=tmp_path / "reports"),
    )
    section = config.section("export")
    section["mode"] = mode
    section["render"] = {
        "episode_pages": episode_pages,
        "per_engineer": {"featured": featured, "current": current,
                         "foundational": foundational, "other": other},
    }
    config = replace(config, sections={**config.sections, "export": section})
    config.paths.reports.mkdir(parents=True, exist_ok=True)

    pipeline = FakePipeline(episode_count=episode_count)
    exporter = Exporter(
        config, pipeline,
        claims=claims_for(episode_count),
        claim_index=claim_index_for(episode_count),
        validation={"status": "pass", "publishable": True,
                    "publishable_blockers": [], "items": []},
        sensitivity={"engineers": []},
        llm_report={"available": False},
        llm_pending={"status": "not_run", "queued": 0},
    )
    manifest = exporter.run()
    read = lambda name: json.loads((config.paths.export / name).read_text())
    return manifest, read


# --------------------------------------------------------------- projection

def test_projection_ships_the_episodes_its_own_plan_names(tmp_path):
    manifest, read = build(tmp_path, mode="projection")
    shipped = {e["episode_id"] for e in read("episodes.json")}
    assert shipped == set(manifest["render_plan"]["episode_page_ids"]) | {"ep-0", "ep-1"}
    assert manifest["projection"]["episodes_included"] == len(shipped)
    assert manifest["projection"]["episodes_omitted"] == 6 - len(shipped)


def test_an_included_episode_is_the_whole_record(tmp_path):
    """A listing-only episode keeps every field, including ones nothing renders."""
    _, read = build(tmp_path, mode="projection")
    listed = next(e for e in read("episodes.json") if e["episode_id"] == "ep-1")
    assert listed["dimensions"], "dimension assessments must not be trimmed away"
    assert listed["participants"]
    assert listed["analytics"]["propagation"] is not None


def test_claims_are_scoped_to_what_a_surface_resolves(tmp_path):
    manifest, read = build(tmp_path, mode="projection")
    shipped = {c["claim_id"] for c in read("claims.json")["claims"]}
    pages = set(manifest["render_plan"]["episode_page_ids"])
    # An episode with a page brings its whole narrative and its dimensions.
    for page in pages:
        index = page.split("-")[1]
        assert f"claim/title-{index}" in shipped
        assert f"claim/dimension-{index}" in shipped
    # An episode that is only listed brings the sentence the listing prints.
    assert "claim/title-1" in shipped
    assert "claim/dimension-1" not in shipped
    # A profile always brings its thesis.
    assert "claim/thesis" in shipped


def test_omissions_are_counted_rather_than_left_unsaid(tmp_path):
    manifest, read = build(tmp_path, mode="projection")
    claims = read("claims.json")
    projection = manifest["projection"]
    assert projection["claims_included"] + projection["claims_omitted"] == 25
    assert claims["count"] + claims["omitted"] == claims["count_all"] == 25
    assert projection["episodes_included"] + projection["episodes_omitted"] == 6
    assert "omission_note" in claims


def test_the_counts_block_still_describes_the_analysis_not_the_package(tmp_path):
    """A smaller package must never make the run look smaller than it was."""
    manifest, _ = build(tmp_path, mode="projection")
    assert manifest["counts"]["episodes"] == 6
    assert manifest["counts"]["claims"] == 25
    assert manifest["projection"]["episodes_included"] < 6


def test_evidence_follows_the_episodes_that_ship(tmp_path):
    manifest, read = build(tmp_path, mode="projection")
    shipped = {e["episode_id"] for e in read("episodes.json")}
    artifacts = {
        row["artifact_id"]
        for name in read("evidence.json")["shards"].values()
        for row in read(name["file"])
    }
    assert artifacts == {f"pr/{e.split('-')[1]}" for e in shipped}
    assert manifest["projection"]["evidence_artifacts_omitted"] == 6 - len(shipped)


def test_the_plan_is_published_so_the_site_does_not_recompute_it(tmp_path):
    manifest, _ = build(tmp_path, mode="projection")
    plan = manifest["render_plan"]
    assert plan["episode_page_ids"] == ["ep-0"]
    assert plan["per_engineer"]["featured"] == 1
    assert plan["episode_pages"] == 2
    assert manifest["export_mode"] == "projection"


def test_the_coverage_page_is_given_the_same_accounting(tmp_path):
    manifest, read = build(tmp_path, mode="projection")
    assert read("coverage.json")["package"] == manifest["projection"]


# --------------------------------------------------------------------- full

def test_full_mode_ships_everything_and_says_so(tmp_path):
    manifest, read = build(tmp_path, mode="full")
    assert manifest["export_mode"] == "full"
    assert len(read("episodes.json")) == 6
    assert read("claims.json")["count"] == 25
    assert manifest["projection"]["episodes_omitted"] == 0
    assert manifest["projection"]["claims_omitted"] == 0
    assert manifest["projection"]["evidence_artifacts_omitted"] == 0
    # The plan is still published: full and projected builds render alike.
    assert manifest["render_plan"]["episode_page_ids"] == ["ep-0"]


def test_an_unknown_mode_is_refused_rather_than_guessed(tmp_path):
    with pytest.raises(ValueError, match="export.mode"):
        build(tmp_path, mode="whatever")


# ------------------------------------------------------------- safety intact

def test_the_safety_scan_still_runs_over_the_projected_package(tmp_path):
    manifest, _ = build(tmp_path, mode="projection")
    assert manifest["safety_scan"]["status"] == "pass"
    assert manifest["publishable"] is True
