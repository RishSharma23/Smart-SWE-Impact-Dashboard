"""Versioned prompts and their JSON schemas.

Every prompt here obeys four rules that make the output auditable:

1. **Evidence IDs are mandatory.**  The schema requires an ``evidence_ids``
   array on every claim, and the task layer drops any claim whose IDs are not
   in the set that was supplied.  A model cannot cite something it was not
   given.
2. **Author claims and observed facts are separate fields.**  "The PR says it
   fixes the funnel bug" and "the funnel bug is fixed" are different
   statements, and conflating them is how a dashboard ends up repeating
   marketing copy as fact.
3. **Identity-blind where feasible.**  Logins are replaced with
   ``ENGINEER_A``/``ENGINEER_B`` before the payload is built, so a comparison
   cannot be swayed by who wrote the code.
4. **The rubric is quoted into the prompt**, so the model grades against the
   same written rules the deterministic evaluator uses, and a disagreement
   between them is meaningful rather than a category error.
"""

from __future__ import annotations

from typing import Any, Mapping

SCHEMA_VERSION = "1.0.0"

_BASE_SYSTEM = (
    "You are an evidence extractor for an engineering-impact analysis. "
    "You never rank people, never speculate about intent, effort, seniority or "
    "personality, and never assert anything you were not shown. "
    "Every claim you make must cite evidence_ids drawn ONLY from the ids "
    "supplied in the input. If the evidence does not support a claim, say so "
    "explicitly and leave the field empty rather than guessing. "
    "Respond with JSON matching the supplied schema and nothing else."
)


def _evidence_array(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": {"type": "string"},
    }


# --------------------------------------------------------------------------
# 1. episode extraction
# --------------------------------------------------------------------------

EPISODE_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["problem", "intervention", "claimed_outcome", "observed_outcome",
                 "evidence_ids", "insufficient_evidence"],
    "properties": {
        "problem": {"type": "string",
                    "description": "The problem this work addressed, in one sentence."},
        "intervention": {"type": "string",
                         "description": "What was actually changed, in one sentence."},
        "claimed_outcome": {
            "type": "string",
            "description": "What the AUTHORS claim resulted. Quote their framing.",
        },
        "observed_outcome": {
            "type": "string",
            "description": (
                "What the evidence independently corroborates. Empty string if "
                "nothing corroborates the claim."
            ),
        },
        "evidence_ids": _evidence_array("IDs supporting the fields above."),
        "insufficient_evidence": {
            "type": "boolean",
            "description": "True if the supplied evidence does not support a summary.",
        },
    },
}

EPISODE_EXTRACTION_SYSTEM = (
    _BASE_SYSTEM
    + " Distinguish sharply between what the pull-request text CLAIMS and what "
      "the linked artifacts CORROBORATE. Merging a pull request is not evidence "
      "that users saw a change."
)


def episode_extraction_user(payload: Mapping[str, Any]) -> str:
    return (
        "Summarise this impact episode.\n\n"
        f"Episode reference: {payload.get('subject')}\n"
        f"Status (computed deterministically): {payload.get('status')}\n"
        f"Release corroboration: {payload.get('release_corroboration')}\n\n"
        f"Artifacts (cite these ids):\n{payload.get('artifacts')}\n\n"
        f"Pull request titles and bodies:\n{payload.get('text')}\n\n"
        f"Deterministic features already computed:\n{payload.get('features')}\n"
    )


# --------------------------------------------------------------------------
# 2. dimension evidence
# --------------------------------------------------------------------------

DIMENSION_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["dimension", "band", "rationale", "evidence_ids",
                 "counterevidence", "confidence", "insufficient_evidence"],
    "properties": {
        "dimension": {"type": "string"},
        "band": {
            "type": ["integer", "null"],
            "description": "0-4, or null for UNKNOWN. Null is not zero.",
        },
        "rationale": {"type": "string"},
        "evidence_ids": _evidence_array("IDs that justify this band."),
        "counterevidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["detail", "evidence_ids"],
                "properties": {
                    "detail": {"type": "string"},
                    "evidence_ids": _evidence_array("IDs for this counterevidence."),
                },
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "insufficient_evidence": {"type": "boolean"},
    },
}

