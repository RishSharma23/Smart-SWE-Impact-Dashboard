"""Review-intervention candidates.

The question this answers is "did a review comment *change* something", not
"how many reviews did someone leave".  Four independent pieces of evidence are
recorded per comment, and the original text is always retained:

1. **Substance** -- is this an approval, an acknowledgement, a nit, a bot
   message, or an actual argument?  Classified with explicit patterns and a
   length floor, with the matched pattern kept.
2. **Consequence** -- was the referenced file touched by a commit that landed
   *after* the comment?  This is the closest deterministic proxy for "the
   author acted on it" available without reading the diff semantically.
3. **Acknowledgement** -- did the author reply affirmatively, or was the thread
   resolved, and by whom?
4. **Safety vocabulary** -- does the comment name a class of risk (data loss,
   migration, security, privacy, performance, ...)?

Deliberately *not* done here: deciding whether the intervention was correct or
valuable.  The spec puts semantic consequence classification in Phase 2; this
module's job is to hand Phase 2 a small, well-evidenced candidate set instead
of every comment in the repository.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable, Mapping

from ..config import parse_ts
from ..versions import feature_version

BOT_SUFFIX = "[bot]"


def _compile(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in patterns]


class ReviewClassifier:
    def __init__(self, params: Mapping[str, Any]) -> None:
        self.short_chars = int(params.get("short_comment_chars", 40))
        self.follow_hours = int(params.get("change_follow_hours", 336))
        self.ack_patterns = _compile(params.get("acknowledgement_patterns") or [])
        self.nit_patterns = _compile(params.get("nit_patterns") or [])
        self.vocabulary = {
            str(name): [str(t).lower() for t in terms]
            for name, terms in (params.get("safety_vocabulary") or {}).items()
        }

    # -- substance -------------------------------------------------------

    def classify_comment(
        self, body: str, *, author_login: str | None, author_is_bot: bool
    ) -> dict[str, Any]:
        text = (body or "").strip()
        stripped = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
        stripped = re.sub(r"<!--.*?-->", " ", stripped, flags=re.DOTALL).strip()

        reasons: list[str] = []
        if author_is_bot or (author_login or "").endswith(BOT_SUFFIX):
            return {
                "substance_class": "bot",
                "is_substantive": False,
                "substance_reasons": ["author is a bot account"],
                "matched_nit_pattern": None,
                "matched_ack_pattern": None,
            }

        if not stripped:
            return {
                "substance_class": "empty",
                "is_substantive": False,
                "substance_reasons": ["comment body is empty after stripping code/HTML"],
                "matched_nit_pattern": None,
                "matched_ack_pattern": None,
            }

        nit_hit = next((p.pattern for p in self.nit_patterns if p.search(stripped)), None)
        ack_hit = next((p.pattern for p in self.ack_patterns if p.search(stripped)), None)

        if ack_hit and len(stripped) <= self.short_chars:
            return {
                "substance_class": "acknowledgement",
                "is_substantive": False,
                "substance_reasons": [f"short acknowledgement (<= {self.short_chars} chars)"],
                "matched_nit_pattern": nit_hit,
                "matched_ack_pattern": ack_hit,
            }
        if nit_hit:
            reasons.append("explicit nit/style marker")
            return {
                "substance_class": "nit",
                "is_substantive": False,
                "substance_reasons": reasons,
                "matched_nit_pattern": nit_hit,
                "matched_ack_pattern": ack_hit,
            }
        if len(stripped) <= self.short_chars:
            return {
                "substance_class": "short",
                "is_substantive": False,
                "substance_reasons": [f"body <= {self.short_chars} chars"],
                "matched_nit_pattern": nit_hit,
                "matched_ack_pattern": ack_hit,
            }

        if "?" in stripped:
            reasons.append("contains a question")
        if re.search(r"\b(should|could|would|instead|prefer|why|consider|suggest)\b",
                     stripped, re.IGNORECASE):
            reasons.append("contains suggestion/argument language")
        if len(stripped) > 200:
            reasons.append("long-form comment (>200 chars)")
        if not reasons:
            reasons.append("substantive by length with no nit/ack marker")

        return {
            "substance_class": "substantive",
            "is_substantive": True,
            "substance_reasons": reasons,
            "matched_nit_pattern": nit_hit,
            "matched_ack_pattern": ack_hit,
        }

    # -- safety vocabulary -----------------------------------------------

    def safety_terms(self, body: str) -> dict[str, list[str]]:
        text = (body or "").lower()
        hits: dict[str, list[str]] = {}
        for category, terms in self.vocabulary.items():
            matched = [t for t in terms if t in text]
            if matched:
                hits[category] = matched
        return hits


def compute_candidates(
    *,
    prs: Mapping[int, Mapping[str, Any]],
    review_comments: Iterable[Mapping[str, Any]],
    threads: Mapping[str, Mapping[str, Any]],
    commits_by_pr: Mapping[int, list[Mapping[str, Any]]],
    files_by_pr: Mapping[int, list[Mapping[str, Any]]],
    actors: Mapping[str, Mapping[str, Any]],
    classifier: ReviewClassifier,
) -> list[dict[str, Any]]:
    """One candidate row per review comment, with all four evidence axes."""
    out: list[dict[str, Any]] = []

    for comment in review_comments:
        pr_number = comment.get("pr_number")
        if pr_number is None:
            continue
        pr = prs.get(int(pr_number))
        if pr is None:
            continue

        author_id = comment.get("author_actor_id")
        actor = actors.get(str(author_id)) if author_id else None
        author_is_bot = bool(actor and actor.get("is_bot"))
        body = comment.get("body_text") or ""

        substance = classifier.classify_comment(
            body, author_login=comment.get("author_login"), author_is_bot=author_is_bot
        )
        safety = classifier.safety_terms(body)

        thread = threads.get(str(comment.get("thread_id") or ""), {})
        comment_at = parse_ts(comment.get("created_at"))

        # -- consequence: did a later commit touch the referenced file?
        path = comment.get("path") or thread.get("path")
        followed_by_change = None
        follow_evidence = None
        if path and comment_at:
            later = [
                c for c in commits_by_pr.get(int(pr_number), [])
                if (parse_ts(c.get("committed_at")) or comment_at) > comment_at
            ]
            if later:
                changed_paths = {
                    str(f.get("path"))
                    for f in files_by_pr.get(int(pr_number), [])
                }
                followed_by_change = path in changed_paths
                follow_evidence = (
                    f"{len(later)} commit(s) after the comment; referenced path "
                    f"{'is' if followed_by_change else 'is not'} in the merged diff"
                )
            else:
                followed_by_change = False
                follow_evidence = "no commit in this PR is dated after the comment"
        elif not path:
            follow_evidence = "comment is not anchored to a file path"

        # PostHog squash-merges, so intra-PR commit history is usually absent
        # from the analysed branch. Thread resolution is the stronger signal.
        is_author_self_comment = (
            comment.get("author_actor_id") == pr.get("author_actor_id")
        )

        out.append(
            {
                "candidate_id": f"{pr_number}:{comment.get('comment_id')}",
                "pr_number": int(pr_number),
                "pr_id": pr.get("pr_id"),
                "comment_id": comment.get("comment_id"),
                "thread_id": comment.get("thread_id"),
                "url": comment.get("url"),
                "commenter_actor_id": author_id,
                "commenter_login": comment.get("author_login"),
                "commenter_is_bot": author_is_bot,
                "pr_author_actor_id": pr.get("author_actor_id"),
                "is_self_comment": is_author_self_comment,
                "created_at": comment.get("created_at"),
                "position_in_thread": comment.get("position_in_thread"),
                "is_thread_opener": comment.get("is_thread_opener"),
                "path": path,
                "component": thread.get("component"),
                "body_length": len(body),
                # Original text retained on purpose (spec: keep text + linkage).
                "body_text": body,
                **substance,
                "safety_categories": sorted(safety),
                "safety_terms_matched": safety,
                "has_safety_vocabulary": bool(safety),
                # thread outcome
                "thread_is_resolved": thread.get("is_resolved"),
                "thread_resolved_by_login": thread.get("resolved_by_login"),
                "thread_resolved_by_author": (
                    thread.get("resolved_by_login") == pr.get("author_login")
                    if thread.get("resolved_by_login") else None
                ),
                "thread_comment_count": thread.get("comment_count"),
                "thread_is_outdated": thread.get("is_outdated"),
                "author_replied_in_thread": (
                    pr.get("author_login") in (thread.get("participant_logins") or [])
                    and not is_author_self_comment
                ),
                # consequence
                "followed_by_change_in_path": followed_by_change,
                "follow_evidence": follow_evidence,
                # A candidate worth Phase 2's attention: substantive, from
                # someone other than the author, on a PR that merged.
                "is_intervention_candidate": bool(
                    substance["is_substantive"]
                    and not is_author_self_comment
                    and not author_is_bot
                ),
                "review_intervention_version": feature_version("review_intervention"),
            }
        )
    return out


def summarise_by_actor(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per-reviewer rollup of intervention evidence (counts, not scores)."""
    by_actor: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row.get("is_intervention_candidate"):
            continue
        key = str(row.get("commenter_actor_id"))
        bucket = by_actor.setdefault(
            key,
            {
                "actor_id": key,
                "login": row.get("commenter_login"),
                "substantive_comments": 0,
                "distinct_prs": set(),
                "distinct_authors_helped": set(),
                "distinct_components": set(),
                "safety_comments": 0,
                "safety_category_counts": {},
                "resolved_threads": 0,
                "followed_by_change": 0,
                "review_intervention_version": feature_version("review_intervention"),
            },
        )
        bucket["substantive_comments"] += 1
        bucket["distinct_prs"].add(row.get("pr_number"))
        if row.get("pr_author_actor_id"):
            bucket["distinct_authors_helped"].add(row["pr_author_actor_id"])
        if row.get("component"):
            bucket["distinct_components"].add(row["component"])
        if row.get("has_safety_vocabulary"):
            bucket["safety_comments"] += 1
            for category in row.get("safety_categories") or []:
                bucket["safety_category_counts"][category] = (
                    bucket["safety_category_counts"].get(category, 0) + 1
                )
        if row.get("thread_is_resolved"):
            bucket["resolved_threads"] += 1
        if row.get("followed_by_change_in_path"):
            bucket["followed_by_change"] += 1

    out: list[dict[str, Any]] = []
    for bucket in by_actor.values():
        out.append(
            {
                **{
                    k: v for k, v in bucket.items()
                    if k not in {"distinct_prs", "distinct_authors_helped", "distinct_components"}
                },
                "distinct_prs": len(bucket["distinct_prs"]),
                "distinct_authors_helped": len(bucket["distinct_authors_helped"]),
                "distinct_components": len(bucket["distinct_components"]),
                "safety_category_counts": dict(sorted(bucket["safety_category_counts"].items())),
            }
        )
    return sorted(out, key=lambda r: (-r["substantive_comments"], str(r["actor_id"])))


def summarise(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    total = len(items) or 1
    classes: dict[str, int] = {}
    for row in items:
        key = str(row.get("substance_class"))
        classes[key] = classes.get(key, 0) + 1
    return {
        "review_comments": len(items),
        "substance_distribution": dict(sorted(classes.items())),
        "intervention_candidates": sum(
            1 for r in items if r.get("is_intervention_candidate")
        ),
        "with_safety_vocabulary": sum(1 for r in items if r.get("has_safety_vocabulary")),
        "substantive_rate": round(
            sum(1 for r in items if r.get("is_substantive")) / total, 4
        ),
        "resolved_threads": sum(1 for r in items if r.get("thread_is_resolved")),
    }
