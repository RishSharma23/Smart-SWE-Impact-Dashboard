"""The claim registry: every dashboard sentence, and what it rests on.

The rule the phase spec sets is absolute — *every dashboard sentence linked to
supporting artifact IDs and URLs; no untraceable prose* — and the only reliable
way to satisfy it is to make prose impossible to produce any other way.  So the
UI never receives a free-text string.  It receives claim records:

    {claim_id, text, claim_type, subject, evidence[], derivation, confidence}

``claim_id`` is content-addressed over the text, the subject and the sorted
evidence IDs, so a reader who files "claim/8f3a… is wrong" is pointing at
exactly one sentence resting on exactly one evidence set.  Change the sentence
or the evidence and the ID changes, which is the property a correction pathway
needs.

The orphan check in the validation program is therefore trivially strong: it
walks the exported package, collects every human-readable string, and asserts
each one is either a claim with evidence or a field explicitly registered as
non-claim chrome (labels, headings, enum values).  Anything else fails the gate.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from .ids import claim_id
from .versions import derivation_version

log = logging.getLogger("impact2.claims")

VERSION = derivation_version("claims")

CLAIM_TYPES = (
    "episode_narrative",     # what an episode was and did
    "dimension_band",        # why an episode scored a band on a dimension
    "attribution",           # why a person is credited with a role/share
    "portfolio",             # what an engineer's portfolio shows
    "ranking",               # why one engineer outranks another
    "stability",             # how stable a ranking position is
    "limitation",            # what this measurement cannot see
    "counterevidence",       # what argues against a claim
)


class ClaimRegistry:
    """Accumulates claims and refuses the ones that cannot be traced."""

    def __init__(self, *, repo_url: str) -> None:
        self.repo_url = repo_url
        self._claims: dict[str, dict[str, Any]] = {}
        self.rejected: list[dict[str, Any]] = []

    def add(
        self,
        *,
        text: str,
        claim_type: str,
        subject: str,
        evidence: Sequence[Mapping[str, Any]],
        derivation: str,
        confidence: str | None = None,
        allow_without_evidence: bool = False,
    ) -> str | None:
        """Register a claim. Returns its ID, or None if it was rejected.

        A claim with no evidence is rejected rather than published, except for
        the explicitly-marked classes that describe the *method* rather than the
        data (limitations, methodology notes) — those carry ``derivation``
        pointing at the config or rubric that produced them, which is their
        evidence.
        """
        text = " ".join(str(text).split())
        if not text:
            return None
        if claim_type not in CLAIM_TYPES:
            raise ValueError(f"unknown claim_type {claim_type!r}")

        cleaned = [
            {
                "artifact_id": e.get("artifact_id"),
                "url": e.get("url"),
                "kind": e.get("kind"),
                "detail": (str(e.get("detail"))[:280] if e.get("detail") else None),
            }
            for e in evidence
            if e and (e.get("artifact_id") or e.get("url"))
        ]
        if not cleaned and not allow_without_evidence:
            self.rejected.append(
                {"text": text[:200], "claim_type": claim_type, "subject": subject,
                 "reason": "no resolvable artifact ID or URL"}
            )
            return None

        identifier = claim_id(text, subject, [str(e["artifact_id"]) for e in cleaned])
        self._claims[identifier] = {
            "claim_id": identifier,
            "text": text,
            "claim_type": claim_type,
            "subject": subject,
            "evidence": cleaned,
            "evidence_count": len(cleaned),
            "evidence_is_methodological": not cleaned,
            "derivation": derivation,
            "confidence": confidence,
            "claims_version": VERSION,
        }
        return identifier

    # -- bulk builders ----------------------------------------------------
    def from_episode(self, episode: Mapping[str, Any]) -> dict[str, str | None]:
        """Claim IDs for an episode's four narrative fields."""
        eid = str(episode["episode_id"])
        pr_evidence = [
            {"artifact_id": f"pr/{n}", "url": f"{self.repo_url}/pull/{n}",
             "kind": "pull_request"}
            for n in (episode.get("pr_numbers") or [])[:6]
        ]
        issue_evidence = [
            {"artifact_id": f"issue/{n}", "url": f"{self.repo_url}/issues/{n}",
             "kind": "issue"}
            for n in (episode.get("issue_numbers") or [])[:4]
        ]
        return {
            "title": self.add(
                text=str(episode.get("title") or ""),
                claim_type="episode_narrative", subject=eid,
                evidence=issue_evidence + pr_evidence[:1],
                derivation="episodes.build:title from linked issue or anchor PR",
                confidence=_confidence_word(episode.get("cluster_confidence")),
            ),
            "problem": self.add(
                text=str(episode.get("problem") or ""),
                claim_type="episode_narrative", subject=eid,
                evidence=issue_evidence or pr_evidence[:1],
                derivation="episodes.build:_problem_statement",
                confidence=_confidence_word(episode.get("cluster_confidence")),
            ),
            "intervention": self.add(
                text=str(episode.get("intervention") or ""),
                claim_type="episode_narrative", subject=eid,
                evidence=pr_evidence,
                derivation="episodes.build:_intervention from the merge-commit diff",
                confidence=_confidence_word(episode.get("cluster_confidence")),
            ),
            "observable_outcome": self.add(
                text=str(episode.get("observable_outcome") or ""),
                claim_type="episode_narrative", subject=eid,
                evidence=pr_evidence + [
                    {"artifact_id": f"{eid}/status", "kind": "status_evidence",
                     "detail": str(e.get("detail"))}
                    for e in (episode.get("release_evidence") or [])
                ],
                derivation="episodes.status:classify",
                confidence=str(episode.get("release_corroboration")),
            ),
        }

    def from_dimension(self, assessment: Mapping[str, Any]) -> str | None:
        evidence = [
            {
                "artifact_id": e.get("artifact_id") or f"{assessment['episode_id']}/{e.get('kind')}",
                "url": e.get("url"),
                "kind": e.get("kind"),
                "detail": e.get("detail"),
            }
            for e in (assessment.get("evidence") or [])
        ]
        return self.add(
            text=str(assessment.get("rationale") or ""),
            claim_type="dimension_band",
            subject=str(assessment["dimension_record_id"]),
            evidence=evidence,
            derivation=(
                f"dimensions.rubric:{assessment['dimension']} "
                f"(rubric {assessment.get('rubric_version')})"
            ),
            confidence=str(assessment.get("confidence")),
        )

    def from_participant(self, participant: Mapping[str, Any]) -> list[str]:
        out: list[str] = []
        subject = str(participant["participant_id"])
        for role, items in (participant.get("role_evidence") or {}).items():
            for item in items[:2]:
                identifier = self.add(
                    text=(
                        f"{participant.get('login') or participant.get('actor_cluster_id')} "
                        f"acted as {role.replace('_', ' ')}: {item.get('detail')}"
                    ),
                    claim_type="attribution", subject=subject,
                    evidence=[{"artifact_id": item.get("artifact_id"),
                               "url": item.get("url"), "kind": "role_evidence",
                               "detail": item.get("detail")}],
                    derivation="episodes.participants:infer_roles",
                    confidence=str(participant.get("attribution_confidence")),
                )
                if identifier:
                    out.append(identifier)
        share = self.add(
            text=(
                f"Shared credit is recorded as '{participant.get('share_category')}' "
                f"because {'; '.join(participant.get('share_reasons') or [])}."
            ),
            claim_type="attribution", subject=subject,
            evidence=[
                {"artifact_id": e.get("artifact_id"), "url": e.get("url"),
                 "kind": "role_evidence", "detail": e.get("detail")}
                for e in (participant.get("direct_evidence") or [])[:3]
            ],
            derivation="episodes.participants:_share_category",
            confidence=str(participant.get("attribution_confidence")),
        )
        if share:
            out.append(share)
        return out

    def from_portfolio(self, portfolio: Mapping[str, Any]) -> list[str]:
        out: list[str] = []
        subject = str(portfolio["portfolio_id"])
        name = portfolio.get("login") or portfolio.get("actor_cluster_id")
        for dimension, detail in (portfolio.get("dimension_detail") or {}).items():
            if detail.get("is_unknown"):
                identifier = self.add(
                    text=(
                        f"{name} has no assessable {dimension.replace('_', ' ')} "
                        f"evidence: {detail.get('unknown_reason')}. This is a gap in "
                        "the data, not a low score."
                    ),
                    claim_type="portfolio", subject=subject,
                    evidence=[], derivation="portfolio.build:dimension_value",
                    confidence="unknown", allow_without_evidence=True,
                )
            else:
                entries = detail.get("entries") or []
                identifier = self.add(
                    text=(
                        f"{name}'s {dimension.replace('_', ' ')} evidence is carried by "
                        f"'{(entries[0] or {}).get('episode_title')}' "
                        f"(band {(entries[0] or {}).get('band')}, "
                        f"{(entries[0] or {}).get('share_category')} credit)"
                        + (f" with {len(entries) - 1} further corroborating episode(s)."
                           if len(entries) > 1 else ".")
                    ),
                    claim_type="portfolio", subject=subject,
                    evidence=[
                        {"artifact_id": e.get("episode_id"), "kind": "episode",
                         "detail": e.get("rationale")}
                        for e in entries[:4]
                    ],
                    derivation=(
                        "portfolio.build:aggregate_ordered "
                        f"(coefficients, headroom cap)"
                    ),
                    confidence=str(detail.get("confidence")),
                )
            if identifier:
                out.append(identifier)
        if not portfolio.get("rankable"):
            identifier = self.add(
                text=(
                    f"{name} is labelled '{portfolio.get('eligibility_label')}': "
                    + "; ".join(portfolio.get("eligibility_reasons") or [])
                    + ". This describes the available evidence, not the engineer."
                ),
                claim_type="limitation", subject=subject, evidence=[],
                derivation="portfolio.build:_eligibility",
                confidence="high", allow_without_evidence=True,
            )
            if identifier:
                out.append(identifier)
        return out

    def from_comparison(
        self, comparison: Mapping[str, Any], *, scenario: str
    ) -> str | None:
        return self.add(
            text=str(comparison.get("explanation") or ""),
            claim_type="ranking",
            subject=f"{scenario}:{comparison.get('a')}vs{comparison.get('b')}",
            evidence=[
                {"artifact_id": f"criterion/{c['criterion']}", "kind": "criterion",
                 "detail": (
                     f"a={c['a_value']} b={c['b_value']} w={c['weight']} "
                     f"concordance={c['concordance']} discordance={c['discordance']}"
                 )}
                for c in (comparison.get("per_criterion") or [])
            ],
            derivation=f"rank.outranking:ELECTRE III, scenario '{scenario}'",
            confidence=None,
        )

    def from_stability(
        self, record: Mapping[str, Any], *, login: str | None
    ) -> str | None:
        probability = record.get("mean_top5_inclusion_probability")
        if probability is None:
            return None
        return self.add(
            text=(
                f"{login or record.get('actor_cluster_id')} appears in the top five "
                f"in {probability:.0%} of resampled and reweighted configurations "
                f"(position range {record.get('position_range')})."
            ),
            claim_type="stability", subject=str(record["actor_cluster_id"]),
            evidence=[
                {"artifact_id": f"analysis/{name}", "kind": "stability_analysis",
                 "detail": f"mean position {row.get('mean_position')}, "
                           f"stability {row.get('rank_stability_index')}"}
                for name, row in (record.get("per_analysis") or {}).items()
            ],
            derivation="rank.sensitivity:bootstrap + weight + structural variation",
            confidence=None,
        )

    def limitations(self, items: Sequence[str], *, derivation: str) -> list[str]:
        out: list[str] = []
        for item in items:
            identifier = self.add(
                text=str(item), claim_type="limitation", subject="dashboard",
                evidence=[], derivation=derivation, confidence="high",
                allow_without_evidence=True,
            )
            if identifier:
                out.append(identifier)
        return out

    # -- output ------------------------------------------------------------
    def all(self) -> list[dict[str, Any]]:
        return sorted(self._claims.values(), key=lambda c: c["claim_id"])

    def get(self, identifier: str) -> dict[str, Any] | None:
        return self._claims.get(identifier)

    def summarise(self) -> dict[str, Any]:
        by_type: dict[str, int] = defaultdict(int)
        without_evidence = 0
        for claim in self._claims.values():
            by_type[str(claim["claim_type"])] += 1
            if not claim["evidence"]:
                without_evidence += 1
        return {
            "claims": len(self._claims),
            "by_type": dict(sorted(by_type.items())),
            "methodological_claims_without_artifact_evidence": without_evidence,
            "rejected_for_lack_of_evidence": len(self.rejected),
            "rejected_examples": self.rejected[:10],
            "claims_version": VERSION,
        }


def _confidence_word(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if number >= 0.8:
        return "high"
    if number >= 0.55:
        return "medium"
    return "low"


def audit(
    claims: Sequence[Mapping[str, Any]], referenced_ids: Iterable[str]
) -> dict[str, Any]:
    """Validation item 9: zero orphan claims, and zero dangling references."""
    known = {str(c["claim_id"]) for c in claims}
    referenced = {str(r) for r in referenced_ids if r}
    orphan_claims = [
        c["claim_id"] for c in claims
        if not c["evidence"] and c["claim_type"] not in {"limitation", "portfolio"}
    ]
    dangling = sorted(referenced - known)
    unreferenced = sorted(known - referenced)
    return {
        "claims": len(known),
        "referenced_by_export": len(referenced),
        "orphan_claims": orphan_claims,
        "orphan_claim_count": len(orphan_claims),
        "dangling_references": dangling[:20],
        "dangling_reference_count": len(dangling),
        "unreferenced_claims": len(unreferenced),
        "status": "pass" if not orphan_claims and not dangling else "fail",
        "claims_version": VERSION,
    }
