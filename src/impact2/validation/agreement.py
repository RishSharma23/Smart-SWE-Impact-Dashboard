"""Inter-rater agreement statistics for ordinal bands.

Weighted Cohen's kappa (quadratic weights) and Krippendorff's alpha (ordinal
difference function), implemented directly so the arithmetic is inspectable and
so the ``unknown`` category is handled the way this project needs rather than
the way a library happens to.

The important modelling choice: ``unknown`` is its own category, not a missing
value and not band 0.  Two raters who both say "we cannot tell" agree, and that
agreement is real information about the evidence.  A rater who says 0 and one
who says unknown disagree by the maximum ordinal distance, because "there is
nothing" and "we cannot see" are opposite claims about the world.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

# Ordinal positions. `unknown` sits off the scale; its distance to any band is
# the full range, which is what makes 0-vs-unknown maximally discordant.
BANDS = [0, 1, 2, 3, 4]
UNKNOWN = "unknown"


def _category(value: Any) -> Any:
    return UNKNOWN if value is None else int(value)


def _ordinal_distance(a: Any, b: Any) -> float:
    if a == b:
        return 0.0
    if a == UNKNOWN or b == UNKNOWN:
        return float(len(BANDS) - 1)
    return abs(float(a) - float(b))


def weighted_cohens_kappa(
    pass_a: Mapping[str, Any], pass_b: Mapping[str, Any]
) -> dict[str, Any]:
    """Quadratic-weighted kappa over the shared subjects of two passes."""
    subjects = sorted(set(pass_a) & set(pass_b))
    if len(subjects) < 2:
        return {
            "kappa": None, "n": len(subjects),
            "reason": "fewer than two shared subjects",
        }

    categories = sorted(
        {_category(pass_a[s]) for s in subjects} | {_category(pass_b[s]) for s in subjects},
        key=lambda c: (c == UNKNOWN, c),
    )
    index = {c: i for i, c in enumerate(categories)}
    n = len(subjects)
    max_distance = max(
        _ordinal_distance(a, b) for a in categories for b in categories
    ) or 1.0

    observed = 0.0
    counts_a: dict[Any, int] = defaultdict(int)
    counts_b: dict[Any, int] = defaultdict(int)
    for subject in subjects:
        a, b = _category(pass_a[subject]), _category(pass_b[subject])
        counts_a[a] += 1
        counts_b[b] += 1
        weight = 1.0 - (_ordinal_distance(a, b) / max_distance) ** 2
        observed += weight
    observed /= n

    expected = 0.0
    for a in categories:
        for b in categories:
            probability = (counts_a[a] / n) * (counts_b[b] / n)
            weight = 1.0 - (_ordinal_distance(a, b) / max_distance) ** 2
            expected += probability * weight

    if abs(1.0 - expected) < 1e-12:
        return {
            "kappa": None, "n": n, "observed_agreement": round(observed, 6),
            "expected_agreement": round(expected, 6),
            "reason": "expected agreement is 1.0; kappa is undefined (no variance)",
        }

    kappa = (observed - expected) / (1.0 - expected)
    return {
        "kappa": round(kappa, 6),
        "n": n,
        "observed_agreement": round(observed, 6),
        "expected_agreement": round(expected, 6),
        "exact_agreement_rate": round(
            sum(1 for s in subjects
                if _category(pass_a[s]) == _category(pass_b[s])) / n, 6
        ),
        "within_one_band_rate": round(
            sum(1 for s in subjects
                if _ordinal_distance(_category(pass_a[s]), _category(pass_b[s])) <= 1) / n,
            6,
        ),
        "weighting": "quadratic",
        "interpretation": _interpret(kappa),
    }


def krippendorff_alpha(
    ratings: Mapping[str, Sequence[Any]]
) -> dict[str, Any]:
    """Ordinal alpha over subjects rated by two or more passes."""
    usable = {s: [r for r in v] for s, v in ratings.items() if len(v) >= 2}
    if len(usable) < 2:
        return {"alpha": None, "n": len(usable),
                "reason": "fewer than two subjects with two or more ratings"}

    pairs: list[tuple[Any, Any]] = []
    for values in usable.values():
        for i, a in enumerate(values):
            for b in values[i + 1:]:
                pairs.append((_category(a), _category(b)))
    if not pairs:
        return {"alpha": None, "n": len(usable), "reason": "no comparable pairs"}

    observed = sum(_ordinal_distance(a, b) ** 2 for a, b in pairs) / len(pairs)

    everything = [_category(v) for values in usable.values() for v in values]
    expected_pairs = [
        (a, b) for i, a in enumerate(everything) for b in everything[i + 1:]
    ]
    expected = (
        sum(_ordinal_distance(a, b) ** 2 for a, b in expected_pairs) / len(expected_pairs)
        if expected_pairs else 0.0
    )
    if expected == 0:
        return {"alpha": None, "n": len(usable),
                "reason": "no disagreement is possible; alpha is undefined"}

    alpha = 1.0 - (observed / expected)
    return {
        "alpha": round(alpha, 6),
        "n": len(usable),
        "observed_disagreement": round(observed, 6),
        "expected_disagreement": round(expected, 6),
        "metric": "ordinal (squared difference, unknown off-scale)",
        "interpretation": _interpret(alpha),
    }


def _interpret(value: float) -> str:
    if value >= 0.80:
        return "strong agreement"
    if value >= 0.67:
        return "acceptable for tentative conclusions"
    if value >= 0.40:
        return "moderate; conclusions need human confirmation"
    if value >= 0.0:
        return "weak; the two passes largely disagree"
    return "worse than chance"


def confusion(
    pass_a: Mapping[str, Any], pass_b: Mapping[str, Any]
) -> list[dict[str, Any]]:
    subjects = sorted(set(pass_a) & set(pass_b))
    matrix: dict[tuple[Any, Any], int] = defaultdict(int)
    for subject in subjects:
        matrix[(_category(pass_a[subject]), _category(pass_b[subject]))] += 1
    return [
        {"pass_a": str(a), "pass_b": str(b), "count": count}
        for (a, b), count in sorted(matrix.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))
    ]


def kendall_tau(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Rank correlation between two orderings of the same items."""
    common = [x for x in a if x in set(b)]
    if len(common) < 2:
        return None
    pos_a = {x: i for i, x in enumerate(a)}
    pos_b = {x: i for i, x in enumerate(b)}
    concordant = discordant = 0
    for i, x in enumerate(common):
        for y in common[i + 1:]:
            sign_a = pos_a[x] - pos_a[y]
            sign_b = pos_b[x] - pos_b[y]
            if sign_a * sign_b > 0:
                concordant += 1
            elif sign_a * sign_b < 0:
                discordant += 1
    total = concordant + discordant
    return round((concordant - discordant) / total, 6) if total else None


def top_n_overlap(a: Sequence[str], b: Sequence[str], n: int = 5) -> float:
    top_a, top_b = set(a[:n]), set(b[:n])
    return round(len(top_a & top_b) / max(1, len(top_a)), 4)
