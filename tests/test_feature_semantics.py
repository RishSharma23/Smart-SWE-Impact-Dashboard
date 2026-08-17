"""Feature semantics: the rules the spec says must never be broken."""

from __future__ import annotations

import datetime as dt

import pytest
import yaml

from impact.config import CONFIG_DIR
from impact.features import blast_radius, change_shape, episodes, regression
from impact.features import review_intervention as RI

UTC = dt.timezone.utc


def _pr(number: int, **kw):
    base = {
        "pr_number": number, "pr_id": f"repo#pr/{number}", "title_prefix": "feat",
        "title_subject": f"subject {number}", "merged_at": "2026-06-01T00:00:00Z",
        "author_actor_id": "github/user/a", "author_login": "a",
    }
    base.update(kw)
    return base


def _file(path, **kw):
    base = {
        "path": path, "change_status": "M", "additions": 10, "deletions": 1,
        "component": "product:x", "platform": "product", "language": "python",
        "owners": ["team-x"], "risk_surfaces": [], "license_area": "MIT",
        "is_test": False, "is_docs": False, "is_generated": False,
        "is_snapshot": False, "is_lockfile": False, "is_vendor": False,
        "is_binary": False, "is_migration": False, "is_config": False,
        "is_binary_asset": False, "is_styling": False, "is_localization": False,
    }
    base.update(kw)
    return base


# ------------------------------------------------------- change shape ----


def test_title_claim_mismatch_is_recorded_not_judged():
    """A 'feat' that only edits docs is a disagreement worth surfacing."""
    row = change_shape.compute(
        _pr(1, title_prefix="feat"),
        [_file("README.md", is_docs=True, component="infra:docs", platform="infrastructure")],
    )
    assert row["title_claim_corroborated"] is False
    assert "feat" in row["title_claim_note"]


def test_chore_makes_no_checkable_claim():
    row = change_shape.compute(_pr(2, title_prefix="chore"), [_file("a.py")])
    assert row["title_claim_corroborated"] is None


def test_test_to_production_linkage_is_by_stem_not_ratio():
    row = change_shape.compute(
        _pr(3),
        [
            _file("products/x/backend/service.py"),
            _file("products/x/backend/test_service.py", is_test=True),
            _file("products/x/backend/other.py"),
        ],
    )
    assert row["test_to_production_link_count"] == 1
    link = row["test_to_production_links"][0]
    assert link["production_path"] == "products/x/backend/service.py"


def test_component_entropy_is_zero_for_a_single_component():
    row = change_shape.compute(_pr(4), [_file("a.py"), _file("b.py")])
    assert row["component_entropy"] == 0.0
    assert row["dominant_component_share"] == 1.0


def test_component_entropy_rises_with_spread():
    row = change_shape.compute(
        _pr(5),
        [_file("a.py", component="product:x"), _file("b.py", component="product:y")],
    )
    assert row["component_entropy"] == pytest.approx(1.0)


# ------------------------------------------------------- blast radius ----


def test_unparsed_language_yields_unknown_not_local():
    """A Rust-only change must never be reported as small just because we
    cannot see its edges."""
    row = blast_radius.compute(
        _pr(6),
        [_file("rust/capture/src/main.rs", language="rust", component="rust:capture")],
        nodes={}, reverse_adjacency={},
    )
    assert row["reachability_band"] == "unknown"
    assert row["reachability_is_uncertain"] is True
    assert any("rust" in u for u in row["reachability_uncertainty"])


def test_shared_library_touch_needs_corroboration_to_be_platform_wide():
    """A path glob alone is not breadth.

    Requiring only "touches something under frontend/src/lib" put 41% of PRs in
    the platform_wide band on the real dataset, which is not a summary. The
    band now needs the path signal AND observed spread (a hub node, or two or
    more downstream products).
    """
    changed = _file("frontend/src/lib/utils.ts", risk_surfaces=["shared_library"],
                    component="frontend:shared-lib", language="typescript")

    uncorroborated = blast_radius.compute(
        _pr(7), [changed],
        nodes={"frontend/src/lib/utils.ts": {"fan_in": 0, "is_hub": False,
                                             "component": "frontend:shared-lib"}},
        reverse_adjacency={},
    )
    assert uncorroborated["reachability_band"] != "platform_wide"

    corroborated = blast_radius.compute(
        _pr(7), [changed],
        nodes={"frontend/src/lib/utils.ts": {"fan_in": 400, "is_hub": True,
                                             "component": "frontend:shared-lib"}},
        reverse_adjacency={},
    )
    assert corroborated["reachability_band"] == "platform_wide"
    assert corroborated["hub_files_touched"] == 1


