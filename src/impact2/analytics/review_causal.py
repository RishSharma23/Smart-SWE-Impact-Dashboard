"""E: review causal evidence — did the comment change anything?

A review intervention is worth crediting when three things hold together:

1. the comment **precedes** a corresponding change;
2. the author **acknowledged** it, or the thread **resolved**;
3. the concern is **consequential** — correctness, design, security, data
   integrity, migration safety, scope, API contract — rather than a naming
   preference.

The obstacle, documented in Phase 1 §10.5: PostHog squash-merges, so the
analysed branch has no intra-PR commit history and a literal pre-comment /
post-comment revision diff is usually impossible.  This module does not pretend
otherwise.  It reports ``pre_post_revision_compared: false`` with the reason,
and leans on the two signals that *do* survive squashing:

* GitHub's ``outdated`` flag on a review comment, which means the diff hunk the
  comment was anchored to has since moved — direct evidence the code changed
  after the comment was written;
* thread resolution and author reply, which are timestamps GitHub records
  independently of the merge strategy.

Concern classification is a documented keyword taxonomy.  It is labelled
``deterministic_rule`` on every row, and the optional LLM layer overwrites the
label with ``llm:<model>@<prompt>`` when it runs.  The two are never conflated.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from ..config import Phase2Config, parse_ts
from ..ids import comment_artifact, intervention_id
from ..versions import derivation_version

log = logging.getLogger("impact2.analytics.review_causal")

VERSION = derivation_version("review_causality")

CONSEQUENCE_BANDS = ("none", "local_change", "design_change", "prevented_risk")
CAUSAL_CONFIDENCE = ("high", "medium", "low")


class ReviewCausalityAnalyzer:
    def __init__(
        self,
        config: Phase2Config,
        *,
        prs: Mapping[int, Mapping[str, Any]],
        threads: Mapping[str, Mapping[str, Any]],
        review_comments: Sequence[Mapping[str, Any]],
        files_by_pr: Mapping[int, Sequence[Mapping[str, Any]]],
        actors: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self.config = config
        self.prs = prs
        self.threads = threads
        self.files_by_pr = files_by_pr
        self.actors = actors
        self.comment_by_id = {
            str(c.get("comment_id")): c for c in review_comments if c.get("comment_id")
        }
        taxonomy = config.get("analytics.review_causality.concern_classes")
        self.taxonomy = {
            str(name): [str(t).lower() for t in terms]
            for name, terms in taxonomy.items()
        }
        self.consequential = set(
            config.get("analytics.review_causality.consequential_classes")
        )
        self.outdated_is_evidence = bool(
            config.get("analytics.review_causality.outdated_comment_is_change_evidence")
        )

    # -- concern classification -------------------------------------------
    def classify_concern(self, body: str) -> tuple[list[str], dict[str, list[str]]]:
        text = (body or "").lower()
        # Strip fenced code so a snippet containing the word "security" does not
        # classify the comment as a security concern.
        text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
        hits: dict[str, list[str]] = {}
        for name, terms in self.taxonomy.items():
            matched = [t for t in terms if t in text]
            if matched:
                hits[name] = matched
        return sorted(hits), hits

    # -- causal assessment -------------------------------------------------
    def assess(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        pr_number = int(candidate["pr_number"])
        pr = self.prs.get(pr_number) or {}
        body = str(candidate.get("body_text") or "")
        comment = self.comment_by_id.get(str(candidate.get("comment_id"))) or {}
        thread = self.threads.get(str(candidate.get("thread_id") or "")) or {}

        concerns, matched_terms = self.classify_concern(body)
        consequential = [c for c in concerns if c in self.consequential]

        # -- evidence axes --------------------------------------------------
        comment_at = parse_ts(candidate.get("created_at"))
        merged_at = parse_ts(pr.get("merged_at"))
        precedes_merge = bool(comment_at and merged_at and comment_at < merged_at)

        is_outdated = bool(comment.get("is_outdated"))
        followed_by_change = candidate.get("followed_by_change_in_path")
        resolved = bool(candidate.get("thread_is_resolved"))
        resolved_by_author = candidate.get("thread_resolved_by_author")
        author_replied = bool(candidate.get("author_replied_in_thread"))

        change_evidence: list[str] = []
        if followed_by_change:
            change_evidence.append(
                "the referenced file is present in the merged diff and a commit "
                "in this PR is dated after the comment"
            )
        if is_outdated and self.outdated_is_evidence:
            change_evidence.append(
                "GitHub marks this comment outdated: the code it was anchored to "
                "changed after the comment was written"
            )
        if precedes_merge and not change_evidence:
            change_evidence.append(
                "the comment precedes the merge, but no file-level change could be "
                "attributed to it"
            )

        comment_precedes_change = bool(followed_by_change) or (
            is_outdated and self.outdated_is_evidence
        )
        acknowledged = resolved or author_replied

        # -- causal confidence ------------------------------------------------
        reasons: list[str] = []
        if comment_precedes_change and acknowledged and consequential:
            confidence = "high"
            reasons.append("change followed the comment, it was acknowledged or "
                           "resolved, and the concern is consequential")
        elif comment_precedes_change and consequential:
            confidence = "medium"
            reasons.append("change followed the comment and the concern is "
                           "consequential, but there is no acknowledgement")
        elif consequential and acknowledged:
            confidence = "low"
            reasons.append("consequential concern that was acknowledged, but no "
                           "code change could be attributed to it")
        else:
            confidence = "low"
            reasons.append(
                "no consequential concern class matched" if not consequential
                else "no change or acknowledgement evidence"
            )

        # -- consequence band --------------------------------------------------
        touched_components = {
            str(f.get("component"))
            for f in self.files_by_pr.get(pr_number, [])
            if f.get("component")
        }
        safety_classes = {"security", "privacy", "data_integrity", "migration_safety"}
        if not comment_precedes_change:
            band = "none"
        elif set(consequential) & safety_classes:
            band = "prevented_risk"
        elif {"design_architecture", "alternative_approach", "scope"} & set(consequential) \
                and len(touched_components) > 1:
            band = "design_change"
        elif consequential:
            band = "local_change"
        else:
            band = "none"

        return {
            "intervention_id": intervention_id(str(candidate.get("candidate_id"))),
            "candidate_id": candidate.get("candidate_id"),
            "artifact_id": comment_artifact(str(candidate.get("comment_id"))),
            "pr_number": pr_number,
            "thread_id": candidate.get("thread_id"),
            "url": candidate.get("url"),
            "commenter_actor_id": candidate.get("commenter_actor_id"),
            "pr_author_actor_id": candidate.get("pr_author_actor_id"),
            "created_at": candidate.get("created_at"),
            "path": candidate.get("path"),
            "component": candidate.get("component"),
            # concern
            "concern_classes": concerns,
            "consequential_classes": consequential,
            "concern_terms_matched": matched_terms,
            "concern_method": "deterministic_rule",
            "concern_taxonomy_version": VERSION,
            # causality
            "comment_precedes_change": comment_precedes_change,
            "change_evidence": change_evidence,
            "acknowledged_or_resolved": acknowledged,
            "thread_is_resolved": resolved,
            "thread_resolved_by_author": resolved_by_author,
            "author_replied_in_thread": author_replied,
            "comment_is_outdated": is_outdated,
            "pre_post_revision_compared": False,
            "pre_post_unavailable_reason": str(
                self.config.get("analytics.review_causality.pre_post_unavailable_reason")
            ).strip(),
            "causal_confidence": confidence,
            "causal_reasons": reasons,
            "consequence_band": band,
            "is_consequential": bool(consequential) and band != "none",
            # The text the claim rests on, retained so any assertion can be shown.
            "body_excerpt": body[:600],
            "review_causality_version": VERSION,
        }

    def analyse_all(
        self, candidates: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for candidate in candidates:
            if not candidate.get("is_intervention_candidate"):
                continue
            out.append(self.assess(candidate))
        log.info(
            "review causality: %d interventions assessed, %d consequential "
            "(%d high confidence)",
            len(out),
            sum(1 for r in out if r["is_consequential"]),
            sum(1 for r in out if r["causal_confidence"] == "high"),
        )
        return out


def by_episode(
    interventions: Sequence[Mapping[str, Any]],
    episode_prs: Mapping[str, Sequence[int]],
) -> dict[str, list[dict[str, Any]]]:
    pr_to_episode: dict[int, str] = {}
    for episode, numbers in episode_prs.items():
        for number in numbers:
            pr_to_episode[int(number)] = episode
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in interventions:
        episode = pr_to_episode.get(int(row["pr_number"]))
        if episode:
            out[episode].append(dict(row))
    return out


def summarise(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    by_band: dict[str, int] = defaultdict(int)
    by_confidence: dict[str, int] = defaultdict(int)
    by_concern: dict[str, int] = defaultdict(int)
    for row in items:
        by_band[str(row.get("consequence_band"))] += 1
        by_confidence[str(row.get("causal_confidence"))] += 1
        for concern in row.get("concern_classes") or []:
            by_concern[str(concern)] += 1
    return {
        "interventions_assessed": len(items),
        "consequential": sum(1 for r in items if r.get("is_consequential")),
        "by_consequence_band": dict(sorted(by_band.items())),
        "by_causal_confidence": dict(sorted(by_confidence.items())),
        "by_concern_class": dict(sorted(by_concern.items())),
        "outdated_comment_evidence_used": sum(
            1 for r in items if r.get("comment_is_outdated")
        ),
        "pre_post_revision_compared": 0,
        "concern_method": "deterministic_rule",
        "review_causality_version": VERSION,
    }
