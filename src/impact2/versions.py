"""Version stamps for every Phase 2 derivation.

Phase 1's rule carries over: a derived record states which version of which
rule produced it, so a mixed-version table is detectable and a published claim
can be reproduced years later.  Bumping a value here is the signal that the
owning table must be rebuilt; a validation gate compares the stamps found on
stored rows against these constants.

The YAML files under ``config/phase2/`` mirror the versions that are
user-visible (rubric, attribution, outranking, analytics, llm); a gate asserts
the two agree.
"""

from __future__ import annotations

# The whole Phase 2 methodology.  Shown in the UI next to every ranking.
METHODOLOGY_VERSION = "1.0.0"

# Contract version of the static package handed to Phase 3.
EXPORT_SCHEMA_VERSION = "1.0.0"

# The Phase 1 schema version this code is written against.  Verified at load.
REQUIRED_PHASE1_SCHEMA = "1.0.0"

DERIVATION_VERSIONS: dict[str, str] = {
    "artifact_graph": "1.0.0",
    "episode_construction": "1.0.0",
    "episode_status": "1.0.0",
    "participants": "1.0.0",
    "dimension_rubric": "1.0.0",
    "propagation": "1.0.0",
    "decay": "1.0.0",
    "novelty": "1.0.0",
    "corrective_burden": "1.0.0",
    "review_causality": "1.0.0",
    "diversity": "1.0.0",
    "uncertainty": "1.0.0",
    "portfolio_aggregation": "1.0.0",
    "outranking": "1.0.0",
    "sensitivity": "1.0.0",
    "claims": "1.0.0",
    "validation": "1.0.0",
    "export": "1.0.0",
}

# Prompt versions live with the LLM layer but are stamped here too so a single
# import gives a caller every version in play.
PROMPT_VERSIONS: dict[str, str] = {
    "episode_extraction": "1.0.0",
    "dimension_evidence": "1.0.0",
    "review_consequence": "1.0.0",
    "semantic_edges": "1.0.0",
    "pairwise_episode_comparison": "1.0.0",
    "executive_summary": "1.0.0",
}


def derivation_version(name: str) -> str:
    try:
        return DERIVATION_VERSIONS[name]
    except KeyError as exc:  # pragma: no cover - programming error
        raise KeyError(
            f"unknown derivation {name!r}; add it to DERIVATION_VERSIONS"
        ) from exc


def all_versions() -> dict[str, object]:
    return {
        "methodology": METHODOLOGY_VERSION,
        "export_schema": EXPORT_SCHEMA_VERSION,
        "required_phase1_schema": REQUIRED_PHASE1_SCHEMA,
        "derivations": dict(DERIVATION_VERSIONS),
        "prompts": dict(PROMPT_VERSIONS),
    }