def test_two_products_is_cross_product():
    row = blast_radius.compute(
        _pr(8),
        [_file("products/a/x.py", component="product:a"),
         _file("products/b/y.py", component="product:b")],
        nodes={"products/a/x.py": {"component": "product:a"},
               "products/b/y.py": {"component": "product:b"}},
        reverse_adjacency={},
    )
    assert row["reachability_band"] == "cross_product"
    assert row["crosses_product_boundary"] is True


def test_ownership_crossing_is_reported_separately_from_components():
    row = blast_radius.compute(
        _pr(9),
        [_file("a.py", owners=["team-x"]), _file("b.py", owners=["team-y"])],
        nodes={}, reverse_adjacency={},
    )
    assert row["distinct_owners"] == 2
    assert row["crosses_ownership_boundary"] is True


# ----------------------------------------------------------- episodes ----


def test_closing_reference_is_a_strong_edge():
    refs = [{
        "source_kind": "pull_request", "source_number": 10,
        "reference_kind": "issue_or_pr", "reference_value": "500",
        "reference_subtype": "github_closing_reference", "source_field": "github_metadata",
        "evidence": "fixes the thing",
    }]
    edges = episodes.build_edges(prs={10: _pr(10)}, references=refs, feature_flags=[])
    assert edges[0]["edge_type"] == "closes_issue"
    assert edges[0]["strength"] == "strong"


def test_weak_flag_edges_never_form_an_episode():
    """A shared feature flag is worth recording but is far too promiscuous to
    define a unit of work."""
    flags = [{"pr_number": n, "flag_key": "some-flag"} for n in (20, 21, 22)]
    prs = {n: _pr(n) for n in (20, 21, 22)}
    edges = episodes.build_edges(prs=prs, references=[], feature_flags=flags)
    assert all(e["strength"] == "weak" for e in edges)
    assert episodes.build_episodes(edges) == []


def test_medium_edges_group_into_an_episode():
    refs = [
        {"source_kind": "pull_request", "source_number": 31,
         "reference_kind": "issue_or_pr", "reference_value": "30",
         "reference_subtype": "mention", "source_field": "body", "evidence": "follow up to #30"},
        {"source_kind": "pull_request", "source_number": 31,
         "reference_kind": "edge_phrase", "reference_value": "follow_up",
         "reference_subtype": None, "source_field": "body", "evidence": "follow up"},
    ]
    prs = {30: _pr(30), 31: _pr(31)}
    edges = episodes.build_edges(prs=prs, references=refs, feature_flags=[])
    assert any(e["edge_type"] == "follow_up" for e in edges)
    eps = episodes.build_episodes(edges)
    assert len(eps) == 1 and eps[0]["pr_count"] == 2


# --------------------------------------------------------- regression ----


def _regression(prs, files, **kw):
    return regression.compute(
        prs=prs, files_by_pr=files, edges=kw.get("edges", []),
        flags_by_pr=kw.get("flags", {}), issues_by_pr=kw.get("issues", {}),
        window_end=kw.get("window_end", dt.datetime(2026, 12, 1, tzinfo=UTC)),
    )


def test_shared_files_alone_is_only_a_proximate_candidate():
    """The spec's hard rule: never label a PR a regression solely because a
    later fix touched the same files."""
    prs = {
        1: _pr(1, merged_at="2026-06-01T00:00:00Z", title_prefix="feat"),
        2: _pr(2, merged_at="2026-06-03T00:00:00Z", title_prefix="fix"),
    }
    files = {1: [_file("a.py")], 2: [_file("a.py")]}
    rows = {r["pr_number"]: r for r in _regression(prs, files)}
    assert rows[1]["regression_evidence_tier"] == "proximate"
    assert rows[1]["requires_human_confirmation"] is True
    assert rows[1]["was_reverted"] is False


def test_shared_issue_upgrades_to_linked():
    prs = {
        1: _pr(1, merged_at="2026-06-01T00:00:00Z", title_prefix="feat"),
        2: _pr(2, merged_at="2026-06-03T00:00:00Z", title_prefix="fix"),
    }
    files = {1: [_file("a.py")], 2: [_file("a.py")]}
    rows = {r["pr_number"]: r
            for r in _regression(prs, files, issues={1: {99}, 2: {99}})}
    assert rows[1]["regression_evidence_tier"] == "linked"
    assert rows[1]["requires_human_confirmation"] is False


def test_conventional_revert_is_detected_as_explicit():
    prs = {
        1: _pr(1, merged_at="2026-06-01T00:00:00Z", title_prefix="feat",
               title_subject="add holdout groups"),
        2: _pr(2, merged_at="2026-06-05T00:00:00Z", title_prefix="revert",
               title_subject="add holdout groups"),
    }
    rows = {r["pr_number"]: r for r in _regression(prs, {1: [_file("a.py")], 2: []})}
    assert rows[1]["regression_evidence_tier"] == "explicit"
    assert rows[1]["was_reverted"] is True


