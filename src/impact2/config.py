"""Phase 2 configuration.

Every tunable — clustering control, rubric threshold, criterion weight, decay
half-life, confidence discount — lives in ``config/phase2/*.yaml`` and is
loaded here into one frozen object that is serialised verbatim into the
dashboard manifest.  A reader of the published dashboard can therefore
reconstruct exactly what the run was told to do, which is the difference
between a defensible ranking and a mysterious one.

Nothing in this module invents a default that is not also written down in the
YAML.  ``Phase2Config.get`` walks a dotted path and raises on a missing key
rather than silently substituting a number nobody agreed to.
"""

from __future__ import annotations

import copy
import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config" / "phase2"

UTC = dt.timezone.utc

CONFIG_FILES = {
    "episodes": "episodes.yaml",
    "rubric": "rubric.yaml",
    "attribution": "attribution.yaml",
    "outranking": "outranking.yaml",
    "analytics": "analytics.yaml",
    "eligibility": "eligibility.yaml",
    "llm": "llm.yaml",
    "export": "export.yaml",
}

# Top-level key inside each file (files are namespaced so they can be merged
# into one manifest without collisions).
ROOT_KEYS = {
    "episodes": "episodes",
    "rubric": "rubric",
    "attribution": "attribution",
    "outranking": "outranking",
    "analytics": "analytics",
    "eligibility": "eligibility",
    "llm": "llm",
    "export": "export",
}

_MISSING = object()


class ConfigError(KeyError):
    """A configuration path was requested that nobody wrote down."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"missing Phase 2 config file: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass(frozen=True)
class Paths:
    """Where Phase 2 reads from and writes to.

    ``phase1_artifacts`` is the contract surface (``artifacts/``).  ``fallback``
    points at the un-exported pipeline layers and is only used when the caller
    explicitly allows it — see :mod:`impact2.inputs`.
    """

    project_root: Path
    phase1_artifacts: Path
    phase1_schemas: Path
    fallback_normalized: Path
    fallback_derived: Path
    work: Path                 # data/phase2 — derived Phase 2 tables
    llm_cache: Path
    reports: Path              # reports/phase2 — audit queues, validation
    export: Path               # artifacts/phase3 — the static UI package
    export_schemas: Path

    @classmethod
    def build(cls, project_root: Path | None = None) -> "Paths":
        root = Path(project_root or PROJECT_ROOT)
        data = Path(os.getenv("IMPACT_DATA_DIR", root / "data"))
        if not data.is_absolute():
            data = root / data
        return cls(
            project_root=root,
            phase1_artifacts=root / "artifacts",
            phase1_schemas=root / "schemas",
            fallback_normalized=data / "normalized",
            fallback_derived=data / "derived",
            work=data / "phase2",
            llm_cache=data / "phase2" / "llm_cache",
            reports=root / "reports" / "phase2",
            export=root / "artifacts" / "phase3",
            export_schemas=root / "artifacts" / "phase3" / "schemas",
        )

    def ensure(self) -> "Paths":
        for folder in (self.work, self.llm_cache, self.reports, self.export,
                       self.export_schemas):
            folder.mkdir(parents=True, exist_ok=True)
        return self

    def as_dict(self) -> dict[str, str]:
        # Relative paths only: absolute local paths must never reach an export.
        def rel(p: Path) -> str:
            try:
                return str(p.relative_to(self.project_root))
            except ValueError:
                return p.name

        return {
            "phase1_artifacts": rel(self.phase1_artifacts),
            "work": rel(self.work),
            "reports": rel(self.reports),
            "export": rel(self.export),
        }


@dataclass(frozen=True)
class Phase2Config:
    """All Phase 2 configuration, frozen, with the file hashes that produced it."""

    sections: dict[str, Any]
    file_hashes: dict[str, str]
    paths: Paths
    overrides: dict[str, Any] = field(default_factory=dict)

    # -- access ----------------------------------------------------------
    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        """Read ``section.a.b.c``.

        A missing key raises unless a default is passed explicitly, because a
        silently-defaulted threshold is a threshold nobody can audit.
        """
        if dotted in self.overrides:
            return self.overrides[dotted]
        node: Any = self.sections
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                if default is _MISSING:
                    raise ConfigError(
                        f"no Phase 2 configuration at {dotted!r} "
                        f"(stopped at {part!r})"
                    )
                return default
        return copy.deepcopy(node) if isinstance(node, (dict, list)) else node

    def section(self, name: str) -> dict[str, Any]:
        return copy.deepcopy(self.sections.get(name) or {})

    def with_overrides(self, overrides: dict[str, Any]) -> "Phase2Config":
        """Return a copy with dotted-path overrides — used by sensitivity runs."""
        merged = dict(self.overrides)
        merged.update(overrides)
        return Phase2Config(
            sections=self.sections,
            file_hashes=self.file_hashes,
            paths=self.paths,
            overrides=merged,
        )

    # -- criteria helpers ------------------------------------------------
    @property
    def criteria(self) -> list[str]:
        return list(self.get("outranking.criteria").keys())

    def criterion_weights(self, scenario: str = "balanced") -> dict[str, float]:
        base = {k: float(v["weight"]) for k, v in self.get("outranking.criteria").items()}
        scenarios = self.get("outranking.scenarios")
        override = (scenarios.get(scenario) or {}).get("weights") or {}
        weights = {**base, **{k: float(v) for k, v in override.items()}}
        total = sum(weights.values()) or 1.0
        return {k: round(v / total, 8) for k, v in weights.items()}

    def as_dict(self) -> dict[str, Any]:
        return {
            "sections": copy.deepcopy(self.sections),
            "file_sha256": dict(self.file_hashes),
            "overrides": dict(self.overrides),
            "paths": self.paths.as_dict(),
        }


def load_config(
    *, project_root: Path | None = None, overrides: dict[str, Any] | None = None
) -> Phase2Config:
    import hashlib

    paths = Paths.build(project_root)
    sections: dict[str, Any] = {}
    hashes: dict[str, str] = {}
    for name, filename in CONFIG_FILES.items():
        path = (paths.project_root / "config" / "phase2" / filename)
        raw = _read_yaml(path)
        sections[name] = raw.get(ROOT_KEYS[name], raw)
        hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    return Phase2Config(
        sections=sections, file_hashes=hashes, paths=paths,
        overrides=dict(overrides or {}),
    )


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_ts(value: Any) -> dt.datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime.

    Mirrors :func:`impact.config.parse_ts` deliberately rather than importing
    it, so Phase 2 has no import-time dependency on Phase 1 internals — only on
    the Parquet contract.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=UTC)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def days_between(a: dt.datetime | None, b: dt.datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 86400.0


def now() -> dt.datetime:
    return dt.datetime.now(UTC)


def first(values: Iterable[Any], default: Any = None) -> Any:
    for value in values:
        if value is not None:
            return value
    return default
