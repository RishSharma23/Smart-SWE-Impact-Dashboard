"""Path -> component and path -> owner resolution.

The spec's priority order is implemented literally, and each resolved path
records which priority answered it:

    1  product manifest        products/<name>/manifest.tsx   (component)
    2  nearest owners.yaml     PostHog's distributed ownership (owner)
       nearest AGENTS.md       path-local instructions         (context)
    3  CODEOWNERS[-soft]       hard review gates               (owner)
    4  conventions             config/components.yaml          (component)
    5  module graph            impact.graph                    (component hint)
    6  unknown

Component and owner are *different dimensions* and are resolved separately:
priorities 1 and 4 answer "what part of the product is this", priorities 2 and
3 answer "which team is accountable".  Collapsing them would lose the case
PostHog cares about most -- a product directory whose specific file is owned by
a platform team.

Ownership semantics follow the repository's own resolver:
* the **nearest enclosing** ``owners.yaml`` wins;
* within a file, a pattern starting with ``/`` is anchored to that file's
  directory, and an unanchored pattern matches at any depth beneath it;
* later rules win over earlier ones (CODEOWNERS-style last-match).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

import yaml

from ..config import Settings
from ..hashing import sha256_text
from ..versions import feature_version
from .paths import glob_match, normalize_repo_path

log = logging.getLogger("impact.components")

CODEOWNERS_LINE_RE = re.compile(r"^(?P<pattern>\S+)(?P<owners>(?:\s+[@\w./-]+)*)\s*$")


@dataclass
class Resolution:
    path: str
    component: str
    platform: str
    component_source: str
    component_priority: int
    component_pattern: str | None
    owners: list[str]
    owner_source: str | None
    owner_priority: int | None
    owner_pattern: str | None
    license_area: str
    uncertainty: list[str] = field(default_factory=list)
    agents_context: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "component": self.component,
            "platform": self.platform,
            "component_source": self.component_source,
            "component_rule_priority": self.component_priority,
            "component_rule_pattern": self.component_pattern,
            "owners": self.owners,
            "owner_count": len(self.owners),
            "owner_source": self.owner_source,
            "owner_rule_priority": self.owner_priority,
            "owner_rule_pattern": self.owner_pattern,
            "license_area": self.license_area,
            "uncertainty": self.uncertainty,
            "is_unclassified": self.component_source == "unknown",
            "nearest_agents_file": self.agents_context,
            "component_map_version": feature_version("component_map"),
        }


class ComponentIndex:
    """Ownership + component ruleset snapshotted at one commit."""

    def __init__(
        self,
        *,
        conventions: list[dict[str, Any]],
        products: dict[str, dict[str, Any]],
        owners_files: dict[str, dict[str, Any]],
        codeowners: list[tuple[str, list[str], str]],
        agents_files: list[str],
        license_areas: list[dict[str, Any]],
        head_sha: str,
        source_hashes: dict[str, str],
    ) -> None:
        self.conventions = conventions
        self.products = products
        self.owners_files = owners_files
        self.codeowners = codeowners
        self.agents_dirs = sorted(
            {str(PurePosixPath(p).parent) for p in agents_files}, key=len, reverse=True
        )
        self.agents_files = {str(PurePosixPath(p).parent): p for p in agents_files}
        self.license_areas = sorted(
            license_areas, key=lambda a: len(a.get("path_prefix", "")), reverse=True
        )
        self.head_sha = head_sha
        self.source_hashes = source_hashes
        # Deepest first so "nearest enclosing directory" is a first-hit scan.
        self._owner_dirs = sorted(owners_files, key=lambda d: len(d), reverse=True)

    # -- construction ----------------------------------------------------

    @classmethod
    def build(cls, settings: Settings, head_sha: str) -> "ComponentIndex":
        from ..ingest.git_source import read_file_at, run_git

        repo_files = run_git(settings.clone_path, ["ls-files"]).splitlines()
        source_hashes: dict[str, str] = {}

        # -- priority 1: product manifests
        products: dict[str, dict[str, Any]] = {}
        manifest_re = re.compile(r"^products/([^/]+)/manifest\.tsx?$")
        for path in repo_files:
            match = manifest_re.match(path)
            if not match:
                continue
            name = match.group(1)
            content = read_file_at(settings, head_sha, path)
            products[name] = {
                "product": name,
                "manifest_path": path,
                "manifest_sha256": sha256_text(content) if content else None,
            }
        # Product directories without a manifest still exist and must not vanish.
        for path in repo_files:
            parts = path.split("/")
            if len(parts) > 2 and parts[0] == "products":
                products.setdefault(
                    parts[1],
                    {"product": parts[1], "manifest_path": None, "manifest_sha256": None},
                )

        # -- priority 2: distributed ownership
        owners_files: dict[str, dict[str, Any]] = {}
        for path in repo_files:
            if not path.endswith("owners.yaml"):
                continue
            content = read_file_at(settings, head_sha, path)
            if content is None:
                continue
            source_hashes[path] = sha256_text(content)
            try:
                parsed = yaml.safe_load(content) or {}
            except yaml.YAMLError as exc:
                log.warning("unparseable owners file %s: %s", path, exc)
                continue
            directory = str(PurePosixPath(path).parent)
            directory = "" if directory == "." else directory
            owners_files[directory] = {
                "path": path,
                "default_owners": _as_list(parsed.get("owners")),
                "teams": parsed.get("teams") or {},
                "rules": [
                    {
                        "match": _as_list(rule.get("match")),
                        "owners": _as_list(rule.get("owners")),
                    }
                    for rule in (parsed.get("rules") or [])
                    if isinstance(rule, dict)
                ],
            }

        # -- priority 3: CODEOWNERS
        codeowners: list[tuple[str, list[str], str]] = []
        for candidate in (settings.components.get("dynamic_sources", {}) or {}).get(
            "codeowners_paths", []
        ):
            content = read_file_at(settings, head_sha, candidate)
            if content is None:
                continue
            source_hashes[candidate] = sha256_text(content)
            for line in content.splitlines():
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                match = CODEOWNERS_LINE_RE.match(line)
                if not match:
                    continue
                owners = match.group("owners").split()
                codeowners.append((match.group("pattern"), owners, candidate))

        agents_files = [p for p in repo_files if p.endswith("AGENTS.md")]

        return cls(
            conventions=list(settings.components.get("conventions") or []),
            products=products,
            owners_files=owners_files,
            codeowners=codeowners,
            agents_files=agents_files,
            license_areas=[dict(a) for a in settings.license_areas],
            head_sha=head_sha,
            source_hashes=source_hashes,
        )

    # -- resolution ------------------------------------------------------

    def _license_area(self, path: str) -> str:
        for area in self.license_areas:
            prefix = area.get("path_prefix", "")
            if prefix and path.startswith(prefix):
                return str(area.get("license", "unknown"))
        for area in self.license_areas:
            if not area.get("path_prefix"):
                return str(area.get("license", "unknown"))
        return "unknown"

    def _resolve_owner(self, path: str) -> tuple[list[str], str | None, int | None, str | None]:
        """Nearest owners.yaml, then CODEOWNERS, then nothing."""
        for directory in self._owner_dirs:
            if directory and not (path == directory or path.startswith(directory + "/")):
                continue
            spec = self.owners_files[directory]
            relative = path[len(directory) + 1 :] if directory else path
            hit: tuple[list[str], str] | None = None
            for rule in spec["rules"]:
                for pattern in rule["match"]:
                    if _owners_pattern_matches(pattern, relative):
                        hit = (rule["owners"], pattern)  # last match wins
            if hit:
                return hit[0], f"owners_yaml:{spec['path']}", 2, hit[1]
            if spec["default_owners"]:
                return spec["default_owners"], f"owners_yaml:{spec['path']}", 2, "<default>"

        hit_co: tuple[list[str], str, str] | None = None
        for pattern, owners, source in self.codeowners:
            if _codeowners_matches(pattern, path):
                hit_co = (owners, pattern, source)  # last match wins
        if hit_co:
            return hit_co[0], f"codeowners:{hit_co[2]}", 3, hit_co[1]
        return [], None, None, None

    def _nearest_agents(self, path: str) -> str | None:
        for directory in self.agents_dirs:
            if directory in {"", "."}:
                continue
            if path.startswith(directory + "/"):
                return self.agents_files[directory]
        return self.agents_files.get(".")

    @lru_cache(maxsize=200_000)
    def resolve(self, path: str) -> Resolution:
        path = normalize_repo_path(path)
        uncertainty: list[str] = []

        component = None
        platform = "unknown"
        source = "unknown"
        priority = 6
        pattern_used: str | None = None

        # Priority 1 -- product manifests.
        parts = path.split("/")
        if len(parts) >= 2 and parts[0] == "products" and parts[1] in self.products:
            product = self.products[parts[1]]
            component = f"product:{parts[1]}"
            platform = "product"
            priority = 1 if product["manifest_path"] else 4
            source = "product_manifest" if product["manifest_path"] else "product_directory"
            pattern_used = product["manifest_path"] or "products/*/"
            if not product["manifest_path"]:
                uncertainty.append("product directory has no manifest.tsx")

        # Priority 4 -- conventions.
        if component is None:
            for rule in self.conventions:
                glob = str(rule.get("pattern", ""))
                if glob_match(glob, path):
                    component = _expand_component(str(rule.get("component", "")), glob, path)
                    platform = str(rule.get("platform", "unknown"))
                    source = "convention"
                    priority = 4
                    pattern_used = glob
                    break

        if component is None:
            component = "unknown"
            platform = "unknown"
            source = "unknown"
            priority = 6
            uncertainty.append("no component rule matched this path")

        owners, owner_source, owner_priority, owner_pattern = self._resolve_owner(path)
        if len(owners) > 1:
            uncertainty.append(f"{len(owners)} owners match; ownership is shared")
        if not owners:
            uncertainty.append("no owner rule matched this path")

        license_area = self._license_area(path)
        if license_area != "MIT" and license_area != "unknown":
            uncertainty.append(f"non-default license area: {license_area}")

        return Resolution(
            path=path,
            component=component,
            platform=platform,
            component_source=source,
            component_priority=priority,
            component_pattern=pattern_used,
            owners=sorted(owners),
            owner_source=owner_source,
            owner_priority=owner_priority,
            owner_pattern=owner_pattern,
            license_area=license_area,
            uncertainty=uncertainty,
            agents_context=self._nearest_agents(path),
        )

    # -- reporting -------------------------------------------------------

    def component_catalog(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for name, product in sorted(self.products.items()):
            rows.append(
                {
                    "component": f"product:{name}",
                    "platform": "product",
                    "label": name.replace("_", " "),
                    "source_rule": "product_manifest" if product["manifest_path"] else "product_directory",
                    "path_glob": f"products/{name}/**",
                    "manifest_path": product["manifest_path"],
                    "manifest_sha256": product["manifest_sha256"],
                    "snapshot_commit": self.head_sha,
                    "component_map_version": feature_version("component_map"),
                }
            )
        seen = {r["component"] for r in rows}
        for rule in self.conventions:
            component = str(rule.get("component", ""))
            if "{1}" in component or component in seen:
                continue
            seen.add(component)
            rows.append(
                {
                    "component": component,
                    "platform": str(rule.get("platform", "unknown")),
                    "label": component.split(":", 1)[-1].replace("-", " "),
                    "source_rule": "convention",
                    "path_glob": str(rule.get("pattern", "")),
                    "manifest_path": None,
                    "manifest_sha256": None,
                    "snapshot_commit": self.head_sha,
                    "component_map_version": feature_version("component_map"),
                }
            )
        return rows

    def rule_snapshot(self) -> dict[str, Any]:
        return {
            "head_sha": self.head_sha,
            "owners_files": {
                directory: {
                    "path": spec["path"],
                    "rule_count": len(spec["rules"]),
                    "sha256": self.source_hashes.get(spec["path"]),
                }
                for directory, spec in sorted(self.owners_files.items())
            },
            "codeowners_rules": len(self.codeowners),
            "codeowners_sources": sorted(
                {source for _, _, source in self.codeowners}
            ),
            "product_count": len(self.products),
            "agents_files": len(self.agents_files),
            "convention_rules": len(self.conventions),
            "source_hashes": self.source_hashes,
        }


# --------------------------------------------------------------------------


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _expand_component(template: str, pattern: str, path: str) -> str:
    """Fill ``{1}`` from the path segment the glob's first ``*`` captured."""
    if "{1}" not in template:
        return template
    prefix = pattern.split("*", 1)[0].rstrip("/")
    remainder = path[len(prefix) :].lstrip("/") if prefix else path
    segment = remainder.split("/", 1)[0] if remainder else "unknown"
    return template.replace("{1}", segment or "unknown")


