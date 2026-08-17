"""Identity resolution fixtures: co-authors, bots, duplicate emails, ambiguity."""

from __future__ import annotations

import pytest
import yaml

from impact.config import CONFIG_DIR
from impact.normalize.actors import ActorResolver, UnionFind


@pytest.fixture(scope="module")
def bots_cfg() -> dict:
    return yaml.safe_load((CONFIG_DIR / "bots.yaml").read_text())


def _rows(resolver: ActorResolver) -> dict[str, dict]:
    rows, _ = resolver.finalize()
    return {r["actor_id"]: r for r in rows}


def test_github_noreply_email_links_to_login(bots_cfg):
    """12345+login@users.noreply.github.com identifies the same person as
    the GitHub login, and must not create a second identity."""
    r = ActorResolver(bots_cfg)
    r.add_github_actor({"login": "octocat", "__typename": "User"}, source="pr")
    r.add_git_identity("The Octocat", "583231+octocat@users.noreply.github.com",
                       source="git")
    rows = _rows(r)
    assert len(rows) == 1
    actor = next(iter(rows.values()))
    assert actor["login"] == "octocat"
    assert actor["account_type"] == "user"


def test_duplicate_emails_cluster_one_person(bots_cfg):
    r = ActorResolver(bots_cfg)
    r.add_git_identity("Ana Dev", "ana@posthog.com", source="git_author")
    r.add_git_identity("Ana Dev", "ana@posthog.com", source="git_committer")
    rows = _rows(r)
    assert len(rows) == 1
    assert rows[next(iter(rows))]["email_count"] == 1


def test_shared_email_across_logins_is_ambiguous_not_merged(bots_cfg):
    """A role address used by two accounts must NOT silently merge two people."""
    r = ActorResolver(bots_cfg)
    r.add_github_actor({"login": "alice", "__typename": "User"}, source="pr")
    r.add_github_actor({"login": "bob", "__typename": "User"}, source="pr")
    r.add_git_identity("Alice", "eng@posthog.com", source="git")
    # Force both logins to carry the same address.
    rows_before, _ = r.finalize()
    ids = {row["login"]: row["actor_id"] for row in rows_before}
    r.records[ids["alice"]].emails.add("eng@posthog.com")
    r.records[ids["bob"]].emails.add("eng@posthog.com")

    rows = _rows(r)
    alice = rows[ids["alice"]]
    bob = rows[ids["bob"]]
    assert alice["identity_cluster_id"] != bob["identity_cluster_id"]
    assert alice["ambiguity_status"] == "ambiguous"
    assert any("shared_email" in reason for reason in alice["ambiguity_reasons"])


def test_display_name_alone_never_merges(bots_cfg):
    """Two different people can share a display name."""
    r = ActorResolver(bots_cfg)
    r.add_git_identity("Michael", "michael.a@posthog.com", source="git")
    r.add_git_identity("Michael", "michael.b@posthog.com", source="git")
    rows = _rows(r)
    assert len(rows) == 2
    clusters = {row["identity_cluster_id"] for row in rows.values()}
    assert len(clusters) == 2


@pytest.mark.parametrize(
    "login,typename,expect_bot",
    [
        ("dependabot[bot]", "Bot", True),
        ("posthog[bot]", "Bot", True),
        ("tests-posthog[bot]", "Bot", True),
        ("trunk-io", "Bot", True),          # caught by __typename, not by name
        ("octocat", "User", False),
        ("robert", "User", False),          # 'rob' must not trigger 'bot'
    ],
)
def test_bot_classification(bots_cfg, login, typename, expect_bot):
    r = ActorResolver(bots_cfg)
    r.add_github_actor({"login": login, "__typename": typename}, source="pr")
    rows = _rows(r)
    actor = next(iter(rows.values()))
    assert actor["is_bot"] is expect_bot
    if expect_bot:
        assert actor["bot_reasons"], "a bot verdict must carry its reasons"
        assert actor["bot_probability"] >= 0.9


def test_bot_probability_is_graded_not_binary(bots_cfg):
    """A name-shaped guess must not reach certainty."""
    r = ActorResolver(bots_cfg)
    r.add_github_actor({"login": "release-automation", "__typename": "User"},
                       source="pr")
    actor = next(iter(_rows(r).values()))
    assert 0 < actor["bot_probability"] < 0.9
    assert actor["is_bot"] is False
    assert "bot_classification_uncertain" in actor["ambiguity_reasons"]


def test_ai_co_author_is_not_a_bot(bots_cfg):
    """A human PR written with Claude is human-authored work, tracked
    separately from bot authorship."""
    r = ActorResolver(bots_cfg)
    r.add_git_identity("Claude", "noreply@anthropic.com", source="git_co_author")
    actor = next(iter(_rows(r).values()))
    assert actor["is_ai_assistant_identity"] is True
    assert actor["is_bot"] is False


def test_co_authors_become_distinct_actors(bots_cfg):
    r = ActorResolver(bots_cfg)
    r.add_git_identity("Author One", "one@posthog.com", source="git_author")
    r.add_git_identity("Helper Two", "two@posthog.com", source="git_co_author")
    rows = _rows(r)
    assert len(rows) == 2
    sources = {tuple(row["sources"]) for row in rows.values()}
    assert ("git_author",) in sources
    assert ("git_co_author",) in sources


def test_git_only_identity_is_flagged_ambiguous(bots_cfg):
    r = ActorResolver(bots_cfg)
    r.add_git_identity("Nobody", "nobody@example.com", source="git")
    actor = next(iter(_rows(r).values()))
    assert actor["login"] is None
    assert actor["account_type"] == "git_identity"
    assert actor["ambiguity_status"] == "ambiguous"


def test_union_find_root_is_deterministic():
    """Cluster ids must be stable across reruns, regardless of insert order."""
    a, b = UnionFind(), UnionFind()
    a.union("z", "a")
    a.union("m", "z")
    b.union("m", "z")
    b.union("a", "z")
    assert a.find("z") == b.find("z") == "a"


def test_resolution_is_deterministic(bots_cfg):
    def build():
        r = ActorResolver(bots_cfg)
        r.add_github_actor({"login": "octocat", "__typename": "User"}, source="pr")
        r.add_git_identity("Octo", "583231+octocat@users.noreply.github.com", source="git")
        r.add_git_identity("Other", "other@posthog.com", source="git")
        rows, summary = r.finalize()
        return rows, summary

    assert build() == build()
