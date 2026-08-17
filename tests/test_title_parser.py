"""Conventional-title parser fixtures.

The spec names the cases that must be covered: scoped, unscoped, breaking,
malformed, uppercase, bot, and non-conventional.  Each is asserted on
behaviour, not just on "did not crash".
"""

from __future__ import annotations

import pytest

from impact.normalize.title_parser import parse_title, prefix_distribution


@pytest.mark.parametrize(
    "title,prefix,scope,breaking,status",
    [
        # scoped -- the dominant PostHog shape
        ("feat(experiments): add holdout groups", "feat", "experiments", False, "strict"),
        ("fix(hogql): correct null handling", "fix", "hogql", False, "strict"),
        # unscoped
        ("chore: bump node", "chore", None, False, "strict"),
        ("docs: explain the ingestion path", "docs", None, False, "strict"),
        # breaking, both spellings
        ("feat(api)!: drop v1 endpoints", "feat", "api", True, "strict"),
        ("refactor!: rename the module", "refactor", None, True, "strict"),
        # scope containing punctuation PostHog actually uses
        ("fix(data-warehouse): retry on 429", "fix", "data-warehouse", False, "strict"),
        ("feat(products/replay): scanner paths", "feat", "products/replay", False, "strict"),
    ],
)
def test_conventional_forms(title, prefix, scope, breaking, status):
    parsed = parse_title(title)
    assert parsed.prefix_normalized == prefix
    assert parsed.scope == scope
    assert parsed.breaking is breaking
    assert parsed.parser_status == status
    assert parsed.confidence >= 0.9
    assert parsed.raw_title == title          # raw is always retained


def test_uppercase_type_lowers_confidence_but_still_parses():
    parsed = parse_title("Fix(api): handle nulls")
    assert parsed.prefix_normalized == "fix"
    assert parsed.parser_status == "loose"
    assert parsed.confidence < 0.98
    assert any("lowercase" in n for n in parsed.parser_notes)


def test_alias_type_is_mapped_and_flagged():
    parsed = parse_title("bugfix(ingestion): drop bad events")
    assert parsed.prefix_normalized == "fix"
    assert parsed.parser_status == "alias"
    assert parsed.confidence < 0.9


def test_unknown_type_is_not_invented():
    parsed = parse_title("wibble(core): something")
    assert parsed.prefix_normalized is None
    assert parsed.prefix == "wibble"
    assert parsed.parser_status == "unknown_type"
    assert parsed.confidence < 0.5


@pytest.mark.parametrize(
    "title",
    [
        "just a plain title with no convention",
        "Merge branch 'master' into feature",
        "",
        "WIP",
        "update stuff",
    ],
)
def test_non_conventional(title):
    parsed = parse_title(title)
    assert parsed.prefix_normalized is None
    assert parsed.parser_status == "not_conventional"
    assert parsed.confidence == 0.0


def test_squash_suffix_is_stripped_and_recorded():
    parsed = parse_title("fix(marketing-analytics): read click ids (#83550)")
    assert parsed.prefix_normalized == "fix"
    assert parsed.squash_pr_number == 83550
    assert parsed.subject == "read click ids"
    assert "(#83550)" not in (parsed.subject or "")


def test_merge_queue_artifact_is_not_a_human_title():
    """PostHog's Trunk merge queue opens throwaway PRs. They must never be
    mistaken for authored work."""
    parsed = parse_title("trunk-merge/pr-83501/f36d6fde-c01b-401f-a866-b575ddb40f08")
    assert parsed.title_class == "merge_queue_artifact"
    assert parsed.confidence == 0.0
    assert parsed.parser_status == "not_a_human_title"


def test_bot_dependency_bump_is_classified():
    parsed = parse_title("Bump lodash from 4.17.20 to 4.17.21")
    assert parsed.title_class == "dependency_bump"
    parsed2 = parse_title("chore(deps): bump urllib3 from 2.0.0 to 2.2.0")
    assert parsed2.title_class == "dependency_bump"
    assert parsed2.prefix_normalized == "chore"


def test_breaking_change_footer_in_body():
    parsed = parse_title(
        "feat(api): new pagination", body="BREAKING CHANGE: cursor replaces offset"
    )
    assert parsed.breaking is True
    assert any("BREAKING" in n for n in parsed.parser_notes)


def test_bracket_prefix_does_not_become_a_type():
    parsed = parse_title("[hotfix] fix(api): patch the leak")
    assert parsed.prefix_normalized == "fix"
    assert any("bracket" in n for n in parsed.parser_notes)


def test_git_style_revert_is_recognised_as_a_class():
    parsed = parse_title('Revert "feat(api): new pagination"')
    assert parsed.title_class == "revert"


def test_conventional_revert_parses_as_revert_type():
    """PostHog spells reverts conventionally; the git form is the rare one."""
    parsed = parse_title("revert(experiments): back out holdout groups (#12345)")
    assert parsed.prefix_normalized == "revert"
    assert parsed.scope == "experiments"
    assert parsed.squash_pr_number == 12345


def test_empty_subject_lowers_confidence():
    parsed = parse_title("feat(api):")
    assert parsed.prefix_normalized == "feat"
    assert parsed.subject is None
    assert parsed.confidence < 0.9


def test_prefix_distribution_counts_unparsed_separately():
    dist = prefix_distribution(
        ["feat(a): x", "fix: y", "no convention here", "feat: z"]
    )
    assert dist["feat"] == 2
    assert dist["fix"] == 1
    assert dist["<not_conventional>"] == 1


def test_parser_is_deterministic():
    title = "feat(experiments)!: add holdout groups (#999)"
    assert parse_title(title).as_dict() == parse_title(title).as_dict()