def test_test_only_overlap_does_not_create_a_candidate():
    prs = {
        1: _pr(1, merged_at="2026-06-01T00:00:00Z", title_prefix="feat"),
        2: _pr(2, merged_at="2026-06-02T00:00:00Z", title_prefix="fix"),
    }
    files = {1: [_file("t_a.py", is_test=True)], 2: [_file("t_a.py", is_test=True)]}
    rows = {r["pr_number"]: r for r in _regression(prs, files)}
    assert rows[1]["regression_evidence_tier"] == "none"


def test_survival_is_null_when_the_window_ends_too_soon():
    """NULL means 'not knowable yet', never 0."""
    prs = {1: _pr(1, merged_at="2026-11-20T00:00:00Z")}
    files = {1: [_file("new.py", change_status="A")]}
    rows = _regression(prs, files, window_end=dt.datetime(2026, 12, 1, tzinfo=UTC))
    assert rows[0]["survival_30d"] is None
    assert "insufficient follow-up history" in rows[0]["survival_30d_reason"]


def test_survival_is_measured_when_history_allows():
    prs = {1: _pr(1, merged_at="2026-06-01T00:00:00Z")}
    files = {1: [_file("new.py", change_status="A")]}
    rows = _regression(prs, files, window_end=dt.datetime(2026, 12, 1, tzinfo=UTC))
    assert rows[0]["survival_30d"] == 1.0
    assert rows[0]["survival_30d_reason"] is None


# -------------------------------------------------- review intervention ----


@pytest.fixture(scope="module")
def classifier() -> RI.ReviewClassifier:
    cfg = yaml.safe_load((CONFIG_DIR / "feature_versions.yaml").read_text())
    return RI.ReviewClassifier(cfg["parameters"]["review_intervention"])


@pytest.mark.parametrize(
    "body,expected",
    [
        ("LGTM", "acknowledgement"),
        ("👍", "acknowledgement"),
        ("nit: rename this variable", "nit"),
        ("ok", "short"),
        ("", "empty"),
        ("This will drop rows when the migration runs on a table with a null "
         "column, because the backfill assumes non-null. Should we gate it?",
         "substantive"),
    ],
)
def test_substance_classification(classifier, body, expected):
    result = classifier.classify_comment(body, author_login="human", author_is_bot=False)
    assert result["substance_class"] == expected


def test_bot_comments_are_never_substantive(classifier):
    result = classifier.classify_comment(
        "A very long and detailed automated analysis " * 10,
        author_login="greptile-apps[bot]", author_is_bot=True,
    )
    assert result["substance_class"] == "bot"
    assert result["is_substantive"] is False


def test_safety_vocabulary_is_categorised(classifier):
    hits = classifier.safety_terms(
        "this migration could cause data loss and there is an injection risk"
    )
    assert "migration" in hits
    assert "data_loss" in hits
    assert "security" in hits


def test_self_comments_are_not_interventions(classifier):
    rows = RI.compute_candidates(
        prs={1: {"pr_number": 1, "pr_id": "x", "author_actor_id": "github/user/a",
                 "author_login": "a"}},
        review_comments=[{
            "pr_number": 1, "comment_id": "c1", "thread_id": "t1",
            "author_actor_id": "github/user/a", "author_login": "a",
            "body_text": "I should refactor this later because the current shape "
                         "makes the retry path hard to follow.",
            "created_at": "2026-06-01T00:00:00Z", "position_in_thread": 0,
        }],
        threads={"t1": {"path": "a.py", "is_resolved": False}},
        commits_by_pr={}, files_by_pr={}, actors={}, classifier=classifier,
    )
    assert rows[0]["is_self_comment"] is True
    assert rows[0]["is_intervention_candidate"] is False


def test_comment_body_is_retained_for_audit(classifier):
    body = "Consider using a bulk update here; the loop issues one query per row."
    rows = RI.compute_candidates(
        prs={1: {"pr_number": 1, "pr_id": "x", "author_actor_id": "github/user/a",
                 "author_login": "a"}},
        review_comments=[{
            "pr_number": 1, "comment_id": "c1", "thread_id": "t1",
            "author_actor_id": "github/user/b", "author_login": "b",
            "body_text": body, "created_at": "2026-06-01T00:00:00Z",
            "position_in_thread": 0,
        }],
        threads={"t1": {"path": "a.py", "is_resolved": True}},
        commits_by_pr={}, files_by_pr={}, actors={}, classifier=classifier,
    )
    assert rows[0]["body_text"] == body
    assert rows[0]["is_intervention_candidate"] is True