DIMENSION_EVIDENCE_SYSTEM = (
    _BASE_SYSTEM
    + " You grade against the rubric quoted in the user message and nothing else. "
      "Never raise a band because there was a lot of work: volume is not "
      "evidence. Band 3 requires corroboration from at least two distinct "
      "artifact classes; band 4 additionally requires an explicit textual "
      "marker. Use null for UNKNOWN when the evidence cannot be read at all; "
      "use 0 only when you can see that there is genuinely nothing."
)


def dimension_evidence_user(payload: Mapping[str, Any]) -> str:
    return (
        f"Dimension: {payload.get('dimension')}\n\n"
        f"Rubric (grade against exactly this):\n{payload.get('rubric')}\n\n"
        f"Artifacts (cite these ids):\n{payload.get('artifacts')}\n\n"
        f"Episode evidence:\n{payload.get('text')}\n\n"
        f"Deterministic features:\n{payload.get('features')}\n"
    )


# --------------------------------------------------------------------------
# 3. review consequence
# --------------------------------------------------------------------------

REVIEW_CONSEQUENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["concern_classes", "is_consequential", "consequence_band",
                 "reasoning", "evidence_ids"],
    "properties": {
        "concern_classes": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["correctness", "design_architecture", "alternative_approach",
                         "scope", "security", "privacy", "data_integrity",
                         "migration_safety", "performance", "api_contract",
                         "testing", "style_or_naming", "question", "praise", "other"],
            },
        },
        "is_consequential": {"type": "boolean"},
        "consequence_band": {
            "type": "string",
            "enum": ["none", "local_change", "design_change", "prevented_risk"],
        },
        "reasoning": {"type": "string"},
        "evidence_ids": _evidence_array("The comment id, and any change ids cited."),
    },
}

REVIEW_CONSEQUENCE_SYSTEM = (
    _BASE_SYSTEM
    + " Judge only what the comment RAISES and whether the supplied evidence "
      "shows the code changed afterwards. You cannot see the diff history "
      "(this repository squash-merges), so do not claim a change you were not "
      "shown. A polite suggestion that nothing followed is 'none'."
)


def review_consequence_user(payload: Mapping[str, Any]) -> str:
    return (
        f"Review comment id: {payload.get('subject')}\n"
        f"File: {payload.get('path')}\n\n"
        f"Comment text:\n{payload.get('text')}\n\n"
        f"Deterministic evidence about what followed:\n{payload.get('features')}\n\n"
        f"Citable ids:\n{payload.get('artifacts')}\n"
    )


# --------------------------------------------------------------------------
# 4. semantic edges
# --------------------------------------------------------------------------

SEMANTIC_EDGES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["same_initiative", "relationship", "reasoning", "confidence"],
    "properties": {
        "same_initiative": {"type": "boolean"},
        "relationship": {
            "type": "string",
            "enum": ["same_initiative", "follow_up", "unrelated",
                     "same_area_different_work", "cannot_tell"],
        },
        "reasoning": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}

SEMANTIC_EDGES_SYSTEM = (
    _BASE_SYSTEM
    + " Two pull requests touching the same area are NOT the same initiative. "
      "Only answer same_initiative when the texts describe one piece of work "
      "split across them. 'fix flaky test' twice is same_area_different_work."
)


def semantic_edges_user(payload: Mapping[str, Any]) -> str:
    return (
        "Do these two pull requests belong to one initiative?\n\n"
        f"A:\n{payload.get('a')}\n\n"
        f"B:\n{payload.get('b')}\n\n"
        f"Deterministic signals:\n{payload.get('features')}\n"
    )


