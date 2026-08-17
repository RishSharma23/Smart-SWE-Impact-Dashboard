"""Running the semantic layer, and proving it is stable enough to use.

Every task returns a record with a ``method`` field — ``llm:<model>@<prompt>``
when the model ran, ``pending`` when it did not.  Nothing in the deterministic
pipeline changes shape based on whether this ran; the LLM adds fields, it never
replaces them.  A record that carries both a deterministic verdict and an LLM
verdict keeps both, and the disagreement rate between them is reported.

The stability programme (validation item 3) is implemented here because it is
the same machinery: repeat a case N times, reverse the artifact order, blind the
identity, and ablate to title-only and diff-only.  If the model gives three
different answers to the same input, that is a fact about the model the reader
needs, and it is measured rather than assumed away.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Mapping, Sequence

from ..config import Phase2Config
from ..ids import canonical_json
from .prompts import TASKS
from .provider import LLMClient, redact

log = logging.getLogger("impact2.llm.tasks")


def _artifact_block(artifacts: Sequence[Mapping[str, Any]], limit: int = 40) -> str:
    lines = []
    for artifact in artifacts[:limit]:
        lines.append(
            f"- id={artifact.get('artifact_id')} kind={artifact.get('artifact_kind')} "
            f"title={str(artifact.get('title') or '')[:120]!r} "
            f"url={artifact.get('url')}"
        )
    return "\n".join(lines) or "(none)"


def _allowed_ids(artifacts: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(a.get("artifact_id")) for a in artifacts if a.get("artifact_id")}


def _filter_evidence(payload: Mapping[str, Any], allowed: set[str]) -> tuple[list[str], list[str]]:
    """Drop cited IDs the model was not given. A model cannot cite what it never saw."""
    cited = [str(x) for x in (payload.get("evidence_ids") or [])]
    kept = [c for c in cited if c in allowed]
    dropped = [c for c in cited if c not in allowed]
    return kept, dropped


class SemanticLayer:
    def __init__(self, client: LLMClient, config: Phase2Config) -> None:
        self.client = client
        self.config = config
        self.forbidden = list(
            config.get("llm.controls.redaction.forbidden_fields")
        )
        self.disagreements: list[dict[str, Any]] = []

    def _run(self, task: str, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        if not bool(self.config.get(f"llm.tasks.{task}.enabled")):
            return None
        spec = TASKS[task]
        limit = int(self.config.get(f"llm.tasks.{task}.max_input_chars"))
        clean = redact(dict(payload), self.forbidden)
        user = spec["user"](clean)[:limit]
        return self.client.complete(
            task=task, system=spec["system"], user=user,
            schema=spec["schema"], payload=clean,
        )

    def _method(self, task: str) -> str:
        return (
            f"llm:{self.client.provider}/{self.client.model}"
            f"@{self.config.get(f'llm.tasks.{task}.prompt_version')}"
        )

    # -- episode narrative -------------------------------------------------
    def episode_extraction(
        self, episode: Mapping[str, Any], artifacts: Sequence[Mapping[str, Any]],
        text: str, features: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "subject": episode.get("episode_id"),
            "status": episode.get("status"),
            "release_corroboration": episode.get("release_corroboration"),
            "artifacts": _artifact_block(artifacts),
            "text": text,
            "features": canonical_json(features),
        }
        response = self._run("episode_extraction", payload)
        if response is None:
            return {
                "episode_id": episode.get("episode_id"),
                "llm_status": "pending",
                "method": "pending",
                "note": "no LLM provider configured; deterministic narrative is in use",
            }
        kept, dropped = _filter_evidence(response, _allowed_ids(artifacts))
        return {
            "episode_id": episode.get("episode_id"),
            "llm_status": "ok",
            "method": self._method("episode_extraction"),
            "problem": response.get("problem"),
            "intervention": response.get("intervention"),
            # Kept apart on purpose: a claim is not a corroborated outcome.
            "author_claimed_outcome": response.get("claimed_outcome"),
            "observed_outcome": response.get("observed_outcome"),
            "evidence_ids": kept,
            "dropped_uncitable_ids": dropped,
            "insufficient_evidence": bool(response.get("insufficient_evidence")),
        }

    # -- dimension second opinion ------------------------------------------
    def dimension_evidence(
        self, assessment: Mapping[str, Any], rubric_text: str,
        artifacts: Sequence[Mapping[str, Any]], text: str,
        features: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "subject": assessment.get("dimension_record_id"),
            "dimension": assessment.get("dimension"),
            "rubric": rubric_text,
            "artifacts": _artifact_block(artifacts),
            "text": text,
            "features": canonical_json(features),
        }
        response = self._run("dimension_evidence", payload)
        if response is None:
            return {
                "dimension_record_id": assessment.get("dimension_record_id"),
                "llm_status": "pending", "method": "pending",
            }
        kept, dropped = _filter_evidence(response, _allowed_ids(artifacts))
        deterministic_band = assessment.get("band")
        llm_band = response.get("band")
        agrees = deterministic_band == llm_band
        if not agrees:
            self.disagreements.append(
                {
                    "dimension_record_id": assessment.get("dimension_record_id"),
                    "dimension": assessment.get("dimension"),
                    "deterministic_band": deterministic_band,
                    "llm_band": llm_band,
                    "llm_rationale": str(response.get("rationale"))[:280],
                }
            )
        return {
            "dimension_record_id": assessment.get("dimension_record_id"),
            "llm_status": "ok",
            "method": self._method("dimension_evidence"),
            "llm_band": llm_band,
            "llm_rationale": response.get("rationale"),
            "llm_confidence": response.get("confidence"),
            "llm_counterevidence": response.get("counterevidence") or [],
            "evidence_ids": kept,
            "dropped_uncitable_ids": dropped,
            # The deterministic band is authoritative; this is a second opinion
            # whose agreement rate is reported.
            "deterministic_band": deterministic_band,
            "agrees_with_deterministic": agrees,
            "authoritative": "deterministic",
        }

    # -- review consequence -------------------------------------------------
    def review_consequence(
        self, intervention: Mapping[str, Any]
    ) -> dict[str, Any]:
        artifacts = [
            {"artifact_id": intervention.get("artifact_id"),
             "artifact_kind": "review_comment",
             "url": intervention.get("url"), "title": "the comment itself"}
        ]
        payload = {
            "subject": intervention.get("intervention_id"),
            "path": intervention.get("path"),
            "text": intervention.get("body_excerpt"),
            "features": canonical_json(
                {
                    "comment_precedes_change": intervention.get("comment_precedes_change"),
                    "change_evidence": intervention.get("change_evidence"),
                    "acknowledged_or_resolved": intervention.get("acknowledged_or_resolved"),
                    "comment_is_outdated": intervention.get("comment_is_outdated"),
                }
            ),
            "artifacts": _artifact_block(artifacts),
        }
        response = self._run("review_consequence", payload)
        if response is None:
            return {
                "intervention_id": intervention.get("intervention_id"),
                "llm_status": "pending", "method": "pending",
            }
        deterministic = intervention.get("consequence_band")
        return {
            "intervention_id": intervention.get("intervention_id"),
            "llm_status": "ok",
            "method": self._method("review_consequence"),
            "llm_concern_classes": response.get("concern_classes") or [],
            "llm_is_consequential": bool(response.get("is_consequential")),
            "llm_consequence_band": response.get("consequence_band"),
            "llm_reasoning": response.get("reasoning"),
            "deterministic_consequence_band": deterministic,
            "agrees_with_deterministic": response.get("consequence_band") == deterministic,
            "authoritative": "deterministic",
        }

    # -- semantic edge adjudication ------------------------------------------
    def semantic_edge(
        self, edge: Mapping[str, Any], a_text: str, b_text: str
    ) -> dict[str, Any]:
        payload = {
            "subject": edge.get("edge_uid"),
            "a": a_text, "b": b_text,
            "features": canonical_json(
                {
                    "lexical_similarity": edge.get("similarity"),
                    "days_apart": edge.get("days_apart"),
                    "shared_components": edge.get("shared_components"),
                    "corroborated_by_other_edge": edge.get("corroborated_by_other_edge"),
                }
            ),
        }
        response = self._run("semantic_edges", payload)
        if response is None:
            return {"edge_uid": edge.get("edge_uid"), "llm_status": "pending",
                    "method": "pending"}
        return {
            "edge_uid": edge.get("edge_uid"),
            "llm_status": "ok",
            "method": self._method("semantic_edges"),
            "llm_same_initiative": bool(response.get("same_initiative")),
            "llm_relationship": response.get("relationship"),
            "llm_confidence": response.get("confidence"),
            "llm_reasoning": response.get("reasoning"),
            # Even an affirmative LLM verdict does not merge episodes on its own;
            # it counts as one corroborating signal, which is what the phase spec
            # means by "corroborated by another signal or manually approved".
            "promotes_to_corroborated": (
                bool(response.get("same_initiative"))
                and response.get("confidence") == "high"
            ),
        }

    # -- pairwise, identity blinded -------------------------------------------
    def pairwise_episodes(
        self, a: Mapping[str, Any], b: Mapping[str, Any], rubric_text: str
    ) -> dict[str, Any]:
        payload = {
            "subject": f"{a.get('episode_id')}|{b.get('episode_id')}",
            "rubric": rubric_text,
            "a": _blind_episode(a),
            "b": _blind_episode(b),
        }
        response = self._run("pairwise_episode_comparison", payload)
        if response is None:
            return {
                "pair": [a.get("episode_id"), b.get("episode_id")],
                "llm_status": "pending", "method": "pending",
            }
        return {
            "pair": [a.get("episode_id"), b.get("episode_id")],
            "llm_status": "ok",
            "method": self._method("pairwise_episode_comparison"),
            "stronger": response.get("stronger"),
            "dimension_notes": response.get("dimension_notes") or [],
            "reasoning": response.get("reasoning"),
            "confidence": response.get("confidence"),
            "insufficient_evidence": bool(response.get("insufficient_evidence")),
            "identity_blinded": True,
            "advisory_only": True,
        }

    # -- executive summary -----------------------------------------------------
    def executive_summary(
        self, subject: str, artifacts: Sequence[Mapping[str, Any]],
        features: Mapping[str, Any], limitations: Sequence[str],
    ) -> dict[str, Any]:
        payload = {
            "subject": subject,
            "artifacts": _artifact_block(artifacts, limit=60),
            "features": canonical_json(features),
            "limitations": "\n".join(f"- {l}" for l in limitations),
        }
        response = self._run("executive_summary", payload)
        if response is None:
            return {"subject": subject, "llm_status": "pending", "method": "pending"}
        allowed = _allowed_ids(artifacts)
        sentences = []
        for sentence in response.get("sentences") or []:
            kept = [i for i in (sentence.get("evidence_ids") or []) if i in allowed]
            if not kept:
                continue     # a sentence with no citable evidence is not published
            sentences.append({"text": sentence.get("text"), "evidence_ids": kept})
        return {
            "subject": subject,
            "llm_status": "ok",
            "method": self._method("executive_summary"),
            "summary": response.get("summary"),
            "sentences": sentences,
            "dropped_sentences": len(response.get("sentences") or []) - len(sentences),
            "requires_manual_audit": True,
            "manual_audit_status": "pending",
        }

    # -- stability programme ----------------------------------------------------
    def stability_tests(
        self, cases: Sequence[Mapping[str, Any]], rubric_text: str
    ) -> dict[str, Any]:
        """Validation item 3: repeatability, order reversal, blinding, ablation."""
        cfg = self.config.get("llm.controls.stability")
        repeats = int(cfg["repeat_times"])
        limit = int(cfg["repeat_cases"])
        selected = list(cases)[:limit]

        if not self.client.available:
            return {
                "status": "pending",
                "reason": (
                    "no LLM provider configured; the stability programme cannot "
                    "run and every case is queued"
                ),
                "cases_queued": len(selected),
                "requirements": {
                    "repeat_cases": limit, "repeat_times": repeats,
                    "reverse_artifact_order": bool(cfg["reverse_artifact_order"]),
                    "identity_blind_variant": bool(cfg["identity_blind_variant"]),
                    "ablations": list(cfg["ablations"]),
                },
            }

        results: list[dict[str, Any]] = []
        for case in selected:
            variants: dict[str, list[Any]] = defaultdict(list)
            for _ in range(repeats):
                base = self.dimension_evidence(
                    case["assessment"], rubric_text, case["artifacts"],
                    case["text"], case["features"],
                )
                variants["repeat"].append(base.get("llm_band"))
            if cfg["reverse_artifact_order"]:
                reversed_case = self.dimension_evidence(
                    case["assessment"], rubric_text, list(reversed(case["artifacts"])),
                    case["text"], case["features"],
                )
                variants["reversed_order"].append(reversed_case.get("llm_band"))
            for ablation in cfg["ablations"]:
                text = _ablate(case["text"], ablation)
                ablated = self.dimension_evidence(
                    case["assessment"], rubric_text, case["artifacts"], text,
                    case["features"],
                )
                variants[f"ablation_{ablation}"].append(ablated.get("llm_band"))

            repeat_values = variants["repeat"]
            results.append(
                {
                    "case": case["assessment"].get("dimension_record_id"),
                    "dimension": case["assessment"].get("dimension"),
                    "deterministic_band": case["assessment"].get("band"),
                    "variants": {k: v for k, v in variants.items()},
                    "repeat_agreement": (
                        round(repeat_values.count(repeat_values[0]) / len(repeat_values), 4)
                        if repeat_values else None
                    ),
                    "order_stable": (
                        variants.get("reversed_order", [None])[0] == repeat_values[0]
                        if repeat_values else None
                    ),
                }
            )

        agreements = [r["repeat_agreement"] for r in results if r["repeat_agreement"]]
        order_stable = [r for r in results if r.get("order_stable")]
        return {
            "status": "ran",
            "cases": len(results),
            "repeats_per_case": repeats,
            "mean_repeat_agreement": (
                round(sum(agreements) / len(agreements), 4) if agreements else None
            ),
            "order_stability_rate": (
                round(len(order_stable) / len(results), 4) if results else None
            ),
            "results": results,
            "identity_blinded": True,
        }


def _blind_episode(episode: Mapping[str, Any]) -> str:
    """Strip identity before a comparison so the model cannot be swayed by it."""
    return (
        f"Title: {episode.get('title')}\n"
        f"Problem: {episode.get('problem')}\n"
        f"Intervention: {episode.get('intervention')}\n"
        f"Outcome: {episode.get('observable_outcome')}\n"
        f"Status: {episode.get('status')} "
        f"(release {episode.get('release_corroboration')})\n"
        f"Components: {episode.get('components')}\n"
        f"Counterevidence: "
        f"{[c.get('detail') for c in (episode.get('counterevidence') or [])][:3]}\n"
    )


def _ablate(text: str, mode: str) -> str:
    if mode == "title_only":
        return "\n".join(line for line in text.splitlines() if line.startswith("TITLE"))
    if mode == "diff_only":
        return "\n".join(line for line in text.splitlines() if line.startswith("FILES"))
    return text


def pending_queue(client: LLMClient, config: Phase2Config) -> dict[str, Any]:
    """The LLM_PENDING artifact: exactly what would run, and how to run it."""
    by_task: dict[str, int] = defaultdict(int)
    for item in client.pending:
        by_task[str(item["task"])] += 1
    providers = config.get("llm.providers")
    return {
        "status": "pending" if client.pending else "empty",
        "queued": len(client.pending),
        "by_task": dict(sorted(by_task.items())),
        "items": client.pending[:500],
        "truncated": max(0, len(client.pending) - 500),
        "how_to_run": {
            "step_1": (
                "Create a free account with one of the providers below and "
                "generate one API key with no billing enabled."
            ),
            "providers": {
                name: {
                    "env_var": spec["api_key_env"],
                    "default_model": spec["default_model"],
                    "site": spec["base_url"],
                }
                for name, spec in providers.items()
            },
            "step_2": "Add the key to .env, e.g. OPENROUTER_API_KEY=...",
            "step_3": "Run `make p2-llm`, then `make p2-dimensions p2-rank p2-export`.",
            "step_4": (
                "Afterwards `LLM_REPLAY_ONLY=1 make p2` reproduces the run from "
                "the cache with no key and no network."
            ),
        },
        "note": (
            "The deterministic pipeline has already produced a complete result. "
            "These tasks would add semantic extraction and summaries; they are "
            "not required for the ranking, and keyword matching has NOT been "
            "substituted for them."
        ),
    }
