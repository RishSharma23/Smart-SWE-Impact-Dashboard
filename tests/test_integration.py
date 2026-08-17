"""Opt-in integration tests.

Deselected by default (``addopts = -m 'not integration'``). Run with:

    make test-integration        # or: pytest -m integration

Two groups:

* **artifact tests** need a completed run (``artifacts/*.parquet``). They assert
  the Phase 2 contract holds against the *real* data, not against fixtures.
* **live tests** additionally need the network and a GitHub token.

Both skip cleanly when their prerequisite is absent, so this file is safe to run
anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from impact.config import PROJECT_ROOT, load_settings, parse_ts
from impact.store import read_json, read_table

pytestmark = pytest.mark.integration

ARTIFACTS = PROJECT_ROOT / "artifacts"


def _need(table: str) -> list[dict]:
    path = ARTIFACTS / f"{table}.parquet"
    if not path.exists():
        pytest.skip(f"{path.name} not present; run `make all` first")
    return read_table(path)


@pytest.fixture(scope="module")
def manifest() -> dict:
    path = ARTIFACTS / "run_manifest.json"
    if not path.exists():
        pytest.skip("run_manifest.json not present; run `make export` first")
    return json.loads(path.read_text())


# ------------------------------------------------------- manifest ----


def test_manifest_pins_the_source_and_window(manifest):
    assert len(manifest["source"]["analyzed_head_sha"]) == 40
    assert manifest["window"]["lookback_days"] == 90
    assert manifest["source"]["repository_qualifier"] == "github.com/PostHog/posthog"


def test_manifest_row_counts_match_the_actual_files(manifest):
    for name, meta in manifest["tables"].items():
        rows = read_table(ARTIFACTS / f"{name}.parquet")
        assert len(rows) == meta["row_count"], name


def test_every_exported_table_has_a_schema(manifest):
    for name in manifest["tables"]:
        assert (PROJECT_ROOT / "schemas" / f"{name}.schema.json").exists(), name


def test_content_hashes_are_recorded(manifest):
    for name, meta in manifest["tables"].items():
        assert meta["content_sha256"], name
        assert len(meta["content_sha256"]) == 64, name


# ------------------------------------------------- contract on real data ----


def test_linear_history_assumption_still_holds(manifest):
    """pr_files is only complete while one PR == one commit."""
    assert manifest["source"]["linear_history"] is True


def test_every_pr_has_an_eligibility_decision():
    prs = _need("pull_requests")
    for pr in prs:
        if not pr["ranking_eligible"]:
            assert pr["ranking_ineligible_reason"], pr["pr_number"]


def test_eligible_prs_are_merged_inside_the_window():
    settings = load_settings()
    prs = _need("pull_requests")
    eligible = [p for p in prs if p["ranking_eligible"]]
    assert eligible, "no eligible PRs in the dataset"
    for pr in eligible:
        merged = parse_ts(pr["merged_at"])
        assert merged is not None
        assert pr["state"] == "MERGED"
        assert not pr["is_merge_queue_artifact"]


def test_merge_queue_artifacts_are_excluded_from_ranking():
    prs = _need("pull_requests")
    artifacts = [p for p in prs if p["is_merge_queue_artifact"]]
    if not artifacts:
        pytest.skip("no merge-queue artifacts in this window")
    assert all(not p["ranking_eligible"] for p in artifacts)


def test_binary_files_carry_no_line_counts():
    files = _need("pr_files")
    for row in files:
        if row["is_binary"]:
            assert row["additions"] is None and row["deletions"] is None
            assert row["line_counts_unavailable_reason"]


def test_no_derived_table_contains_a_score_column():
    """Phase 1 emits evidence. A column named like a score is a contract break."""
    banned = ("impact_score", "score", "rank", "weight", "rating", "points")
    for table in (
        "pr_change_shape", "pr_blast_radius", "pr_regression_candidates",
        "review_intervention_candidates", "reviewer_intervention_rollup",
    ):
        rows = _need(table)
        if not rows:
            continue
        for column in rows[0]:
            lowered = column.lower()
            # similarity_score is git's rename detection, not an impact score.
            if lowered == "similarity_score":
                continue
            assert not any(
                lowered == b or lowered.endswith("_" + b) for b in banned
            ), f"{table}.{column} looks like a score"


def test_regression_proximate_candidates_are_flagged_for_review():
    rows = _need("pr_regression_candidates")
    proximate = [r for r in rows if r["regression_evidence_tier"] == "proximate"]
    if not proximate:
        pytest.skip("no proximate candidates")
    assert all(r["requires_human_confirmation"] for r in proximate)


def test_survival_nulls_always_carry_a_reason():
    rows = _need("pr_regression_candidates")
    for row in rows:
        if row["survival_30d"] is None:
            assert row["survival_30d_reason"], row["pr_number"]


def test_actor_clusters_are_internally_consistent():
    actors = _need("actors")
    by_id = {a["actor_id"]: a for a in actors}
    for actor in actors:
        for member in actor["identity_cluster_members"]:
            assert member in by_id
            assert by_id[member]["identity_cluster_id"] == actor["identity_cluster_id"]


def test_pr_files_reference_existing_prs():
    prs = {p["pr_number"] for p in _need("pull_requests")}
    for row in _need("pr_files"):
        assert row["pr_number"] in prs


def test_unknown_reachability_band_always_has_a_reason():
    rows = _need("pr_blast_radius")
    for row in rows:
        if row["reachability_band"] == "unknown":
            assert row["reachability_uncertainty"], row["pr_number"]


def test_web_artifacts_record_their_extraction_status():
    rows = _need("web_artifacts")
    if not rows:
        pytest.skip("no web artifacts")
    for row in rows:
        assert row["extraction_status"]
        if row["extraction_status"] == "ok":
            assert row["content_sha256"] and row["retrieved_at"]
        else:
            assert row["error"] is not None or row["extraction_status"].startswith("skipped")


def test_quality_report_has_no_failing_gate():
    report = read_json(ARTIFACTS / "quality_report.json", None)
    if report is None:
        pytest.skip("quality_report.json not present; run `make validate`")
    failing = [g["gate"] for g in report["gates"] if g["status"] == "fail"]
    assert not failing, f"failing quality gates: {failing}"


# ------------------------------------------------------------ live ----


def test_live_github_token_is_read_only_capable():
    """Confirms a token resolves and the repository is reachable."""
    pytest.importorskip("requests")
    from impact.config import github_token
    from impact.ingest.github_client import GitHubClient

    try:
        github_token()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    settings = load_settings()
    client = GitHubClient.build(settings, workers=1)
    payload = client.graphql(
        "query($owner:String!,$name:String!){ rateLimit{remaining} "
        "repository(owner:$owner,name:$name){ nameWithOwner } }",
        {"owner": settings.owner, "name": settings.name},
        entity="test", shard="live", query_name="live_check",
    )
    assert payload["data"]["repository"]["nameWithOwner"] == "PostHog/posthog"


def test_live_clone_matches_the_recorded_head(manifest):
    from impact.ingest.git_source import run_git

    settings = load_settings()
    if not (settings.clone_path / ".git").exists():
        pytest.skip("clone not present")
    head = run_git(settings.clone_path, ["rev-parse", "HEAD"]).strip()
    assert head == manifest["source"]["analyzed_head_sha"], (
        "the clone has moved since the run; re-run `make all` or the artifacts "
        "no longer describe this checkout"
    )