def _owners_pattern_matches(pattern: str, relative: str) -> bool:
    """PostHog owners.yaml matching.

    ``/x/`` anchors to the owners.yaml directory; a bare ``x/`` or ``x``
    matches at any depth beneath it.
    """
    pattern = pattern.strip()
    if not pattern:
        return False
    if pattern.startswith("/"):
        body = pattern[1:]
        if body.endswith("/"):
            body = body[:-1]
            return relative == body or relative.startswith(body + "/")
        return glob_match(body, relative) or relative == body
    if pattern.endswith("/"):
        body = pattern[:-1]
        return any(
            part == body for part in PurePosixPath(relative).parts[:-1]
        ) or relative.startswith(body + "/")
    if glob_match(pattern, relative):
        return True
    # Unanchored: match the basename or any path suffix.
    if glob_match(pattern, PurePosixPath(relative).name):
        return True
    return glob_match("**/" + pattern, relative)


def _codeowners_matches(pattern: str, path: str) -> bool:
    pattern = pattern.strip()
    if not pattern:
        return False
    if pattern.startswith("/"):
        pattern = pattern[1:]
    if pattern.endswith("/"):
        return path.startswith(pattern) or path == pattern[:-1]
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-3] + "/")
    if glob_match(pattern, path):
        return True
    if "/" not in pattern:
        return glob_match("**/" + pattern, path)
    return path.startswith(pattern + "/")


def component_entropy(components: Iterable[str], base: float = 2.0) -> float:
    """Shannon entropy over the distribution of touched components."""
    import math

    items = [c for c in components if c]
    if not items:
        return 0.0
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    total = len(items)
    entropy = 0.0
    for count in counts.values():
        share = count / total
        entropy -= share * math.log(share, base)
    return round(entropy, 6)


def summarise_coverage(resolutions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(resolutions)
    total = len(rows) or 1
    by_source: dict[str, int] = {}
    for row in rows:
        key = str(row.get("component_source", "unknown"))
        by_source[key] = by_source.get(key, 0) + 1
    unknown = sum(1 for r in rows if r.get("component") == "unknown")
    unowned = sum(1 for r in rows if not r.get("owners"))
    return {
        "paths": len(rows),
        "by_component_source": dict(sorted(by_source.items())),
        "unknown_component_paths": unknown,
        "unknown_component_rate": round(unknown / total, 6),
        "classified_rate": round(1 - unknown / total, 6),
        "unowned_paths": unowned,
        "unowned_rate": round(unowned / total, 6),
    }
