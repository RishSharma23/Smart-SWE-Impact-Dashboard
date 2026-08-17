"""G + validation item 6: uncertainty, bootstrap stability and sensitivity.

Two different questions, deliberately kept apart.

**Bootstrap (uncertainty in the evidence).**  Resample each engineer's
attributed episodes with replacement, rebuild their portfolio, re-rank, and
record where they land.  This answers "if we had observed a slightly different
90 days, would the order hold?"  Missing data widens the resulting interval —
it never lowers the point estimate, because an engineer whose Rust work has no
import graph should come out *uncertain*, not *low*.

**Sensitivity (uncertainty in the method).**  Vary the things we chose rather
than the things we observed: criterion weights, indifference/preference/veto
thresholds, confidence discounts, the OWA coefficients, and whether time decay
applies at all.  This answers "how much of this ranking is our preferences
rather than the evidence?"  The coefficient variants deliberately include a
flat ``[1,1,1,1,1]`` control, which is the volume-friendly setting the whole
design exists to avoid — if the top five survive that, the result is not an
artefact of the diminishing-return curve.

Both report rank frequencies and top-five inclusion probability, which is the
form a reader can actually act on: "this engineer is in the top five in 96% of
plausible configurations" is a defensible sentence; "score 847" is not.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..config import Phase2Config
from ..versions import derivation_version

log = logging.getLogger("impact2.rank.sensitivity")

VERSION = derivation_version("sensitivity")
UNCERTAINTY_VERSION = derivation_version("uncertainty")


def _rank_positions(ranking: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {str(r["actor_cluster_id"]): int(r["position"]) for r in ranking}


def _accumulate(
    store: dict[str, dict[int, int]], positions: Mapping[str, int]
) -> None:
    for actor, position in positions.items():
        store.setdefault(actor, defaultdict(int))[position] += 1


def _summarise_frequencies(
    store: Mapping[str, Mapping[int, int]], trials: int, top_n: int = 5
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for actor, counts in store.items():
        total = sum(counts.values()) or 1
        positions = sorted(counts)
        mean = sum(p * c for p, c in counts.items()) / total
        cumulative = 0
        median = positions[0]
        for position in positions:
            cumulative += counts[position]
            if cumulative >= total / 2:
                median = position
                break
        top_n_count = sum(c for p, c in counts.items() if p <= top_n)
        # 5th/95th percentile positions, for an honest interval.
        low = high = positions[0]
        cumulative = 0
        for position in positions:
            cumulative += counts[position]
            if cumulative >= 0.05 * total:
                low = position
                break
        cumulative = 0
        for position in positions:
            cumulative += counts[position]
            if cumulative >= 0.95 * total:
                high = position
                break
        rows.append(
            {
                "actor_cluster_id": actor,
                "trials": trials,
                "observed": total,
                "mean_position": round(mean, 3),
                "median_position": median,
                "best_position": positions[0],
                "worst_position": positions[-1],
                "position_p05": low,
                "position_p95": high,
                "position_frequencies": {str(p): counts[p] for p in positions},
                f"top{top_n}_inclusion_probability": round(top_n_count / total, 4),
                "rank_stability_index": round(
                    max(counts.values()) / total, 4
                ),
            }
        )
    return sorted(rows, key=lambda r: (r["mean_position"], r["actor_cluster_id"]))


def bootstrap_stability(
    config: Phase2Config,
    *,
    actors: Sequence[str],
    episodes_by_actor: Mapping[str, Sequence[str]],
    rebuild: Callable[[Mapping[str, Sequence[str]]], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Resample episodes with replacement; re-rank; report the distribution."""
    resamples = int(config.get("analytics.uncertainty.bootstrap_resamples"))
    seed = int(config.get("analytics.uncertainty.random_seed"))
    rng = random.Random(seed)

    store: dict[str, dict[int, int]] = {}
    skipped = 0
    for _ in range(resamples):
        sample: dict[str, list[str]] = {}
        for actor in actors:
            pool = list(episodes_by_actor.get(actor) or [])
            if not pool:
                continue
            sample[actor] = [rng.choice(pool) for _ in pool]
        try:
            ranking = rebuild(sample)
        except Exception as exc:  # noqa: BLE001 - a failed resample must not abort
            skipped += 1
            log.debug("bootstrap resample failed: %s", exc)
            continue
        _accumulate(store, _rank_positions(ranking))

    rows = _summarise_frequencies(store, resamples)
    log.info(
        "bootstrap: %d resamples (%d skipped), %d engineers tracked",
        resamples, skipped, len(rows),
    )
    return {
        "method": "bootstrap_over_episodes",
        "resamples": resamples,
        "skipped_resamples": skipped,
        "random_seed": seed,
        "results": rows,
        "note": (
            "Resampling an engineer's own episodes with replacement. Missing "
            "data widens these intervals; it never lowers the point estimate."
        ),
        "uncertainty_version": UNCERTAINTY_VERSION,
    }


def dirichlet_weights(
    rng: random.Random, base: Mapping[str, float], concentration: float
) -> dict[str, float]:
    """Perturb a weight vector while keeping it a valid simplex point."""
    draws = {
        name: rng.gammavariate(max(1e-6, concentration * weight), 1.0)
        for name, weight in base.items()
    }
    total = sum(draws.values()) or 1.0
    return {name: value / total for name, value in draws.items()}


