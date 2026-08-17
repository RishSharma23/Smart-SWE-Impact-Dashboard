"""Episode status, and the difference between merging and shipping.

The phase spec states it flatly: **merge is not proof of user release.**  This
repository merges through a Trunk queue into a continuously-deployed product,
so a merged PR is *observable* in the repository — but whether users saw it
depends on feature flags, rollout and follow-through, none of which merging
proves.

The design that keeps both facts true at once:

* ``status`` takes one of the seven values the spec names;
* ``release_corroboration`` is reported separately and is either
  ``corroborated`` (with the evidence that corroborates it) or ``merged_only``;
* the rubric requires ``corroborated`` before any dimension may reach band 3.

So an episode can be ``shipped_observable`` — it landed on master and was not
reverted — while the UI still says, on the same card, "release not
independently corroborated". That is the honest reading, and it is the one the
dimension bands are computed against.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..config import Phase2Config, parse_ts
from ..versions import derivation_version

VERSION = derivation_version("episode_status")

STATUSES = (
    "shipped_observable",
    "partial_or_behind_flag",
    "reverted",
    "superseded",
    "maintenance",
    "exploratory",
    "unknown",
)

MECHANICAL_FLAGS = ("is_lockfile", "is_generated", "is_snapshot", "is_vendor",
                    "is_binary_asset")


def _mechanical_share(files: Sequence[Mapping[str, Any]]) -> float | None:
    if not files:
        return None
    mechanical = sum(
        1 for f in files if any(bool(f.get(flag)) for flag in MECHANICAL_FLAGS)
    )
    return round(mechanical / len(files), 4)


def classify(
    *,
    config: Phase2Config,
    prs: Sequence[Mapping[str, Any]],
    files: Sequence[Mapping[str, Any]],
    flags: Sequence[Mapping[str, Any]],
    regression_rows: Sequence[Mapping[str, Any]],
    superseded_by: Sequence[Mapping[str, Any]],
    linked_issues: Sequence[Mapping[str, Any]],
    downstream_adoption_count: int,
    window_end: Any,
) -> dict[str, Any]:
    """Return status, release corroboration and the evidence for both."""
    reasons: list[str] = []
    release_evidence: list[dict[str, Any]] = []

    merged = [p for p in prs if p.get("merged_at")]
    all_merged = bool(merged) and len(merged) == len(prs)
    any_merged = bool(merged)

    # -- reverted / superseded (strongest signals, checked first) ---------
    reverted = [
        r for r in regression_rows
        if r.get("was_reverted") and r.get("regression_evidence_tier") == "explicit"
    ]
    reapplied = any(
        str(e.get("edge_type")) == "reapplies" for e in superseded_by
    )
    if reverted and not reapplied:
        return {
            "status": "reverted",
            "status_reasons": [
                f"PR #{r.get('pr_number')} has explicit revert evidence: "
                f"{(r.get('explicit_regression_signals') or [{}])[0].get('evidence', '')}"[:240]
                for r in reverted[:3]
            ],
            "release_corroboration": "merged_only",
            "release_evidence": [],
            "episode_status_version": VERSION,
        }
    if any(str(e.get("edge_type")) == "supersedes" for e in superseded_by):
        reasons.append("a later PR explicitly supersedes this work")
        return {
            "status": "superseded",
            "status_reasons": reasons,
            "release_corroboration": "merged_only",
            "release_evidence": [],
            "episode_status_version": VERSION,
        }

    # -- exploratory: nothing landed --------------------------------------
    if not any_merged:
        states = sorted({str(p.get("state")) for p in prs})
        return {
            "status": "exploratory",
            "status_reasons": [f"no PR in this episode merged (states: {states})"],
            "release_corroboration": "merged_only",
            "release_evidence": [],
            "episode_status_version": VERSION,
        }

    # -- maintenance: the change is packaging, not substance ---------------
    mechanical = _mechanical_share(files)
    threshold = float(config.get("episodes.status.maintenance_mechanical_share"))
    if mechanical is not None and mechanical >= threshold:
        return {
            "status": "maintenance",
            "status_reasons": [
                f"{mechanical:.0%} of changed files are lockfiles, generated code, "
                f"snapshots or vendored code (>= {threshold:.0%})"
            ],
            "release_corroboration": "merged_only",
            "release_evidence": [],
            "episode_status_version": VERSION,
        }

    # -- release corroboration --------------------------------------------
    doc_files = [f for f in files if f.get("is_docs")]
    if doc_files:
        release_evidence.append(
            {
                "kind": "docs_or_changelog_touched",
                "detail": f"{len(doc_files)} documentation file(s) changed, "
                          f"e.g. {doc_files[0].get('path')}",
            }
        )
    removed_flags = [
        f for f in flags if str(f.get("diff_side")) in {"removed", "-", "deletion"}
    ]
    if removed_flags:
        release_evidence.append(
            {
                "kind": "feature_flag_removed",
                "detail": f"feature flag '{removed_flags[0].get('flag_key')}' removed "
                          "from the registry, which is how a rollout ends",
            }
        )
    if downstream_adoption_count > 0:
        release_evidence.append(
            {
                "kind": "downstream_adoption_observed",
                "detail": f"{downstream_adoption_count} later change(s) depend on what "
                          "this episode introduced",
            }
        )
    completed_issues = [
        i for i in linked_issues
        if str(i.get("state")).upper() == "CLOSED"
        and str(i.get("state_reason") or "").upper() in {"COMPLETED", ""}
    ]
    if completed_issues:
        release_evidence.append(
            {
                "kind": "linked_issue_closed_as_completed",
                "detail": f"issue #{completed_issues[0].get('issue_number')} closed as completed",
            }
        )

    corroboration = "corroborated" if release_evidence else "merged_only"

    # -- still behind a flag ----------------------------------------------
    added_flags = [
        f for f in flags if str(f.get("diff_side")) in {"added", "+", "addition"}
    ]
    still_gated = bool(
        config.get("episodes.status.flag_still_gated_is_partial")
        and added_flags and not removed_flags
    )
    if still_gated:
        return {
            "status": "partial_or_behind_flag",
            "status_reasons": [
                f"feature flag '{added_flags[0].get('flag_key')}' was introduced and "
                "was not removed inside the window; the arc is still gated"
            ],
            "release_corroboration": corroboration,
            "release_evidence": release_evidence,
            "episode_status_version": VERSION,
        }

    if not all_merged:
        reasons.append(
            f"{len(merged)}/{len(prs)} PRs merged; the rest are open or closed"
        )

    # -- shipped, with the merge/release distinction preserved -------------
    reasons.append(
        f"{len(merged)} PR(s) merged to the default branch"
        + ("" if release_evidence else
           "; no independent release evidence — merging is not proof of user release")
    )
    return {
        "status": "shipped_observable",
        "status_reasons": reasons,
        "release_corroboration": corroboration,
        "release_evidence": release_evidence,
        "episode_status_version": VERSION,
    }


def is_ranked_status(status: str) -> bool:
    """Statuses whose episodes may carry impact evidence into a portfolio.

    Reverted and superseded work is *not* excluded — it is scored with the
    reversion recorded as counterevidence, because the spec says reversion is
    counterevidence rather than automatic failure. Exploratory work is excluded
    because nothing landed to observe.
    """
    return status in {
        "shipped_observable", "partial_or_behind_flag", "reverted", "superseded",
        "maintenance",
    }
