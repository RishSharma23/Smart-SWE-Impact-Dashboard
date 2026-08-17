"""Window-boundary, path-glob and determinism tests."""

from __future__ import annotations

import datetime as dt

import pytest
import yaml

from impact.config import CONFIG_DIR, parse_ts, resolve_window
from impact.hashing import canonical_json, content_hash, pr_id
from impact.normalize.paths import PathClassifier, glob_match

UTC = dt.timezone.utc


# --------------------------------------------------------------- window ----


@pytest.fixture
def window_cfg() -> dict:
    return yaml.safe_load((CONFIG_DIR / "window.yaml").read_text())


def test_window_anchors_on_utc_midnight(window_cfg):
    """Anchoring on 'now minus 90*24h' would make two runs on the same day
    disagree; anchoring on UTC midnight makes the window stable."""
    now = dt.datetime(2026, 8, 17, 3, 49, 58, tzinfo=UTC)
    w = resolve_window(window_cfg, now=now)
    assert w.start == dt.datetime(2026, 5, 19, 0, 0, 0, tzinfo=UTC)
    assert w.end == now
    assert (w.end.replace(hour=0, minute=0, second=0, microsecond=0) - w.start).days == 90


def test_window_is_stable_across_the_same_day(window_cfg):
    morning = resolve_window(window_cfg, now=dt.datetime(2026, 8, 17, 0, 5, tzinfo=UTC))
    evening = resolve_window(window_cfg, now=dt.datetime(2026, 8, 17, 23, 55, tzinfo=UTC))
    assert morning.start == evening.start


def test_window_membership_is_half_open(window_cfg):
    w = resolve_window(
        window_cfg,
        start_override="2026-05-19T00:00:00Z",
        end_override="2026-08-17T00:00:00Z",
    )
    assert w.contains(parse_ts("2026-05-19T00:00:00Z")) is True    # start inclusive
    assert w.contains(parse_ts("2026-08-16T23:59:59Z")) is True
    assert w.contains(parse_ts("2026-08-17T00:00:00Z")) is False   # end exclusive
    assert w.contains(parse_ts("2026-05-18T23:59:59Z")) is False
    assert w.contains(None) is False


def test_naive_timestamps_are_treated_as_utc():
    assert parse_ts("2026-05-19T00:00:00") == parse_ts("2026-05-19T00:00:00Z")


def test_non_utc_offsets_are_converted_not_truncated():
    """A commit at 2026-05-19T00:30+02:00 is 2026-05-18T22:30Z -- outside a
    window starting at 2026-05-19Z. Getting this wrong shifts a whole day."""
    parsed = parse_ts("2026-05-19T00:30:00+02:00")
    assert parsed == dt.datetime(2026, 5, 18, 22, 30, tzinfo=UTC)


def test_merged_and_created_can_disagree_about_the_window(window_cfg):
    w = resolve_window(
        window_cfg,
        start_override="2026-05-19T00:00:00Z",
        end_override="2026-08-17T00:00:00Z",
    )
    created = parse_ts("2026-05-01T12:00:00Z")   # before the window
    merged = parse_ts("2026-05-20T12:00:00Z")    # inside it
    assert w.contains(created) is False
    assert w.contains(merged) is True


def test_window_rejects_inverted_range(window_cfg):
    with pytest.raises(ValueError):
        resolve_window(
            window_cfg,
            start_override="2026-08-17T00:00:00Z",
            end_override="2026-05-19T00:00:00Z",
        )


# ----------------------------------------------------------------- globs ----


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        # '*' must not cross a path separator
        ("posthog/api/**", "posthog/api/x/y.py", True),
        ("posthog/api/**", "products/x/posthog/api/y.py", False),
        ("products/*/**", "products/experiments/backend/models.py", True),
        ("products/*/**", "products/experiments", True),
        ("products/*", "products/experiments", True),
        ("products/*", "products/experiments/backend/x.py", False),
        # '**/' matches zero or more directories
        ("**/*.md", "README.md", True),
        ("**/*.md", "docs/internal/x.md", True),
        ("**/migrations/**", "posthog/migrations/0001_init.py", True),
        ("**/migrations/**", "products/x/backend/migrations/0002.py", True),
        ("**/*.test.ts", "frontend/src/a/b.test.ts", True),
        ("**/*.test.ts", "frontend/src/a/b.ts", False),
        ("Dockerfile*", "Dockerfile.node", True),
        ("Dockerfile*", "docker/Dockerfile.node", False),
    ],
)
def test_glob_semantics(pattern, path, expected):
    assert glob_match(pattern, path) is expected


@pytest.fixture(scope="module")
def classifier() -> PathClassifier:
    return PathClassifier(yaml.safe_load((CONFIG_DIR / "generated_files.yaml").read_text()))


@pytest.mark.parametrize(
    "path,expected_categories",
    [
        ("pnpm-lock.yaml", {"lockfile"}),
        ("products/x/backend/test/__snapshots__/test_a.ambr", {"snapshot", "test"}),
        ("posthog/migrations/0500_add_col.py", {"migration"}),
        ("frontend/src/scenes/Foo.test.tsx", {"test"}),
        ("docs/internal/monorepo-layout.md", {"docs"}),
        ("frontend/src/styles/main.scss", {"styling"}),
        (".github/workflows/ci.yml", {"ci", "config"}),
        ("frontend/public/logo.png", {"binary_asset"}),
    ],
)
def test_path_categories(classifier, path, expected_categories):
    result = classifier.classify(path)
    assert expected_categories.issubset(set(result.categories)), result.categories


def test_a_path_can_carry_several_categories(classifier):
    """A generated test snapshot is genuinely all three; collapsing to one
    would lose information."""
    result = classifier.classify("products/x/backend/tests/__snapshots__/a.ambr")
    assert "snapshot" in result.categories
    assert "test" in result.categories


def test_risk_surfaces_are_detected(classifier):
    assert "migration" in classifier.classify("posthog/clickhouse/migrations/0001.py").risk_surfaces
    assert "public_api" in classifier.classify("posthog/api/feature_flag.py").risk_surfaces
    assert "shared_library" in classifier.classify("frontend/src/lib/utils.ts").risk_surfaces


def test_language_unknown_is_a_real_value(classifier):
    assert classifier.classify("some/file.zzz").language == "unknown"
    assert classifier.classify("posthog/api/x.py").language == "python"


# --------------------------------------------------------- determinism ----


def test_content_hash_ignores_row_order():
    a = [{"id": 1, "v": "x"}, {"id": 2, "v": "y"}]
    b = list(reversed(a))
    assert content_hash(a) == content_hash(b)


def test_content_hash_excludes_operational_columns():
    a = [{"id": 1, "computed_at": "2026-01-01T00:00:00Z"}]
    b = [{"id": 1, "computed_at": "2026-06-06T12:00:00Z"}]
    assert content_hash(a, exclude=["computed_at"]) == content_hash(b, exclude=["computed_at"])
    assert content_hash(a) != content_hash(b)


def test_content_hash_detects_a_real_change():
    a = [{"id": 1, "v": "x"}]
    b = [{"id": 1, "v": "z"}]
    assert content_hash(a) != content_hash(b)


def test_float_noise_does_not_change_the_hash():
    assert content_hash([{"v": 0.1 + 0.2}]) == content_hash([{"v": 0.30000000000000004}])


def test_canonical_json_is_stable_under_key_order():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_ids_are_repository_qualified_and_stable():
    assert pr_id(123) == "github.com/PostHog/posthog#pr/123"
    assert pr_id(123) == pr_id(123)
