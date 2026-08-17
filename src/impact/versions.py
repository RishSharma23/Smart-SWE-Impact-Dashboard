"""Version stamps recorded on every derived record.

Principle 2 of the phase spec: *every derived field records derivation version,
input identifiers, and computation timestamp*.  Bumping a version here is the
signal that a derived table must be rebuilt; the quality gate compares the
version stamped on stored rows against these constants.
"""

from __future__ import annotations

# Bump when the extraction wire format or request shape changes.
EXTRACTOR_VERSION = "1.0.0"

# Bump when the normalized data contract changes (column added/removed/retyped).
SCHEMA_VERSION = "1.0.0"

# Per-feature-family derivation versions.  Each derived row carries the version
# of the family that produced it so mixed-version tables are detectable.
FEATURE_VERSIONS: dict[str, str] = {
    "title_parse": "1.0.0",
    "path_classify": "1.0.0",
    "component_map": "1.0.0",
    "actor_identity": "1.0.0",
    "change_shape": "1.0.0",
    "blast_radius": "1.0.0",
    "episode_edges": "1.0.0",
    "regression": "1.0.0",
    "review_intervention": "1.0.0",
    "anomaly": "1.0.0",
    "dependency_graph": "1.0.0",
}

# Single string used in the run manifest and in table-level provenance.
PIPELINE_VERSION = "1.0.0"


def feature_version(name: str) -> str:
    try:
        return FEATURE_VERSIONS[name]
    except KeyError as exc:  # pragma: no cover - programming error
        raise KeyError(
            f"unknown feature family {name!r}; add it to FEATURE_VERSIONS"
        ) from exc
