"""Path classification: categories, languages, risk surfaces.

``fnmatch`` is deliberately not used.  Its ``*`` crosses ``/``, so a pattern
like ``posthog/api/**`` would also match ``products/x/posthog/api/y`` and
``**/*.md`` vs ``docs/**`` would give indistinguishable results.  This module
compiles globs to regexes with real path semantics:

    ``*``   any run of characters except ``/``
    ``**``  any run of characters including ``/`` (and an empty run, so
            ``products/*/**`` matches ``products/x/manifest.tsx``)
    ``?``   exactly one character except ``/``

A path may carry many categories at once; that is the point.  Nothing here
removes a file from the dataset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from ..versions import feature_version


def normalize_repo_path(path: str) -> str:
    """Normalise a repository-relative path.

    Deliberately NOT ``lstrip("./")``: ``str.lstrip`` strips a *character set*,
    so ``".github/workflows/ci.yml"`` would become
    ``"github/workflows/ci.yml"`` and every dot-directory in the repository
    (``.github``, ``.claude``, ``.agents``) would silently fail to match its
    rules.
    """
    path = (path or "").strip()
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


@lru_cache(maxsize=8192)
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a path glob into an anchored regex."""
    out: list[str] = ["^"]
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**", index):
                # Trailing "/**" must also match the directory itself, so that
                # "products/*/**" matches "products/experiments" as well as
                # "products/experiments/backend/x.py".
                if index + 2 == length and out and out[-1] == "/":
                    out.pop()
                    out.append("(?:/.*)?")
                    index += 2
                    continue
                if pattern.startswith("**/", index):
                    out.append("(?:.*/)?")
                    index += 3
                    continue
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
            index += 1
            continue
        if char == "?":
            out.append("[^/]")
            index += 1
            continue
        if char == "[":
            close = pattern.find("]", index)
            if close == -1:
                out.append(re.escape(char))
                index += 1
                continue
            out.append(pattern[index : close + 1])
            index = close + 1
            continue
        out.append(re.escape(char))
        index += 1
    out.append("$")
    return re.compile("".join(out))


def glob_match(pattern: str, path: str) -> bool:
    if glob_to_regex(pattern).match(path):
        return True
    # "dir/**" conventionally also matches "dir" and everything under it even
    # when the pattern was written without a trailing slash.
    if pattern.endswith("/**"):
        return path == pattern[:-3] or path.startswith(pattern[:-3] + "/")
    return False


@dataclass
class PathClassification:
    path: str
    extension: str | None
    language: str
    categories: list[str] = field(default_factory=list)
    risk_surfaces: list[str] = field(default_factory=list)
    matched_rules: list[dict[str, str]] = field(default_factory=list)
    depth: int = 0
    top_level: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "extension": self.extension,
            "language": self.language,
            "categories": sorted(self.categories),
            "risk_surfaces": sorted(self.risk_surfaces),
            "matched_rules": self.matched_rules,
            "path_depth": self.depth,
            "top_level_dir": self.top_level,
            "is_test": "test" in self.categories,
            "is_docs": "docs" in self.categories,
            "is_generated": "generated" in self.categories,
            "is_snapshot": "snapshot" in self.categories,
            "is_lockfile": "lockfile" in self.categories,
            "is_vendor": "vendor" in self.categories,
            "is_migration": "migration" in self.categories,
            "is_config": "config" in self.categories,
            "is_binary_asset": "binary_asset" in self.categories,
            "is_styling": "styling" in self.categories,
            "is_localization": "localization" in self.categories,
            "is_ci": "ci" in self.categories,
            "path_classify_version": feature_version("path_classify"),
        }


class PathClassifier:
    def __init__(self, generated_cfg: Mapping[str, Any]) -> None:
        self.categories: dict[str, list[str]] = {
            name: list(spec.get("globs", []))
            for name, spec in (generated_cfg.get("categories") or {}).items()
        }
        self.content_markers: dict[str, list[str]] = {
            name: list(spec.get("content_markers", []))
            for name, spec in (generated_cfg.get("categories") or {}).items()
            if spec.get("content_markers")
        }
        self.risk: dict[str, list[str]] = {
            name: list(spec.get("globs", []))
            for name, spec in (generated_cfg.get("risk_surfaces") or {}).items()
        }
        self.languages: dict[str, str] = dict(generated_cfg.get("languages") or {})
        self.bulk = dict(generated_cfg.get("bulk_change") or {})

    @lru_cache(maxsize=200_000)
    def _classify_cached(self, path: str) -> tuple:
        categories: list[str] = []
        rules: list[dict[str, str]] = []
        for name, globs in self.categories.items():
            for pattern in globs:
                if glob_match(pattern, path):
                    categories.append(name)
                    rules.append({"category": name, "pattern": pattern})
                    break
        surfaces: list[str] = []
        for name, globs in self.risk.items():
            for pattern in globs:
                if glob_match(pattern, path):
                    surfaces.append(name)
                    rules.append({"risk_surface": name, "pattern": pattern})
                    break
        return tuple(categories), tuple(surfaces), tuple(
            tuple(sorted(r.items())) for r in rules
        )

    def classify(self, path: str) -> PathClassification:
        path = normalize_repo_path(path)
        pure = PurePosixPath(path)
        ext = pure.suffix.lower() or None
        # Compound suffixes carry more meaning than the last one alone.
        if path.endswith((".d.ts", ".test.ts", ".test.tsx", ".spec.ts")):
            ext = "." + path.split(".", 1)[1].lower() if "." in path else ext
            ext = ".ts" if ext and ext.endswith("ts") else ext
        language = self.languages.get(pure.suffix.lower(), "unknown")

        categories, surfaces, raw_rules = self._classify_cached(path)
        rules = [dict(r) for r in raw_rules]

        parts = pure.parts
        return PathClassification(
            path=path,
            extension=pure.suffix.lower() or None,
            language=language,
            categories=list(categories),
            risk_surfaces=list(surfaces),
            matched_rules=rules,
            depth=len(parts),
            top_level=parts[0] if parts else None,
        )

    def is_bulk_change(self, classifications: Iterable[PathClassification]) -> dict[str, Any]:
        """Flag mechanical/bulk changes as a descriptor, never as a filter."""
        items = list(classifications)
        min_files = int(self.bulk.get("min_files", 25))
        min_share = float(self.bulk.get("min_share_single_category", 0.9))
        considered = list(self.bulk.get("categories_considered", []))
        if not items:
            return {"is_bulk_change": False, "bulk_category": None, "bulk_share": None}
        if len(items) < min_files:
            return {"is_bulk_change": False, "bulk_category": None, "bulk_share": None}
        best_name, best_share = None, 0.0
        for name in considered:
            share = sum(1 for c in items if name in c.categories) / len(items)
            if share > best_share:
                best_name, best_share = name, share
        return {
            "is_bulk_change": best_share >= min_share,
            "bulk_category": best_name if best_share >= min_share else None,
            "bulk_share": round(best_share, 4),
        }


def content_looks_generated(text: str | None, markers: Iterable[str]) -> bool:
    if not text:
        return False
    head = text[:4000].lower()
    return any(marker.lower() in head for marker in markers)
