"""D: corrective burden — telling iteration apart from regression.

Phase 1 hands over three grades of evidence and refuses to collapse them:
``explicit`` (a revert or a fix that names this PR), ``linked`` (a later fix
closing the same issue or touching the same flag) and ``proximate`` (a later
fix touching the same files, flagged ``requires_human_confirmation``).  In a
monorepo, files change every day, so ``proximate`` is a recall device and
nothing more.

This module turns those grades into four *behavioural* classes, because "a fix
followed this" means very different things depending on who fixed it and when:

    healthy_iteration    the author refined their own work quickly. Not a
                         defect. Penalty 0.
    self_correction      the author fixed their own change later. Mild.
    unrelated_same_area  proximate-only, different component focus, no shared
                         issue or flag. Penalty 0 — this is the class that
                         exists specifically so co-occurrence is not blamed.
    probable_regression  explicit or linked evidence. This one counts.
    confirmed_revert     an explicit revert that was not reapplied.

The penalty is capped and applied to exactly one dimension
(``propagation_durability``).  Everywhere else the same facts appear as
*counterevidence text*, so a reader sees them without the arithmetic punishing
the same event six times.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from ..config import Phase2Config, days_between, parse_ts
from ..versions import derivation_version

log = logging.getLogger("impact2.analytics.corrective")

VERSION = derivation_version("corrective_burden")


class CorrectiveAnalyzer:
    def __init__(
        self,
        config: Phase2Config,
        *,
        prs: Mapping[int, Mapping[str, Any]],
        regression_by_pr: Mapping[int, Mapping[str, Any]],
        change_shape_by_pr: Mapping[int, Mapping[str, Any]],
    ) -> None:
        self.config = config
        self.prs = prs
        self.regression = regression_by_pr
        self.shape = change_shape_by_pr
        self.classes = config.get("analytics.corrective.classes")
        self.max_penalty = float(config.get("analytics.corrective.max_total_penalty"))

    def _classify_candidate(
        self, source_pr: int, candidate: Mapping[str, Any], tier: str
    ) -> tuple[str, str]:
        target = candidate.get("target_number")
        target_pr = self.prs.get(int(target)) if target is not None else None
        source = self.prs.get(source_pr) or {}
        edge_type = str(candidate.get("edge_type") or "")

        if edge_type in {"reverts", "reverted_by"}:
            return "confirmed_revert", (
                f"PR #{target} explicitly reverts PR #{source_pr}"
            )
        if tier == "proximate":
            # Same-area co-occurrence. Only a different-component focus makes
            # it clearly unrelated; otherwise it is still unconfirmed.
            source_component = str(source.get("dominant_component") or "") or str(
                (self.shape.get(source_pr) or {}).get("dominant_component") or ""
            )
            target_component = str(
                (self.shape.get(int(target)) or {}).get("dominant_component") or ""
            ) if target is not None else ""
            if source_component and target_component and source_component != target_component:
                return "unrelated_same_area", (
                    f"PR #{target} touches the same files but its dominant component "
                    f"is '{target_component}', not '{source_component}'"
                )
            return "unrelated_same_area", (
                f"PR #{target} is a later fix touching "
                f"{candidate.get('shared_path_count', '?')} of the same files; "
                "Phase 1 marks this requires_human_confirmation and it is not "
                "treated as a regression"
            )

        same_author = bool(
            target_pr and target_pr.get("author_actor_id") == source.get("author_actor_id")
        )
        days = candidate.get("days_after")
        healthy_window = float(
            self.classes["healthy_iteration"]["max_days_after"]
        )
        if same_author and isinstance(days, (int, float)) and days <= healthy_window:
            return "healthy_iteration", (
                f"the same engineer followed up in PR #{target} after "
                f"{days:.1f} days — normal iteration"
            )
        if same_author:
            return "self_correction", (
                f"the same engineer corrected this in PR #{target}"
                + (f" after {days:.1f} days" if isinstance(days, (int, float)) else "")
            )
        return "probable_regression", (
            f"PR #{target} ({tier} evidence) corrected this change"
            + (f" after {days:.1f} days" if isinstance(days, (int, float)) else "")
        )

    def analyse(self, episode_id: str, pr_numbers: Sequence[int]) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        for number in pr_numbers:
            row = self.regression.get(number)
            if not row:
                continue
            for tier, key in (
                ("explicit", "explicit_regression_signals"),
                ("linked", "linked_fix_candidates"),
                ("proximate", "proximate_fix_candidates"),
            ):
                for candidate in (row.get(key) or [])[:20]:
                    if not isinstance(candidate, Mapping):
                        continue
                    label, detail = self._classify_candidate(number, candidate, tier)
                    events.append(
                        {
                            "source_pr_number": number,
                            "corrective_pr_number": candidate.get("target_number"),
                            "evidence_tier": tier,
                            "corrective_class": label,
                            "detail": detail,
                            "days_after": candidate.get("days_after"),
                            "requires_human_confirmation": tier == "proximate",
                            "penalty_weight": float(
                                self.classes[label].get("penalty", 0.0)
                            ),
                        }
                    )

        reapplied = any(
            str((self.regression.get(n) or {}).get("regression_evidence_tier")) == "explicit"
            and any(
                str(e.get("edge_type")) == "reapplies"
                for e in ((self.regression.get(n) or {}).get("explicit_regression_signals") or [])
                if isinstance(e, Mapping)
            )
            for n in pr_numbers
        )

        by_class: dict[str, int] = defaultdict(int)
        for event in events:
            by_class[event["corrective_class"]] += 1

        # Capped so experimentation is never punished twice.
        raw_penalty = sum(float(e["penalty_weight"]) for e in events)
        penalty = round(min(self.max_penalty, raw_penalty), 4)
        if reapplied and by_class.get("confirmed_revert"):
            penalty = round(max(0.0, penalty - 1.0), 4)

        confirmed_revert = bool(by_class.get("confirmed_revert")) and not reapplied

        return {
            "episode_id": episode_id,
            "events": events,
            "event_count": len(events),
            "by_class": dict(sorted(by_class.items())),
            "raw_penalty": round(raw_penalty, 4),
            "capped_penalty": penalty,
            "penalty_cap": self.max_penalty,
            "cap_applied": raw_penalty > self.max_penalty,
            "confirmed_revert": confirmed_revert,
            "reapplied": reapplied,
            "applies_to_dimension": str(
                self.config.get("analytics.corrective.applies_to_dimension")
            ),
            "unconfirmed_event_count": sum(
                1 for e in events if e["requires_human_confirmation"]
            ),
            "note": (
                "Proximate co-occurrence is never treated as a regression. Only "
                "explicit and linked evidence carries a penalty, the penalty is "
                "capped, and it is applied to one dimension only."
            ),
            "corrective_burden_version": VERSION,
        }


def summarise(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    by_class: dict[str, int] = defaultdict(int)
    for row in items:
        for label, count in (row.get("by_class") or {}).items():
            by_class[str(label)] += int(count)
    penalised = [r for r in items if float(r.get("capped_penalty") or 0) > 0]
    return {
        "episodes_analysed": len(items),
        "events_by_class": dict(sorted(by_class.items())),
        "episodes_with_penalty": len(penalised),
        "episodes_with_confirmed_revert": sum(1 for r in items if r.get("confirmed_revert")),
        "episodes_with_cap_applied": sum(1 for r in items if r.get("cap_applied")),
        "unconfirmed_events": sum(int(r.get("unconfirmed_event_count") or 0) for r in items),
        "corrective_burden_version": VERSION,
    }
