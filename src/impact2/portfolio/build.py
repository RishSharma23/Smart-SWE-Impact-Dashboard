"""Engineer portfolios: attributed episode evidence, aggregated without volume.

The aggregation problem, stated plainly: if a portfolio value is the *sum* of
episode bands, then ten local fixes beat one platform migration and the whole
system collapses into a commit counter with extra steps.  If it is the *max*,
then corroborating work counts for nothing and one lucky episode is a career.

The answer used here is an ordered weighted average with a hard cap:

    value = min(scale_max, v1 + min(headroom, sum(coeff_i * v_i for i >= 2)))

with ``coeffs = [1.00, 0.55, 0.30, 0.17, 0.10]`` and ``headroom = 1.0`` band.
The strongest episode carries the evidentiary mass; everything after it is
capped corroboration.  Ten band-1 episodes reach 2.0; one band-4 episode
reaches 4.0.  A single transformative episode can outrank many moderate ones —
which is what the phase spec requires — while corroboration still moves the
needle.

Each contributing value is ``band x confidence_discount x attribution_factor``,
so a supporting reviewer on a transformative episode contributes real but
clearly smaller evidence than its core implementer, and an episode assessed at
low confidence contributes less than one assessed at high confidence.

Independence: episodes closing the same issue, or lying in the same propagation
lineage, are not independent corroboration.  They are grouped and the strongest
of the group is used once.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from ..config import Phase2Config, days_between, iso, parse_ts
from ..ids import portfolio_id
from ..versions import derivation_version

log = logging.getLogger("impact2.portfolio")

VERSION = derivation_version("portfolio_aggregation")
DIVERSITY_VERSION = derivation_version("diversity")

DIMENSIONS = (
    "product_outcome", "reliability_risk", "engineering_leverage",
    "decision_quality", "propagation_durability", "collaborative_amplification",
)


def aggregate_ordered(
    values: Sequence[float], coefficients: Sequence[float], *,
    headroom: float, scale_max: float,
) -> tuple[float, list[dict[str, Any]]]:
    """OWA with corroboration headroom. Returns (value, per-entry trace)."""
    if not values:
        return 0.0, []
    ordered = sorted(values, reverse=True)
    trace: list[dict[str, Any]] = []
    top = ordered[0]
    trace.append({"rank": 1, "value": round(top, 6), "coefficient": 1.0,
                  "contribution": round(top, 6)})
    corroboration = 0.0
    for index, value in enumerate(ordered[1:], start=1):
        if index >= len(coefficients):
            trace.append({"rank": index + 1, "value": round(value, 6),
                          "coefficient": 0.0, "contribution": 0.0,
                          "note": "beyond the coefficient list; contributes nothing"})
            continue
        coefficient = float(coefficients[index])
        contribution = coefficient * value
        corroboration += contribution
        trace.append({"rank": index + 1, "value": round(value, 6),
                      "coefficient": coefficient,
                      "contribution": round(contribution, 6)})
    capped = min(headroom, corroboration)
    total = min(scale_max, top + capped)
    for entry in trace:
        entry["headroom_capped"] = corroboration > headroom
    return round(total, 6), trace


def entropy(counts: Sequence[float], base: float = 2.0) -> float | None:
    total = sum(counts)
    if total <= 0:
        return None
    value = 0.0
    for count in counts:
        if count <= 0:
            continue
        p = count / total
        value -= p * math.log(p, base)
    return round(value, 6)


class PortfolioBuilder:
    def __init__(
        self,
        config: Phase2Config,
        *,
        episodes: Mapping[str, Mapping[str, Any]],
        dimensions: Sequence[Mapping[str, Any]],
        participants: Sequence[Mapping[str, Any]],
        propagation: Mapping[str, Mapping[str, Any]],
        actors: Mapping[str, Mapping[str, Any]],
        window_start: Any,
        window_end: Any,
    ) -> None:
        self.config = config
        self.episodes = episodes
        self.propagation = propagation
        self.actors = actors
        self.window_start = parse_ts(window_start)
        self.window_end = parse_ts(window_end)

        self.dim_by_episode: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
        for row in dimensions:
            self.dim_by_episode[str(row["episode_id"])][str(row["dimension"])] = row

        self.participants_by_actor: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in participants:
            if row.get("contributes_to_portfolio"):
                self.participants_by_actor[str(row["actor_cluster_id"])].append(row)

        agg = config.get("outranking.aggregation")
        self.coefficients = [float(c) for c in agg["coefficients"]]
        self.headroom = float(agg["corroboration_headroom"])
        self.scale_max = float(agg["scale_max"])
        self.min_factor = float(agg["min_attribution_factor"])
        self.independence = agg["independence"]
        self.discounts = config.get("rubric.confidence.discount")
        self.halfwidths = config.get("rubric.confidence.interval_halfwidth")

    # -- independence -----------------------------------------------------
    def _independence_group(self, episode_id: str) -> str:
        """Episodes that corroborate each other are not independent evidence."""
        episode = self.episodes.get(episode_id) or {}
        if self.independence.get("same_root_issue_counts_once"):
            issues = episode.get("issue_numbers") or []
            if issues:
                return f"issue:{min(int(i) for i in issues)}"
        if self.independence.get("same_propagation_lineage_counts_once"):
            propagation = self.propagation.get(episode_id) or {}
            components = propagation.get("components_reached") or []
            if components and int(propagation.get("max_path_depth") or 0) >= 2:
                return f"lineage:{sorted(components)[0]}"
        return f"episode:{episode_id}"

    # -- entries ----------------------------------------------------------
    def _entries(
        self, actor: str, dimension: str, *, decay_mode: str,
        min_confidence: str | None = None,
        window_days: int | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (contributing entries, unknown/excluded entries)."""
        order = {"low": 0, "medium": 1, "high": 2}
        contributing: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []

        for participant in self.participants_by_actor.get(actor, []):
            eid = str(participant["episode_id"])
            episode = self.episodes.get(eid)
            if not episode or not episode.get("ranked"):
                continue
            assessment = (self.dim_by_episode.get(eid) or {}).get(dimension)
            if not assessment:
                continue

            factor = float((participant.get("attribution_factors") or {}).get(dimension, 0.0))
            if factor < self.min_factor:
                continue

            if window_days is not None and self.window_end is not None:
                ended = parse_ts(episode.get("ended_at"))
                age = days_between(ended, self.window_end)
                if age is not None and age > window_days:
                    excluded.append(
                        {"episode_id": eid, "reason": f"outside the last {window_days} days"}
                    )
                    continue

            band = assessment.get("band")
            confidence = str(assessment.get("confidence") or "low")
            if band is None:
                excluded.append(
                    {"episode_id": eid, "reason": "band is unknown",
                     "unknown_reason": assessment.get("unknown_reason"),
                     "counts_as_zero": False}
                )
                continue
            if min_confidence and order.get(confidence, 0) < order.get(min_confidence, 0):
                excluded.append(
                    {"episode_id": eid,
                     "reason": f"confidence {confidence} below scenario floor {min_confidence}"}
                )
                continue

            discount = float(self.discounts[confidence])
            decay = 1.0
            if decay_mode == "decayed":
                propagation = self.propagation.get(eid) or {}
                effective = propagation.get("effective_decay_factor")
                if effective is None:
                    ended = parse_ts(episode.get("ended_at"))
                    age = days_between(ended, self.window_end) or 0.0
                    half_life = float(self.config.get("analytics.decay.half_life_days"))
                    effective = math.exp(-math.log(2.0) * max(0.0, age) / half_life)
                decay = float(effective)

            value = float(band) * discount * factor * decay
            halfwidth = float(self.halfwidths[confidence]) * factor
            contributing.append(
                {
                    "episode_id": eid,
                    "episode_title": episode.get("title"),
                    "band": int(band),
                    "band_label": assessment.get("band_label"),
                    "confidence": confidence,
                    "confidence_discount": discount,
                    "attribution_factor": round(factor, 6),
                    "share_category": participant.get("share_category"),
                    "roles": participant.get("roles"),
                    "decay_factor": round(decay, 6),
                    "value": round(value, 6),
                    "interval_low": round(max(0.0, value - halfwidth * discount), 6),
                    "interval_high": round(
                        min(self.scale_max, value + halfwidth * discount), 6
                    ),
                    "independence_group": self._independence_group(eid),
                    "rationale": assessment.get("rationale"),
                }
            )

        # Collapse non-independent episodes to their strongest member.
        by_group: dict[str, dict[str, Any]] = {}
        collapsed: list[dict[str, Any]] = []
        for entry in sorted(contributing, key=lambda e: -e["value"]):
            group = entry["independence_group"]
            if group in by_group:
                collapsed.append(
                    {"episode_id": entry["episode_id"],
                     "reason": f"not independent of {by_group[group]['episode_id']} "
                               f"(group {group}); the stronger of the two is used once"}
                )
                continue
            by_group[group] = entry
        excluded.extend(collapsed)
        return sorted(by_group.values(), key=lambda e: -e["value"]), excluded

    # -- per-dimension ------------------------------------------------------
    def dimension_value(
        self, actor: str, dimension: str, *, decay_mode: str,
        min_confidence: str | None = None, window_days: int | None = None,
    ) -> dict[str, Any]:
        entries, excluded = self._entries(
            actor, dimension, decay_mode=decay_mode,
            min_confidence=min_confidence, window_days=window_days,
        )
        if not entries:
            return {
                "dimension": dimension,
                "value": None,
                "is_unknown": True,
                "unknown_reason": (
                    "no attributed episode with an assessable band in this dimension"
                    if not excluded else
                    "; ".join(sorted({str(e.get("reason")) for e in excluded})[:3])
                ),
                "entries": [],
                "excluded": excluded,
                "episode_count": 0,
                "top_episode_id": None,
                "interval_low": None,
                "interval_high": None,
                "confidence": "unknown",
                "aggregation_trace": [],
            }
        value, trace = aggregate_ordered(
            [e["value"] for e in entries], self.coefficients,
            headroom=self.headroom, scale_max=self.scale_max,
        )
        low, _ = aggregate_ordered(
            [e["interval_low"] for e in entries], self.coefficients,
            headroom=self.headroom, scale_max=self.scale_max,
        )
        high, _ = aggregate_ordered(
            [e["interval_high"] for e in entries], self.coefficients,
            headroom=self.headroom, scale_max=self.scale_max,
        )
        confidences = [e["confidence"] for e in entries[:3]]
        dominant = max(set(confidences), key=confidences.count)
        return {
            "dimension": dimension,
            "value": value,
            "is_unknown": False,
            "unknown_reason": None,
            "entries": entries[:10],
            "excluded": excluded[:10],
            "episode_count": len(entries),
            "top_episode_id": entries[0]["episode_id"],
            "top_band": entries[0]["band"],
            "interval_low": low,
            "interval_high": high,
            "confidence": dominant,
            "aggregation_trace": trace[:6],
        }

    # -- portfolio ---------------------------------------------------------
    def build_one(
        self, actor: str, *, decay_mode: str = "decayed",
        min_confidence: str | None = None, window_days: int | None = None,
    ) -> dict[str, Any]:
        participants = self.participants_by_actor.get(actor, [])
        identity = participants[0] if participants else {}
        values = {
            dimension: self.dimension_value(
                actor, dimension, decay_mode=decay_mode,
                min_confidence=min_confidence, window_days=window_days,
            )
            for dimension in DIMENSIONS
        }

        episode_ids = sorted({str(p["episode_id"]) for p in participants})
        known = [v for v in values.values() if not v["is_unknown"]]
        strongest = max(
            (v for v in known), key=lambda v: v["value"], default=None
        )

        # Current vs foundational. In a 90-day window "foundational" cannot mean
        # "years ago", so it is defined by behaviour: work whose value is
        # carried by leverage/durability with observed persistence.
        current_ids: list[str] = []
        foundational_ids: list[str] = []
        for eid in episode_ids:
            propagation = self.propagation.get(eid) or {}
            episode = self.episodes.get(eid) or {}
            leverage = (self.dim_by_episode.get(eid) or {}).get("engineering_leverage") or {}
            durability = (self.dim_by_episode.get(eid) or {}).get("propagation_durability") or {}
            is_foundational = (
                bool(propagation.get("persistence_detected"))
                or int(leverage.get("band") or 0) >= 3
                or int(durability.get("band") or 0) >= 3
            )
            (foundational_ids if is_foundational else current_ids).append(eid)

        # Diversity, descriptive only.
        masses = [
            e["value"]
            for v in values.values() if not v["is_unknown"]
            for e in v["entries"]
        ]
        by_episode_mass: dict[str, float] = defaultdict(float)
        for v in values.values():
            if v["is_unknown"]:
                continue
            for entry in v["entries"]:
                by_episode_mass[entry["episode_id"]] += entry["value"]
        ordered_mass = sorted(by_episode_mass.values(), reverse=True)
        total_mass = sum(ordered_mass) or 1.0
        labels = self.config.get("analytics.diversity.concentration_labels")
        if ordered_mass and ordered_mass[0] / total_mass >= float(
            labels["single_episode_dominant"]
        ):
            concentration = "single_episode_dominant"
        elif sum(ordered_mass[:3]) / total_mass >= float(labels["few_episodes"]):
            concentration = "few_episodes"
        else:
            concentration = "broad"

        eligibility = self._eligibility(values, episode_ids)

        return {
            "portfolio_id": portfolio_id(actor),
            "actor_cluster_id": actor,
            "primary_actor_id": identity.get("primary_actor_id"),
            "login": identity.get("login"),
            "display_name": identity.get("display_name"),
            "affiliation": "unknown",
            "affiliation_note": (
                "Affiliation is not asserted: public GitHub data does not "
                "reliably distinguish employees from community contributors."
            ),
            "identity_ambiguity": identity.get("identity_ambiguity"),
            "identity_ambiguity_reasons": identity.get("identity_ambiguity_reasons") or [],
            "episode_ids": episode_ids,
            "episode_count": len(episode_ids),
            "eligible_episode_count": sum(
                1 for e in episode_ids if (self.episodes.get(e) or {}).get("ranked")
            ),
            "roles_held": sorted({r for p in participants for r in (p.get("roles") or [])}),
            "share_categories": sorted({str(p.get("share_category")) for p in participants}),
            "dimension_values": {k: v["value"] for k, v in values.items()},
            "dimension_detail": values,
            "dimension_confidence": {k: v["confidence"] for k, v in values.items()},
            "dimension_intervals": {
                k: [v["interval_low"], v["interval_high"]] for k, v in values.items()
            },
            "unknown_dimensions": sorted(
                k for k, v in values.items() if v["is_unknown"]
            ),
            "strongest_dimension": strongest["dimension"] if strongest else None,
            "strongest_evidence_episode_id": strongest["top_episode_id"] if strongest else None,
            "current_episode_ids": current_ids,
            "foundational_episode_ids": foundational_ids,
            "concentration_profile": concentration,
            "episode_mass_entropy": entropy(ordered_mass),
            "diversity_affects_ranking": False,
            "active_period": self._active_period(episode_ids),
            "rankable": eligibility["rankable"],
            "eligibility_label": eligibility["label"],
            "eligibility_reasons": eligibility["reasons"],
            "decay_mode": decay_mode,
            "portfolio_version": VERSION,
            "diversity_version": DIVERSITY_VERSION,
        }

    def _eligibility(
        self, values: Mapping[str, Mapping[str, Any]], episode_ids: Sequence[str]
    ) -> dict[str, Any]:
        rules = self.config.get("outranking.ranking_eligibility")
        reasons: list[str] = []
        known = [v for v in values.values() if not v["is_unknown"]]
        confidences = [v["confidence"] for v in known]
        discounts = self.discounts
        mean_discount = (
            sum(float(discounts.get(c, 0.45)) for c in confidences) / len(confidences)
            if confidences else 0.0
        )
        rankable = True
        if len(episode_ids) < int(rules["min_episodes_with_evidence"]):
            rankable = False
            reasons.append(
                f"{len(episode_ids)} attributed episode(s), minimum is "
                f"{rules['min_episodes_with_evidence']}"
            )
        if len(known) < int(rules["min_dimensions_with_band"]):
            rankable = False
            reasons.append(
                f"only {len(known)} dimension(s) have an assessable band, minimum "
                f"is {rules['min_dimensions_with_band']}"
            )
        if len(values) - len(known) > int(rules["max_unknown_dimensions"]):
            rankable = False
            reasons.append(f"{len(values) - len(known)} dimensions are unknown")
        if known and mean_discount < float(rules["min_mean_confidence_discount"]):
            rankable = False
            reasons.append(
                f"mean confidence discount {mean_discount:.2f} below "
                f"{rules['min_mean_confidence_discount']}"
            )
        if rankable:
            reasons.append("meets the minimum observable-evidence bar")
        return {
            "rankable": rankable,
            "label": None if rankable else str(
                self.config.get("eligibility.minimum_evidence_to_rank.label_when_below")
            ),
            "reasons": reasons,
        }

    def _active_period(self, episode_ids: Sequence[str]) -> dict[str, Any]:
        """Described, never used as a denominator."""
        starts, ends = [], []
        for eid in episode_ids:
            episode = self.episodes.get(eid) or {}
            start, end = parse_ts(episode.get("started_at")), parse_ts(episode.get("ended_at"))
            if start:
                starts.append(start)
            if end:
                ends.append(end)
        if not starts or not ends:
            return {"first_observed": None, "last_observed": None, "span_days": None,
                    "note": "no dated episode"}
        return {
            "first_observed": iso(min(starts)),
            "last_observed": iso(max(ends)),
            "span_days": round((max(ends) - min(starts)).total_seconds() / 86400.0, 2),
            "note": (
                "Descriptive only. This is never used as a denominator: "
                "per-day normalisation would penalise anyone who took leave."
            ),
        }

    def build_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        rows = [
            self.build_one(actor, **kwargs)
            for actor in sorted(self.participants_by_actor)
        ]
        log.info(
            "portfolios: %d built, %d rankable (decay_mode=%s)",
            len(rows), sum(1 for r in rows if r["rankable"]),
            kwargs.get("decay_mode", "decayed"),
        )
        return rows


def summarise(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    rankable = [r for r in items if r.get("rankable")]
    concentration: dict[str, int] = defaultdict(int)
    for row in items:
        concentration[str(row.get("concentration_profile"))] += 1
    unknown_counts = [len(r.get("unknown_dimensions") or []) for r in items]
    return {
        "portfolios": len(items),
        "rankable": len(rankable),
        "insufficient_evidence": len(items) - len(rankable),
        "concentration_profiles": dict(sorted(concentration.items())),
        "mean_unknown_dimensions": (
            round(sum(unknown_counts) / len(unknown_counts), 3) if unknown_counts else None
        ),
        "with_foundational_episodes": sum(
            1 for r in items if r.get("foundational_episode_ids")
        ),
        "portfolio_version": VERSION,
    }
