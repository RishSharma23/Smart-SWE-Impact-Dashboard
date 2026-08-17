"""Change-shape and semantic-proxy features.

Everything here is a *descriptor* of what a change touched.  Nothing is summed
into a score, and counts (files, lines) are carried only as evidence with the
uncertainty attached.

The one non-obvious feature is ``title_claim_corroborated``: the conventional
prefix says what the author claims the change is, and the paths say what it
actually touched.  Comparing them is cheap and catches the case the spec warns
about -- a title that is present but not truthful.  A mismatch is recorded as a
disagreement, never as "the title is wrong"; a ``fix`` that only edits tests is
a legitimate thing that simply deserves to be visible.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from ..normalize.components import component_entropy
from ..versions import feature_version

# Path stems that pair a test with the production file it exercises.
TEST_STEM_PATTERNS = (
    re.compile(r"^test_(?P<stem>.+)$"),
    re.compile(r"^(?P<stem>.+)_test$"),
    re.compile(r"^(?P<stem>.+)\.test$"),
    re.compile(r"^(?P<stem>.+)\.spec$"),
)

CHANGE_CATEGORIES = (
    "product_code", "platform_code", "tests", "docs", "config", "migration",
    "generated", "dependency", "snapshot", "styling", "localization",
    "infrastructure", "binary_asset",
)


def _stem(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    for suffix in (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", ".test.js"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name.rsplit(".", 1)[0] if "." in name else name


def _production_stem(path: str) -> str | None:
    stem = _stem(path)
    for pattern in TEST_STEM_PATTERNS:
        match = pattern.match(stem)
        if match:
            return match.group("stem")
    return stem


def categorise_file(row: Mapping[str, Any]) -> str:
    """One primary category per file, most specific first.

    A file can carry many labels (a generated snapshot inside a test dir); the
    primary category picks the one that best describes *why the file changed*.
    """
    if row.get("is_lockfile"):
        return "dependency"
    if row.get("is_snapshot"):
        return "snapshot"
    if row.get("is_generated"):
        return "generated"
    if row.get("is_migration"):
        return "migration"
    if row.get("is_test"):
        return "tests"
    if row.get("is_docs"):
        return "docs"
    if row.get("is_localization"):
        return "localization"
    if row.get("is_styling"):
        return "styling"
    if row.get("is_binary_asset"):
        return "binary_asset"
    if row.get("platform") == "infrastructure":
        return "infrastructure"
    if row.get("is_config"):
        return "config"
    if row.get("platform") == "product":
        return "product_code"
    if row.get("platform") == "platform":
        return "platform_code"
    return "unclassified"


# Which title prefixes are corroborated by which primary categories.
PREFIX_EXPECTATION: dict[str, set[str]] = {
    "feat": {"product_code", "platform_code"},
    "fix": {"product_code", "platform_code"},
    "perf": {"product_code", "platform_code"},
    "refactor": {"product_code", "platform_code"},
    "docs": {"docs"},
    "test": {"tests", "snapshot"},
    "ci": {"infrastructure", "config"},
    "build": {"config", "dependency", "infrastructure"},
    "chore": set(),   # chore is a catch-all; nothing to corroborate
    "style": {"styling", "product_code", "platform_code"},
    "revert": set(),
}


def compute(
    pr: Mapping[str, Any], files: list[Mapping[str, Any]], *, entropy_base: float = 2.0
) -> dict[str, Any]:
    total = len(files)
    categories: dict[str, int] = {}
    for row in files:
        key = categorise_file(row)
        categories[key] = categories.get(key, 0) + 1

    components = [str(f.get("component") or "unknown") for f in files]
    component_counts: dict[str, int] = {}
    for component in components:
        component_counts[component] = component_counts.get(component, 0) + 1
    dominant = max(component_counts.items(), key=lambda kv: (kv[1], kv[0]))[0] if component_counts else None
    dominant_share = round(component_counts.get(dominant or "", 0) / total, 4) if total else None

    statuses: dict[str, int] = {}
    for row in files:
        key = str(row.get("change_status") or "?")
        statuses[key] = statuses.get(key, 0) + 1

    code_files = [
        f for f in files
        if not (f.get("is_generated") or f.get("is_snapshot") or f.get("is_lockfile")
                or f.get("is_vendor") or f.get("is_binary_asset"))
    ]
    generated_files = total - len(code_files)

    # Test-to-production linkage by stem, not by a files-changed ratio.
    test_paths = [str(f["path"]) for f in files if f.get("is_test")]
    prod_paths = [
        str(f["path"]) for f in files
        if not f.get("is_test") and not f.get("is_docs") and not f.get("is_snapshot")
    ]
    prod_stems = {_stem(p): p for p in prod_paths}
    linked: list[dict[str, str]] = []
    for test_path in test_paths:
        stem = _production_stem(test_path)
        if stem and stem in prod_stems:
            linked.append({"test_path": test_path, "production_path": prod_stems[stem]})

    prefix = pr.get("title_prefix")
    expectation = PREFIX_EXPECTATION.get(str(prefix), set()) if prefix else set()
    primary_categories = {k for k, v in categories.items() if v > 0}
    if not prefix or not expectation:
        corroborated: bool | None = None
        corroboration_note = (
            "no conventional prefix" if not prefix else f"prefix '{prefix}' makes no path claim"
        )
    else:
        corroborated = bool(expectation & primary_categories)
        corroboration_note = (
            f"prefix '{prefix}' expects {sorted(expectation)}; observed {sorted(primary_categories)}"
        )

    risk_surfaces: set[str] = set()
    for row in files:
        for surface in row.get("risk_surfaces") or []:
            risk_surfaces.add(str(surface))

    languages: dict[str, int] = {}
    for row in files:
        key = str(row.get("language") or "unknown")
        languages[key] = languages.get(key, 0) + 1

    return {
        "pr_number": pr["pr_number"],
        "pr_id": pr["pr_id"],
        # --- semantic proxies
        "title_prefix": prefix,
        "title_scope": pr.get("title_scope"),
        "title_parser_confidence": pr.get("title_parser_confidence"),
        "title_breaking": pr.get("title_breaking"),
        "title_claim_corroborated": corroborated,
        "title_claim_note": corroboration_note,
        # --- component shape
        "file_count": total,
        "dominant_component": dominant,
        "dominant_component_share": dominant_share,
        "distinct_components": len(component_counts),
        "component_entropy": component_entropy(components, entropy_base),
        "component_histogram": dict(sorted(component_counts.items())),
        "distinct_platforms": len({str(f.get("platform") or "unknown") for f in files}),
        # --- change categories
        "category_histogram": dict(sorted(categories.items())),
        **{f"files_{name}": categories.get(name, 0) for name in CHANGE_CATEGORIES},
        "files_unclassified": categories.get("unclassified", 0),
        # --- add/modify/delete/rename distribution
        "files_added": statuses.get("A", 0),
        "files_modified": statuses.get("M", 0),
        "files_deleted": statuses.get("D", 0),
        "files_renamed": statuses.get("R", 0),
        "files_copied": statuses.get("C", 0),
        "files_type_changed": statuses.get("T", 0),
        # --- code vs generated, retained purely as a quality descriptor
        "code_file_count": len(code_files),
        "generated_or_mechanical_file_count": generated_files,
        "code_share": round(len(code_files) / total, 4) if total else None,
        # --- test linkage
        "test_file_count": len(test_paths),
        "production_file_count": len(prod_paths),
        "test_to_production_links": linked,
        "test_to_production_link_count": len(linked),
        "has_test_changes": bool(test_paths),
        "has_production_changes": bool(prod_paths),
        "tests_without_production_change": bool(test_paths and not prod_paths),
        "production_without_test_change": bool(prod_paths and not test_paths),
        # --- risk-surface indicators (evidence, not severity)
        "risk_surfaces": sorted(risk_surfaces),
        **{
            f"touches_{name}": name in risk_surfaces
            for name in (
                "public_api", "schema", "migration", "auth_privacy", "billing",
                "ingestion", "data_pipeline", "deployment", "shared_library",
                "feature_flag_surface",
            )
        },
        "language_histogram": dict(sorted(languages.items())),
        "primary_language": (
            max(languages.items(), key=lambda kv: (kv[1], kv[0]))[0] if languages else None
        ),
        "license_areas": sorted({str(f.get("license_area") or "unknown") for f in files}),
        "touches_enterprise_licensed_code": any(
            str(f.get("license_area", "")).lower().startswith("posthog enterprise")
            for f in files
        ),
        "change_shape_version": feature_version("change_shape"),
    }


def summarise(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    total = len(items) or 1
    corroborated = [r for r in items if r.get("title_claim_corroborated") is not None]
    return {
        "pull_requests": len(items),
        "with_conventional_prefix": sum(1 for r in items if r.get("title_prefix")),
        "conventional_prefix_rate": round(
            sum(1 for r in items if r.get("title_prefix")) / total, 4
        ),
        "title_claim_checked": len(corroborated),
        "title_claim_mismatch": sum(
            1 for r in corroborated if r["title_claim_corroborated"] is False
        ),
        "with_test_changes": sum(1 for r in items if r.get("has_test_changes")),
        "production_without_tests": sum(
            1 for r in items if r.get("production_without_test_change")
        ),
        "touching_migrations": sum(1 for r in items if r.get("touches_migration")),
        "touching_public_api": sum(1 for r in items if r.get("touches_public_api")),
        "touching_enterprise": sum(
            1 for r in items if r.get("touches_enterprise_licensed_code")
        ),
    }
