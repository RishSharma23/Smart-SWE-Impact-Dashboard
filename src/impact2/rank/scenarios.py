"""Scenario definitions and their availability.

The phase spec asks for eight scenarios, two of which this dataset cannot
support: "last 12 months" and "foundational / full history" both need more than
the 90 days Phase 1 extracted.  The honest response is not to silently run them
against 90 days and label the result "12 months" — it is to mark them
``available: false``, state exactly what is missing and exactly how to get it,
and let the UI render them as a disabled tab with the reason on hover.

The reason matters: the window is *config*, and Phase 1's handover documents
the one-command widening.  A scenario that is unavailable today becomes
available the moment someone re-runs with a longer window, with no schema
change and no code change here.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from ..config import Phase2Config

log = logging.getLogger("impact2.rank.scenarios")


def resolve(
    config: Phase2Config, *, window_days: int | None, is_shallow_clone: bool
) -> list[dict[str, Any]]:
    """Return every scenario with its availability decided from the real window."""
    definitions = config.get("outranking.scenarios")
    out: list[dict[str, Any]] = []

    for name, definition in definitions.items():
        filters = definition.get("filters") or {}
        available = True
        reason: str | None = None
        remedy: str | None = None

        required_days = definition.get("requires_window_days")
        if required_days and (window_days is None or window_days < int(required_days)):
            available = False
            reason = (
                f"needs a {required_days}-day window; the extracted window is "
                f"{window_days} days"
            )
            remedy = (
                "Re-run Phase 1 with `python -m impact all --window-start "
                f"<{required_days} days ago>` and widen the clone with "
                "`git -C data/raw/git/posthog fetch --shallow-since=<date>`, "
                "then re-run `make p2`."
            )
        if definition.get("requires_full_history") and is_shallow_clone:
            available = False
            reason = (
                "needs full repository history; the analysis clone is shallow and "
                "reaches only ~30 days before the window start"
            )
            remedy = (
                "Run `git -C data/raw/git/posthog fetch --unshallow`, then re-run "
                "Phase 1 and `make p2`."
            )

        scenario_window = filters.get("window_days", "absent")
        if available and scenario_window not in (None, "absent"):
            requested = int(scenario_window)
            if window_days is not None and requested > window_days:
                # Not unavailable — just identical to the full window. Say so.
                reason = (
                    f"the {requested}-day filter covers the whole {window_days}-day "
                    "dataset, so this scenario is identical to the unfiltered run"
                )

        out.append(
            {
                "scenario": name,
                "label": definition.get("label", name),
                "description": definition.get("description"),
                "weights_override": definition.get("weights") or {},
                "filters": filters,
                "available": available,
                "unavailable_reason": None if available else reason,
                "note": reason if available else None,
                "remedy": remedy,
                "min_dimension_confidence": filters.get("min_dimension_confidence"),
                "window_days": (
                    None if scenario_window in (None, "absent") else int(scenario_window)
                ),
                "time_decay": filters.get("time_decay", "enabled"),
            }
        )

    unavailable = [s["scenario"] for s in out if not s["available"]]
    if unavailable:
        log.warning(
            "scenarios unavailable on this dataset: %s (reasons recorded and "
            "exported, not hidden)", unavailable,
        )
    return sorted(out, key=lambda s: s["scenario"])


def portfolio_kwargs(scenario: Mapping[str, Any]) -> dict[str, Any]:
    """Translate a scenario into portfolio-aggregation arguments."""
    return {
        "decay_mode": (
            "undecayed" if scenario.get("time_decay") == "disabled" else "decayed"
        ),
        "min_confidence": scenario.get("min_dimension_confidence"),
        "window_days": scenario.get("window_days"),
    }
