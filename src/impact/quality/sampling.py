"""Stratified sampling for the manual-audit gates.

The spec requires at least 30 stratified PRs, 10 regression candidates and 10
review-intervention candidates to be inspected by a human.  This module builds
those samples *deterministically* -- selection is by a stable hash of the PR
number, not by ``random`` -- so the same run always produces the same sample
and an auditor's findings stay attached to the right rows.

Each sample row carries the URL and the evidence a human needs, so auditing
means opening a link and answering one question, not reverse-engineering the
pipeline.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Iterable, Mapping

# Strata the spec names explicitly.
PR_STRATA: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], bool]] = {
    "feat": lambda pr, sh: pr.get("title_prefix") == "feat",
    "fix": lambda pr, sh: pr.get("title_prefix") == "fix",
    "chore": lambda pr, sh: pr.get("title_prefix") == "chore",
    "docs": lambda pr, sh: pr.get("title_prefix") == "docs"
    or bool(sh.get("files_docs")) and not sh.get("files_product_code"),
    "large": lambda pr, sh: (sh.get("file_count") or 0) >= 30,
    "small": lambda pr, sh: 0 < (sh.get("file_count") or 0) <= 2,
    "bot": lambda pr, sh: bool(pr.get("author_is_bot")),
    "generated": lambda pr, sh: (sh.get("generated_or_mechanical_file_count") or 0) > 0
    and (sh.get("code_file_count") or 0) == 0,
    "migration": lambda pr, sh: bool(sh.get("touches_migration")),
    "cross_product": lambda pr, sh: (sh.get("distinct_components") or 0) >= 3,
}


def _stable_rank(value: Any, salt: str = "") -> str:
    return hashlib.sha256(f"{salt}|{value}".encode()).hexdigest()


def stratified_prs(
    prs: Iterable[Mapping[str, Any]],
    shapes: Mapping[int, Mapping[str, Any]],
    blast: Mapping[int, Mapping[str, Any]],
    *,
    per_stratum: int = 3,
    minimum_total: int = 30,
) -> list[dict[str, Any]]:
    pool = [p for p in prs if p.get("ranking_eligible")]
    chosen: dict[int, dict[str, Any]] = {}

    for name, predicate in PR_STRATA.items():
        matches = [
            p for p in pool
            if predicate(p, shapes.get(int(p["pr_number"])) or {})
        ]
        matches.sort(key=lambda p: _stable_rank(p["pr_number"], name))
        for pr in matches[:per_stratum]:
            number = int(pr["pr_number"])
            entry = chosen.setdefault(number, _audit_row(pr, shapes, blast))
            entry["strata"].append(name)

    # Top up deterministically if the strata did not reach the required count.
    if len(chosen) < minimum_total:
        remainder = sorted(
            (p for p in pool if int(p["pr_number"]) not in chosen),
            key=lambda p: _stable_rank(p["pr_number"], "topup"),
        )
        for pr in remainder[: minimum_total - len(chosen)]:
            entry = _audit_row(pr, shapes, blast)
            entry["strata"].append("topup")
            chosen[int(pr["pr_number"])] = entry

    return sorted(chosen.values(), key=lambda r: r["pr_number"])


def _audit_row(
    pr: Mapping[str, Any],
    shapes: Mapping[int, Mapping[str, Any]],
    blast: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    number = int(pr["pr_number"])
    shape = shapes.get(number) or {}
    radius = blast.get(number) or {}
    return {
        "pr_number": number,
        "url": pr.get("url"),
        "title": pr.get("title_raw"),
        "author_login": pr.get("author_login"),
        "author_is_bot": pr.get("author_is_bot"),
        "merged_at": pr.get("merged_at"),
        "strata": [],
        # What the pipeline concluded, so a human can agree or disagree.
        "claim_title_prefix": pr.get("title_prefix"),
        "claim_title_corroborated": shape.get("title_claim_corroborated"),
        "claim_dominant_component": shape.get("dominant_component"),
        "claim_reachability_band": radius.get("reachability_band"),
        "claim_distinct_components": shape.get("distinct_components"),
        "claim_touches": [
            k.replace("touches_", "")
            for k, v in shape.items()
            if k.startswith("touches_") and v
        ],
        "file_count": shape.get("file_count"),
        "code_file_count": shape.get("code_file_count"),
        # Filled in by a human.
        "audit_verdict": None,        # correct | incorrect | partially_correct
        "audit_notes": None,
    }


def regression_candidates(
    rows: Iterable[Mapping[str, Any]],
    prs: Mapping[int, Mapping[str, Any]],
    *,
    minimum: int = 10,
) -> list[dict[str, Any]]:
    """Sample across evidence tiers, weighted toward the weakest tier.

    The point of this audit is to quantify false positives, so the sample must
    over-represent ``proximate`` -- the tier most likely to be wrong.
    """
    by_tier: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        tier = str(row.get("regression_evidence_tier"))
        if tier == "none":
            continue
        by_tier.setdefault(tier, []).append(row)

    quota = {"explicit": 2, "linked": 3, "proximate": 5}
    out: list[dict[str, Any]] = []
    for tier, want in quota.items():
        items = sorted(
            by_tier.get(tier, []),
            key=lambda r: _stable_rank(r["pr_number"], f"regression:{tier}"),
        )
        for row in items[:want]:
            number = int(row["pr_number"])
            pr = prs.get(number) or {}
            signals = (
                row.get("explicit_regression_signals")
                or row.get("linked_fix_candidates")
                or row.get("proximate_fix_candidates")
                or []
            )
            out.append(
                {
                    "pr_number": number,
                    "url": pr.get("url"),
                    "title": pr.get("title_raw"),
                    "evidence_tier": tier,
                    "requires_human_confirmation": row.get("requires_human_confirmation"),
                    "was_reverted": row.get("was_reverted"),
                    "signal_count": len(signals),
                    "evidence": [
                        {
                            "target_number": s.get("target_number"),
                            "edge_type": s.get("edge_type"),
                            "evidence": s.get("evidence"),
                            "days_after": s.get("days_after"),
                        }
                        for s in signals[:3]
                    ],
                    "audit_verdict": None,   # true_positive | false_positive | unclear
                    "audit_notes": None,
                }
            )

    if len(out) < minimum:
        leftovers = sorted(
            (r for tier in by_tier for r in by_tier[tier]
             if int(r["pr_number"]) not in {o["pr_number"] for o in out}),
            key=lambda r: _stable_rank(r["pr_number"], "regression:topup"),
        )
        for row in leftovers[: minimum - len(out)]:
            number = int(row["pr_number"])
            out.append(
                {
                    "pr_number": number,
                    "url": (prs.get(number) or {}).get("url"),
                    "title": (prs.get(number) or {}).get("title_raw"),
                    "evidence_tier": row.get("regression_evidence_tier"),
                    "requires_human_confirmation": row.get("requires_human_confirmation"),
                    "was_reverted": row.get("was_reverted"),
                    "signal_count": None,
                    "evidence": [],
                    "audit_verdict": None,
                    "audit_notes": None,
                }
            )
    return sorted(out, key=lambda r: r["pr_number"])


def intervention_candidates(
    rows: Iterable[Mapping[str, Any]], *, minimum: int = 10
) -> list[dict[str, Any]]:
    """Sample substantive review comments, over-sampling safety-vocabulary hits."""
    candidates = [r for r in rows if r.get("is_intervention_candidate")]
    safety = [r for r in candidates if r.get("has_safety_vocabulary")]
    followed = [
        r for r in candidates
        if r.get("followed_by_change_in_path") and not r.get("has_safety_vocabulary")
    ]
    plain = [
        r for r in candidates
        if not r.get("has_safety_vocabulary")
        and not r.get("followed_by_change_in_path")
    ]

    out: list[dict[str, Any]] = []
    for bucket, want, salt in (
        (safety, 4, "safety"), (followed, 3, "followed"), (plain, 3, "plain")
    ):
        items = sorted(bucket, key=lambda r: _stable_rank(r["candidate_id"], salt))
        out.extend(items[:want])

    if len(out) < minimum:
        chosen = {r["candidate_id"] for r in out}
        leftovers = sorted(
            (r for r in candidates if r["candidate_id"] not in chosen),
            key=lambda r: _stable_rank(r["candidate_id"], "topup"),
        )
        out.extend(leftovers[: minimum - len(out)])

    return sorted(
        (
            {
                "candidate_id": r["candidate_id"],
                "pr_number": r["pr_number"],
                "url": r.get("url"),
                "commenter_login": r.get("commenter_login"),
                "path": r.get("path"),
                "substance_class": r.get("substance_class"),
                "substance_reasons": r.get("substance_reasons"),
                "safety_categories": r.get("safety_categories"),
                "thread_is_resolved": r.get("thread_is_resolved"),
                "followed_by_change_in_path": r.get("followed_by_change_in_path"),
                # The text the claim rests on, so the auditor never has to guess.
                "body_excerpt": (r.get("body_text") or "")[:500],
                "audit_verdict": None,  # substantive | not_substantive | unclear
                "audit_notes": None,
            }
            for r in out
        ),
        key=lambda r: str(r["candidate_id"]),
    )


def summarise_audit(rows: Iterable[Mapping[str, Any]], key: str = "audit_verdict") -> dict[str, Any]:
    items = list(rows)
    verdicts: dict[str, int] = {}
    for row in items:
        verdicts[str(row.get(key))] = verdicts.get(str(row.get(key)), 0) + 1
    completed = sum(1 for r in items if r.get(key) is not None)
    return {
        "sampled": len(items),
        "audited": completed,
        "pending": len(items) - completed,
        "verdicts": dict(sorted(verdicts.items())),
    }