def weight_sensitivity(
    config: Phase2Config,
    *,
    base_weights: Mapping[str, float],
    rank_with: Callable[[Mapping[str, float], Mapping[str, Mapping[str, float]] | None],
                        list[dict[str, Any]]],
) -> dict[str, Any]:
    """Perturb the weights; report how often the order survives."""
    trials = int(config.get("outranking.sensitivity.weight_perturbations"))
    concentration = float(
        config.get("outranking.sensitivity.weight_dirichlet_concentration")
    )
    seed = int(config.get("outranking.bootstrap.random_seed"))
    rng = random.Random(seed + 1)

    store: dict[str, dict[int, int]] = {}
    for _ in range(trials):
        weights = dirichlet_weights(rng, base_weights, concentration)
        _accumulate(store, _rank_positions(rank_with(weights, None)))

    return {
        "method": "dirichlet_weight_perturbation",
        "trials": trials,
        "concentration": concentration,
        "random_seed": seed + 1,
        "base_weights": dict(base_weights),
        "results": _summarise_frequencies(store, trials),
        "sensitivity_version": VERSION,
    }


def structural_sensitivity(
    config: Phase2Config,
    *,
    base_weights: Mapping[str, float],
    rank_with_variant: Callable[[Mapping[str, Any]], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Vary thresholds, confidence discounts, OWA coefficients and time treatment."""
    variants: list[dict[str, Any]] = []
    criteria = list(config.get("outranking.criteria"))

    for thresholds in config.get("outranking.sensitivity.threshold_variants"):
        variants.append(
            {
                "kind": "thresholds",
                "label": f"q={thresholds['q']} p={thresholds['p']} v={thresholds['v']}",
                "overrides": {
                    "thresholds": {c: dict(thresholds) for c in criteria}
                },
            }
        )
    for discounts in config.get("outranking.sensitivity.confidence_discount_variants"):
        variants.append(
            {
                "kind": "confidence_discount",
                "label": f"high={discounts['high']} medium={discounts['medium']} "
                         f"low={discounts['low']}",
                "overrides": {"rubric.confidence.discount": dict(discounts)},
            }
        )
    for coefficients in config.get("outranking.sensitivity.coefficient_variants"):
        variants.append(
            {
                "kind": "owa_coefficients",
                "label": str(coefficients),
                "overrides": {"outranking.aggregation.coefficients": list(coefficients)},
                "is_volume_control": coefficients == [1.0] * len(coefficients),
            }
        )
    for treatment in config.get("outranking.sensitivity.time_treatments"):
        variants.append(
            {
                "kind": "time_treatment",
                "label": str(treatment),
                "overrides": {"_decay_mode": treatment},
            }
        )

    store: dict[str, dict[int, int]] = {}
    per_variant: list[dict[str, Any]] = []
    for variant in variants:
        try:
            ranking = rank_with_variant(variant)
        except Exception as exc:  # noqa: BLE001
            per_variant.append(
                {**{k: v for k, v in variant.items() if k != "overrides"},
                 "error": str(exc)[:200]}
            )
            continue
        positions = _rank_positions(ranking)
        _accumulate(store, positions)
        per_variant.append(
            {
                **{k: v for k, v in variant.items() if k != "overrides"},
                "top5": [
                    {"actor_cluster_id": r["actor_cluster_id"], "login": r.get("login"),
                     "position": r["position"]}
                    for r in ranking[:5]
                ],
            }
        )

    return {
        "method": "one_at_a_time_structural_variation",
        "variants_run": len(per_variant),
        "variants": per_variant,
        "results": _summarise_frequencies(store, len(per_variant)),
        "note": (
            "The flat [1,1,1,1,1] coefficient variant is a deliberate "
            "volume-friendly control: if the top five survives it, the ranking "
            "is not an artefact of the diminishing-return curve."
        ),
        "sensitivity_version": VERSION,
    }


def combine(
    bootstrap: Mapping[str, Any],
    weights: Mapping[str, Any],
    structural: Mapping[str, Any],
) -> dict[str, Any]:
    """One stability record per engineer, across all three analyses."""
    by_actor: dict[str, dict[str, Any]] = defaultdict(dict)
    for source, payload in (
        ("bootstrap", bootstrap), ("weights", weights), ("structural", structural)
    ):
        for row in payload.get("results") or []:
            by_actor[str(row["actor_cluster_id"])][source] = row

    out: list[dict[str, Any]] = []
    for actor, sources in sorted(by_actor.items()):
        inclusions = [
            float(r.get("top5_inclusion_probability") or 0.0) for r in sources.values()
        ]
        stabilities = [float(r.get("rank_stability_index") or 0.0) for r in sources.values()]
        positions = [float(r.get("mean_position") or 0.0) for r in sources.values()]
        out.append(
            {
                "actor_cluster_id": actor,
                "mean_position_across_analyses": round(
                    sum(positions) / len(positions), 3
                ) if positions else None,
                "min_top5_inclusion_probability": round(min(inclusions), 4) if inclusions else None,
                "mean_top5_inclusion_probability": round(
                    sum(inclusions) / len(inclusions), 4
                ) if inclusions else None,
                "rank_stability_index": round(
                    sum(stabilities) / len(stabilities), 4
                ) if stabilities else None,
                "position_range": [
                    min(int(r.get("best_position") or 0) for r in sources.values()),
                    max(int(r.get("worst_position") or 0) for r in sources.values()),
                ],
                "per_analysis": sources,
            }
        )
    return {
        "engineers": out,
        "analyses": ["bootstrap", "weights", "structural"],
        "sensitivity_version": VERSION,
        "uncertainty_version": UNCERTAINTY_VERSION,
    }