# --------------------------------------------------------------------------
# 5. identity-blinded pairwise episode comparison
# --------------------------------------------------------------------------

PAIRWISE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["stronger", "dimension_notes", "reasoning", "confidence",
                 "insufficient_evidence"],
    "properties": {
        "stronger": {"type": "string", "enum": ["A", "B", "incomparable"]},
        "dimension_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["dimension", "stronger", "why"],
                "properties": {
                    "dimension": {"type": "string"},
                    "stronger": {"type": "string", "enum": ["A", "B", "equal", "unknown"]},
                    "why": {"type": "string"},
                },
            },
        },
        "reasoning": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "insufficient_evidence": {"type": "boolean"},
    },
}

PAIRWISE_SYSTEM = (
    _BASE_SYSTEM
    + " The two episodes are anonymised as A and B. You do not know who wrote "
      "either. Compare the EPISODES against the rubric, never the people. "
      "'incomparable' is a valid and often correct answer."
)


def pairwise_user(payload: Mapping[str, Any]) -> str:
    return (
        f"Rubric:\n{payload.get('rubric')}\n\n"
        f"Episode A:\n{payload.get('a')}\n\n"
        f"Episode B:\n{payload.get('b')}\n"
    )


# --------------------------------------------------------------------------
# 6. executive summary
# --------------------------------------------------------------------------

EXECUTIVE_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "sentences", "evidence_ids"],
    "properties": {
        "summary": {"type": "string", "description": "Two to four sentences."},
        "sentences": {
            "type": "array",
            "description": "Each sentence of the summary with its own evidence.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "evidence_ids"],
                "properties": {
                    "text": {"type": "string"},
                    "evidence_ids": _evidence_array("IDs supporting this sentence."),
                },
            },
        },
        "evidence_ids": _evidence_array("Union of all cited ids."),
    },
}

EXECUTIVE_SUMMARY_SYSTEM = (
    _BASE_SYSTEM
    + " Write only what the supplied evidence supports. Every sentence you "
      "return must carry its own evidence_ids. Do not praise, do not compare to "
      "other engineers, and do not describe anyone's ability — describe the "
      "work and what it did. State uncertainty where the evidence is thin."
)


def executive_summary_user(payload: Mapping[str, Any]) -> str:
    return (
        f"Subject: {payload.get('subject')}\n\n"
        f"Evidence you may cite (ids and their content):\n{payload.get('artifacts')}\n\n"
        f"Deterministic findings:\n{payload.get('features')}\n\n"
        f"Known limitations that must not be contradicted:\n{payload.get('limitations')}\n"
    )


TASKS: dict[str, dict[str, Any]] = {
    "episode_extraction": {
        "system": EPISODE_EXTRACTION_SYSTEM,
        "user": episode_extraction_user,
        "schema": EPISODE_EXTRACTION_SCHEMA,
    },
    "dimension_evidence": {
        "system": DIMENSION_EVIDENCE_SYSTEM,
        "user": dimension_evidence_user,
        "schema": DIMENSION_EVIDENCE_SCHEMA,
    },
    "review_consequence": {
        "system": REVIEW_CONSEQUENCE_SYSTEM,
        "user": review_consequence_user,
        "schema": REVIEW_CONSEQUENCE_SCHEMA,
    },
    "semantic_edges": {
        "system": SEMANTIC_EDGES_SYSTEM,
        "user": semantic_edges_user,
        "schema": SEMANTIC_EDGES_SCHEMA,
    },
    "pairwise_episode_comparison": {
        "system": PAIRWISE_SYSTEM,
        "user": pairwise_user,
        "schema": PAIRWISE_SCHEMA,
    },
    "executive_summary": {
        "system": EXECUTIVE_SUMMARY_SYSTEM,
        "user": executive_summary_user,
        "schema": EXECUTIVE_SUMMARY_SCHEMA,
    },
}
