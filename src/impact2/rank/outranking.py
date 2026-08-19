"""ELECTRE III outranking, cross-checked with PROMETHEE II and PROMETHEE III.

Why not a score
---------------
A single 0–1000 number would have to encode a trade-off between "shipped a
product surface" and "prevented a data-loss bug" as an exchange rate, and there
is no honest exchange rate.  Outranking asks a smaller, answerable question
instead: *given these criteria and these preference thresholds, is there enough
evidence that engineer a is at least as good as engineer b?*  The answer is a
credibility value with the concordance and discordance that produced it, which
is a thing a person can argue with.

The model
---------
For each criterion j with indifference ``q``, preference ``p`` and veto ``v``:

    concordance   c_j(a,b) = 1                                if g_j(a) >= g_j(b) - q
                             0                                if g_j(a) <= g_j(b) - p
                             (p - (g_j(b) - g_j(a))) / (p - q) otherwise

    discordance   d_j(a,b) = 0                                if g_j(b) <= g_j(a) + p
                             1                                if g_j(b) >= g_j(a) + v
                             linear                            otherwise

    C(a,b) = sum_j w_j c_j(a,b)   (weights normalised over the criteria in play)

    credibility(a,b) = C(a,b) * prod over j where d_j > C of (1-d_j)/(1-C)

Unknown criteria are *excluded from the pair* and recorded — never scored as
zero.  If engineer a has no leverage evidence because their work is in Rust,
which has no import parser, that is a gap in the data, and the model refuses to
read it as a weakness.

Ranking comes from Roy's descending/ascending distillation, which produces two
complete preorders whose intersection is a partial preorder.  Ties in that
partial preorder become *tiers*, which is the honest shape of the answer: some
engineers are genuinely incomparable on this evidence.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from ..config import Phase2Config
from ..ids import config_digest, ranking_run_id
from ..versions import derivation_version

log = logging.getLogger("impact2.rank")

VERSION = derivation_version("outranking")


# --------------------------------------------------------------------------
# pairwise primitives
# --------------------------------------------------------------------------


def concordance_j(ga: float, gb: float, q: float, p: float) -> float:
    if ga >= gb - q:
        return 1.0
    if ga <= gb - p:
        return 0.0
    return round((p - (gb - ga)) / (p - q), 6) if p > q else 0.0


def discordance_j(ga: float, gb: float, p: float, v: float) -> float:
    if gb <= ga + p:
        return 0.0
    if gb >= ga + v:
        return 1.0
    return round((gb - ga - p) / (v - p), 6) if v > p else 0.0


def credibility(concordance: float, discordances: Mapping[str, float]) -> tuple[float, list[str]]:
    """C(a,b) damped by every criterion whose discordance exceeds it."""
    value = concordance
    vetoing: list[str] = []
    for criterion, d in sorted(discordances.items()):
        if d > concordance and concordance < 1.0:
            value *= (1.0 - d) / (1.0 - concordance)
            vetoing.append(criterion)
        elif d >= 1.0:
            value = 0.0
            vetoing.append(criterion)
    return round(max(0.0, min(1.0, value)), 6), vetoing


class OutrankingModel:
    def __init__(
        self,
        config: Phase2Config,
        *,
        weights: Mapping[str, float],
        thresholds: Mapping[str, Mapping[str, float]] | None = None,
        counterevidence: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.config = config
        self.weights = dict(weights)
        criteria_cfg = config.get("outranking.criteria")
        self.thresholds = {
            name: {
                "q": float((thresholds or {}).get(name, {}).get("q", cfg["q"])),
                "p": float((thresholds or {}).get(name, {}).get("p", cfg["p"])),
                "v": float((thresholds or {}).get(name, {}).get("v", cfg["v"])),
            }
            for name, cfg in criteria_cfg.items()
        }
        self.criteria = list(criteria_cfg)
        self.counterevidence = dict(counterevidence or {})
        self.veto_cfg = config.get("outranking.veto.counterevidence_veto")

    # -- one pair ---------------------------------------------------------
    def compare(
        self, a: Mapping[str, Any], b: Mapping[str, Any]
    ) -> dict[str, Any]:
        values_a = a.get("dimension_values") or {}
        values_b = b.get("dimension_values") or {}

        per_criterion: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        concordance_sum = 0.0
        weight_sum = 0.0
        discordances: dict[str, float] = {}

        for criterion in self.criteria:
            ga, gb = values_a.get(criterion), values_b.get(criterion)
            if ga is None or gb is None:
                # Unknown is not zero. Drop the criterion from this pair and say so.
                excluded.append(
                    {
                        "criterion": criterion,
                        "reason": (
                            f"unknown for {'both' if ga is None and gb is None else ('a' if ga is None else 'b')}"
                        ),
                        "a_unknown_reason": (
                            (a.get("dimension_detail") or {}).get(criterion, {}).get("unknown_reason")
                            if ga is None else None
                        ),
                        "b_unknown_reason": (
                            (b.get("dimension_detail") or {}).get(criterion, {}).get("unknown_reason")
                            if gb is None else None
                        ),
                    }
                )
                continue
            t = self.thresholds[criterion]
            weight = float(self.weights.get(criterion, 0.0))
            c = concordance_j(float(ga), float(gb), t["q"], t["p"])
            d = discordance_j(float(ga), float(gb), t["p"], t["v"])
            concordance_sum += weight * c
            weight_sum += weight
            if d > 0:
                discordances[criterion] = d
            per_criterion.append(
                {
                    "criterion": criterion,
                    "a_value": round(float(ga), 4),
                    "b_value": round(float(gb), 4),
                    "difference": round(float(ga) - float(gb), 4),
                    "weight": round(weight, 6),
                    "concordance": c,
                    "discordance": d,
                    "thresholds": t,
                }
            )

        if weight_sum <= 0:
            return {
                "a": a.get("actor_cluster_id"), "b": b.get("actor_cluster_id"),
                "concordance": None, "credibility": None,
                "per_criterion": [], "excluded_criteria": excluded,
                "vetoing_criteria": [], "counterevidence_veto": False,
                "explanation": (
                    "No criterion is assessable for both engineers, so no "
                    "comparison is possible. This is a data gap, not a tie."
                ),
                "comparable": False,
                "outranking_version": VERSION,
            }

        concordance = round(concordance_sum / weight_sum, 6)
        value, vetoing = credibility(concordance, discordances)

        # Counterevidence veto: severe, high-confidence, and the outranking is
        # carried by a single criterion.
        counter_veto, counter_reason = self._counterevidence_veto(a, per_criterion)
        if counter_veto:
            value = 0.0
            vetoing.append("counterevidence")

        return {
            "a": a.get("actor_cluster_id"),
            "b": b.get("actor_cluster_id"),
            "a_login": a.get("login"),
            "b_login": b.get("login"),
            "concordance": concordance,
            "credibility": value,
            "per_criterion": per_criterion,
            "excluded_criteria": excluded,
            "vetoing_criteria": sorted(set(vetoing)),
            "counterevidence_veto": counter_veto,
            "counterevidence_veto_reason": counter_reason,
            "comparable": True,
            "explanation": self._explain(
                a, b, concordance, value, per_criterion, excluded, vetoing, counter_reason
            ),
            "outranking_version": VERSION,
        }

    def _counterevidence_veto(
        self, a: Mapping[str, Any], per_criterion: Sequence[Mapping[str, Any]]
    ) -> tuple[bool, str | None]:
        if not self.veto_cfg.get("enabled"):
            return False, None
        record = self.counterevidence.get(str(a.get("actor_cluster_id"))) or {}
        severe = record.get("severe_events") or []
        if not severe:
            return False, None
        if self.veto_cfg.get("only_when_single_criterion_carries"):
            carrying = [c for c in per_criterion if c["concordance"] > 0.5 and c["weight"] > 0]
            if len(carrying) > 1:
                return False, None
        return True, (
            f"{a.get('login') or a.get('actor_cluster_id')} carries severe "
            f"high-confidence counterevidence ({severe[0].get('detail')}), and the "
            "outranking rests on a single criterion"
        )

    def _explain(
        self,
        a: Mapping[str, Any],
        b: Mapping[str, Any],
        concordance: float,
        value: float,
        per_criterion: Sequence[Mapping[str, Any]],
        excluded: Sequence[Mapping[str, Any]],
        vetoing: Sequence[str],
        counter_reason: str | None,
    ) -> str:
        """Plain English, published for every top-five pairwise outcome."""
        name_a = a.get("login") or a.get("actor_cluster_id")
        name_b = b.get("login") or b.get("actor_cluster_id")
        favouring = sorted(
            (c for c in per_criterion if c["difference"] > 0),
            key=lambda c: -c["difference"] * c["weight"],
        )[:2]
        against = sorted(
            (c for c in per_criterion if c["difference"] < 0),
            key=lambda c: c["difference"] * c["weight"],
        )[:2]

        parts: list[str] = []
        if favouring:
            parts.append(
                "ahead on "
                + ", ".join(
                    f"{c['criterion'].replace('_', ' ')} "
                    f"({c['a_value']:.2f} vs {c['b_value']:.2f})"
                    for c in favouring
                )
            )
        if against:
            parts.append(
                "behind on "
                + ", ".join(
                    f"{c['criterion'].replace('_', ' ')} "
                    f"({c['a_value']:.2f} vs {c['b_value']:.2f})"
                    for c in against
                )
            )
        summary = f"{name_a} is " + "; ".join(parts) if parts else (
            f"{name_a} and {name_b} are level on every assessable criterion"
        )
        summary += f". Concordance {concordance:.2f}, credibility {value:.2f}."
        if vetoing:
            summary += (
                f" Credibility is damped by a veto-level gap on "
                f"{', '.join(sorted(set(vetoing)))}."
            )
        if counter_reason:
            summary += f" {counter_reason}."
        if excluded:
            summary += (
                f" {len(excluded)} criterion/criteria excluded as unknown for one "
                "or both: unknown evidence is not scored as zero."
            )
        return summary

    # -- full matrix ------------------------------------------------------
    def matrix(
        self, portfolios: Sequence[Mapping[str, Any]]
    ) -> tuple[dict[tuple[str, str], float], list[dict[str, Any]]]:
        comparisons: list[dict[str, Any]] = []
        credibilities: dict[tuple[str, str], float] = {}
        for a in portfolios:
            for b in portfolios:
                key_a, key_b = str(a["actor_cluster_id"]), str(b["actor_cluster_id"])
                if key_a == key_b:
                    continue
                result = self.compare(a, b)
                comparisons.append(result)
                credibilities[(key_a, key_b)] = float(result["credibility"] or 0.0)
        return credibilities, comparisons


# --------------------------------------------------------------------------
# Roy distillation
# --------------------------------------------------------------------------


def distill(
    credibilities: Mapping[tuple[str, str], float],
    alternatives: Sequence[str],
    *,
    alpha: float,
    beta: float,
    max_iterations: int = 60,
    descending: bool = True,
) -> list[list[str]]:
    """One distillation chain. Returns an ordered list of equivalence classes."""
    remaining = list(alternatives)
    chain: list[list[str]] = []
    iterations = 0

    while remaining and iterations < max_iterations:
        iterations += 1
        subset = list(remaining)
        while True:
            values = [
                credibilities.get((a, b), 0.0)
                for a in subset for b in subset if a != b
            ]
            lam = max(values) if values else 0.0
            if lam <= 0:
                chosen = sorted(subset)
                break
            cut = lam - (alpha - beta * lam)
            strength = {a: 0 for a in subset}
            weakness = {a: 0 for a in subset}
            for a in subset:
                for b in subset:
                    if a == b:
                        continue
                    ab = credibilities.get((a, b), 0.0)
                    ba = credibilities.get((b, a), 0.0)
                    if ab > cut and ab > ba + (alpha - beta * ab):
                        strength[a] += 1
                        weakness[b] += 1
            qualification = {a: strength[a] - weakness[a] for a in subset}
            best = (max if descending else min)(qualification.values())
            candidates = sorted(a for a in subset if qualification[a] == best)
            if len(candidates) == len(subset) or len(candidates) == 1:
                chosen = candidates
                break
            subset = candidates
        chain.append(sorted(chosen))
        remaining = [a for a in remaining if a not in set(chosen)]
    if remaining:
        chain.append(sorted(remaining))
    return chain if descending else list(reversed(chain))


def _positions(chain: Sequence[Sequence[str]]) -> dict[str, int]:
    return {a: index for index, group in enumerate(chain) for a in group}


def final_ranking(
    credibilities: Mapping[tuple[str, str], float],
    alternatives: Sequence[str],
    config: Phase2Config,
) -> list[dict[str, Any]]:
    """Intersect the two distillations into a partial preorder, then tier it."""
    alpha = float(config.get("outranking.distillation.alpha"))
    beta = float(config.get("outranking.distillation.beta"))
    max_iterations = int(config.get("outranking.distillation.max_iterations"))

    down = distill(credibilities, alternatives, alpha=alpha, beta=beta,
                   max_iterations=max_iterations, descending=True)
    up = distill(credibilities, alternatives, alpha=alpha, beta=beta,
                 max_iterations=max_iterations, descending=False)
    down_pos, up_pos = _positions(down), _positions(up)

    # Median preorder: average of the two positions, ties become one tier.
    scored = sorted(
        alternatives,
        key=lambda a: (
            (down_pos.get(a, len(down)) + up_pos.get(a, len(up))) / 2.0, a
        ),
    )
    rows: list[dict[str, Any]] = []
    tier = 0
    previous: float | None = None
    for index, actor in enumerate(scored):
        average = (down_pos.get(actor, len(down)) + up_pos.get(actor, len(up))) / 2.0
        if previous is None or abs(average - previous) > 1e-9:
            tier += 1
            previous = average
        # Incomparable pairs are those where neither outranks the other.
        incomparable = sorted(
            other for other in alternatives
            if other != actor
            and credibilities.get((actor, other), 0.0) < 0.5
            and credibilities.get((other, actor), 0.0) < 0.5
        )
        rows.append(
            {
                "actor_cluster_id": actor,
                "position": index + 1,
                "tier": tier,
                "descending_position": down_pos.get(actor),
                "ascending_position": up_pos.get(actor),
                "median_position": round(average, 4),
                "incomparable_with": incomparable[:10],
                "incomparable_count": len(incomparable),
                "outranking_version": VERSION,
            }
        )
    return rows


# --------------------------------------------------------------------------
# PROMETHEE II cross-check
# --------------------------------------------------------------------------


def _pairwise_preferences(
    portfolios: Sequence[Mapping[str, Any]],
    weights: Mapping[str, float],
    thresholds: Mapping[str, Mapping[str, float]],
) -> dict[str, list[tuple[float, float]]]:
    """Per alternative, the (pref(a,b), pref(b,a)) pair for every other b.

    Shared by both PROMETHEE variants so they cannot drift apart: II averages
    these, III also takes their dispersion. A pair on which no criterion is
    comparable (both values unknown on every criterion, so ``weight_sum`` is
    zero) contributes ``(0, 0)`` rather than being dropped, which is what makes
    PROMETHEE III's net flow identical to PROMETHEE II's by construction.
    """
    keys = [str(p["actor_cluster_id"]) for p in portfolios]
    by_key = {str(p["actor_cluster_id"]): p for p in portfolios}
    out: dict[str, list[tuple[float, float]]] = {k: [] for k in keys}

    for a in keys:
        for b in keys:
            if a == b:
                continue
            pref_ab = 0.0
            pref_ba = 0.0
            weight_sum = 0.0
            for criterion, weight in weights.items():
                ga = (by_key[a].get("dimension_values") or {}).get(criterion)
                gb = (by_key[b].get("dimension_values") or {}).get(criterion)
                if ga is None or gb is None:
                    continue
                t = thresholds[criterion]
                q, p = t["q"], t["p"]
                diff = float(ga) - float(gb)
                pref_ab += weight * _linear_preference(diff, q, p)
                pref_ba += weight * _linear_preference(-diff, q, p)
                weight_sum += weight
            if weight_sum > 0:
                out[a].append((pref_ab / weight_sum, pref_ba / weight_sum))
            else:
                out[a].append((0.0, 0.0))
    return out


def promethee_ii(
    portfolios: Sequence[Mapping[str, Any]],
    weights: Mapping[str, float],
    thresholds: Mapping[str, Mapping[str, float]],
) -> list[dict[str, Any]]:
    """Net-flow ranking, run independently as a sanity check on ELECTRE III.

    Different aggregation logic, same inputs. When the two disagree about the
    top five, that disagreement is reported rather than hidden, because it means
    the result is sensitive to the aggregation choice.

    Note what this method cannot do: it sorts by net flow, so it always emits a
    strict total order and breaks ties it has no basis to break. Where ELECTRE
    III reports a shared tier, PROMETHEE II is obliged to pick a winner.
    PROMETHEE III below exists to say "these two are inseparable" in the same
    vocabulary ELECTRE III uses.
    """
    keys = [str(p["actor_cluster_id"]) for p in portfolios]
    if len(keys) < 2:
        return [
            {"actor_cluster_id": k, "net_flow": 0.0, "position": 1,
             "positive_flow": 0.0, "negative_flow": 0.0}
            for k in keys
        ]

    pairs = _pairwise_preferences(portfolios, weights, thresholds)
    n = len(keys) - 1
    rows = [
        {
            "actor_cluster_id": k,
            "positive_flow": round(sum(pos for pos, _ in pairs[k]) / n, 6),
            "negative_flow": round(sum(neg for _, neg in pairs[k]) / n, 6),
            "net_flow": round(
                sum(pos - neg for pos, neg in pairs[k]) / n, 6
            ),
        }
        for k in keys
    ]
    rows.sort(key=lambda r: (-r["net_flow"], r["actor_cluster_id"]))
    for index, row in enumerate(rows):
        row["position"] = index + 1
    return rows


def promethee_iii(
    portfolios: Sequence[Mapping[str, Any]],
    weights: Mapping[str, float],
    thresholds: Mapping[str, Mapping[str, float]],
    *,
    alpha: float,
) -> list[dict[str, Any]]:
    """Interval order on net flow (Brans and Vincke). The second cross-check.

    PROMETHEE II sorts by net flow and therefore always produces a strict total
    order, breaking ties it has no basis to break. ELECTRE III deliberately does
    the opposite: it reports shared tiers and incomparability when the evidence
    cannot separate two people. So whenever ELECTRE III is right that two
    contributors are inseparable, PROMETHEE II is forced to order them and the
    agreement figure records a disagreement that is an artifact of the
    cross-check rather than a fact about the data.

    PROMETHEE III removes that artifact. Each alternative gets an interval
    around its net flow::

        phi(a)   = mean over b of [ pref(a,b) - pref(b,a) ]
        sigma(a) = population standard deviation of the same values
        [x_a, y_a] = [ phi(a) - alpha*sigma(a), phi(a) + alpha*sigma(a) ]

        a P b  (a strictly preferred)  iff  x_a > y_b
        a I b  (indifferent)           iff  the intervals overlap

    ``phi`` here is exactly PROMETHEE II's net flow, so the two methods differ
    only in whether they admit indifference. ``sigma`` is the dispersion of the
    pairwise comparisons that produced the flow: an alternative that beats some
    and loses to others has a wide interval and is harder to place than one that
    beat everyone by the same margin, which is the honest reading.

    The dispersion is computed here rather than taken from the bootstrap in the
    sensitivity stage, deliberately. The bootstrap is optional
    (``--skip-sensitivity``) and is absent from the current export, so sourcing
    the intervals from it would make them null exactly when they are most
    needed. This formulation is self-contained and always available.

    Position is standard competition ranking over the strict relation:
    ``1 + |{b : b P a}|``. Mutually indifferent alternatives therefore share a
    position, the same way ELECTRE III lets them share a tier.
    """
    keys = [str(p["actor_cluster_id"]) for p in portfolios]
    if len(keys) < 2:
        return [
            {"actor_cluster_id": k, "net_flow": 0.0, "dispersion": 0.0,
             "interval": [0.0, 0.0], "position": 1,
             "indifferent_with": [], "indifferent_count": 0}
            for k in keys
        ]

    pairs = _pairwise_preferences(portfolios, weights, thresholds)
    n = len(keys) - 1

    bounds: dict[str, tuple[float, float]] = {}
    flows: dict[str, float] = {}
    dispersions: dict[str, float] = {}
    for k in keys:
        diffs = [pos - neg for pos, neg in pairs[k]]
        phi = sum(diffs) / n
        variance = sum((d - phi) ** 2 for d in diffs) / n
        sigma = math.sqrt(variance)
        half = alpha * sigma
        flows[k] = phi
        dispersions[k] = sigma
        bounds[k] = (phi - half, phi + half)

    # a P b iff x_a > y_b. Anything else is indifference, which is the whole
    # point: the intervals overlap and the method declines to separate them.
    strictly_better_than: dict[str, set[str]] = {k: set() for k in keys}
    for a in keys:
        for b in keys:
            if a != b and bounds[a][0] > bounds[b][1]:
                strictly_better_than[a].add(b)

    rows: list[dict[str, Any]] = []
    for k in keys:
        beaten_by = sorted(a for a in keys if k in strictly_better_than[a])
        indifferent = sorted(
            b for b in keys
            if b != k
            and b not in strictly_better_than[k]
            and k not in strictly_better_than[b]
        )
        rows.append(
            {
                "actor_cluster_id": k,
                "net_flow": round(flows[k], 6),
                "dispersion": round(dispersions[k], 6),
                "interval": [round(bounds[k][0], 6), round(bounds[k][1], 6)],
                "position": len(beaten_by) + 1,
                "indifferent_with": indifferent[:10],
                "indifferent_count": len(indifferent),
            }
        )
    rows.sort(key=lambda r: (r["position"], -r["net_flow"], r["actor_cluster_id"]))
    return rows


def _linear_preference(diff: float, q: float, p: float) -> float:
    if diff <= q:
        return 0.0
    if diff >= p:
        return 1.0
    return (diff - q) / (p - q) if p > q else 1.0


def run_scenario(
    config: Phase2Config,
    portfolios: Sequence[Mapping[str, Any]],
    *,
    scenario: str,
    weights: Mapping[str, float],
    thresholds: Mapping[str, Mapping[str, float]] | None = None,
    counterevidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """One complete ranking run: matrix, distillation, cross-check."""
    rankable = [p for p in portfolios if p.get("rankable")]
    model = OutrankingModel(
        config, weights=weights, thresholds=thresholds, counterevidence=counterevidence
    )
    credibilities, comparisons = model.matrix(rankable)
    keys = [str(p["actor_cluster_id"]) for p in rankable]
    ranking = final_ranking(credibilities, keys, config)
    cross = promethee_ii(rankable, weights, model.thresholds)
    alpha_iii = float(config.get("outranking.promethee_iii.alpha"))
    cross_iii = promethee_iii(
        rankable, weights, model.thresholds, alpha=alpha_iii
    )

    by_key = {str(p["actor_cluster_id"]): p for p in rankable}
    cross_positions = {r["actor_cluster_id"]: r["position"] for r in cross}
    cross_iii_positions = {r["actor_cluster_id"]: r["position"] for r in cross_iii}
    for row in ranking:
        portfolio = by_key[row["actor_cluster_id"]]
        actor = row["actor_cluster_id"]
        row.update(
            {
                "scenario": scenario,
                "login": portfolio.get("login"),
                "display_name": portfolio.get("display_name"),
                "dimension_values": portfolio.get("dimension_values"),
                "cross_check_position": cross_positions.get(actor),
                "cross_check_delta": (
                    cross_positions.get(actor, 0) - row["position"]
                ),
                "cross_check_iii_position": cross_iii_positions.get(actor),
                "cross_check_iii_delta": (
                    cross_iii_positions.get(actor, 0) - row["position"]
                ),
            }
        )

    top5_electre = [r["actor_cluster_id"] for r in ranking[:5]]
    top5_promethee = [r["actor_cluster_id"] for r in cross[:5]]
    top5_promethee_iii = [r["actor_cluster_id"] for r in cross_iii[:5]]
    agreement = len(set(top5_electre) & set(top5_promethee)) / max(1, len(top5_electre))
    agreement_iii = (
        len(set(top5_electre) & set(top5_promethee_iii)) / max(1, len(top5_electre))
    )

    # The reason PROMETHEE III is here. Among the top five, count the pairs
    # ELECTRE III placed in one tier, then count how many of those PROMETHEE III
    # also declines to separate. PROMETHEE II cannot agree with any of them: it
    # emits a total order, so every shared tier reads as a disagreement whether
    # or not the evidence supports one.
    tier_of = {r["actor_cluster_id"]: r["tier"] for r in ranking[:5]}
    indifferent_of = {
        r["actor_cluster_id"]: set(r.get("indifferent_with") or [])
        for r in cross_iii
    }
    shared_tier_pairs = 0
    shared_tier_confirmed = 0
    for i, a in enumerate(top5_electre):
        for b in top5_electre[i + 1:]:
            if tier_of.get(a) != tier_of.get(b):
                continue
            shared_tier_pairs += 1
            if b in indifferent_of.get(a, set()) or a in indifferent_of.get(b, set()):
                shared_tier_confirmed += 1

    digest = config_digest(
        {"weights": dict(weights), "thresholds": model.thresholds, "scenario": scenario}
    )
    log.info(
        "scenario %-24s ranked %d engineers; top-5 agreement: PROMETHEE II "
        "%.0f%%, PROMETHEE III %.0f%% (%d/%d shared tiers confirmed, "
        "%d distinct positions of %d)",
        scenario, len(ranking), agreement * 100, agreement_iii * 100,
        shared_tier_confirmed, shared_tier_pairs,
        len({r["position"] for r in cross_iii}), len(cross_iii),
    )
    return {
        "ranking_run_id": ranking_run_id(scenario, digest),
        "scenario": scenario,
        "config_digest": digest,
        "weights": dict(weights),
        "thresholds": model.thresholds,
        "alternatives": len(rankable),
        "excluded_insufficient_evidence": len(portfolios) - len(rankable),
        "ranking": ranking,
        "comparisons": comparisons,
        # `cross_check` is PROMETHEE II and keeps its exact shape: the export,
        # the contract and the UI all read it. `cross_checks` is the full list
        # and is what a consumer should move to.
        "cross_check": {
            "method": "promethee_ii",
            "ranking": cross,
            "top5_agreement": round(agreement, 4),
            "note": (
                "PROMETHEE II uses different aggregation logic on the same "
                "inputs. Disagreement means the result is sensitive to the "
                "aggregation choice and is reported, not hidden."
            ),
        },
        "cross_checks": [
            {
                "method": "promethee_ii",
                "ranking": cross,
                "top5_agreement": round(agreement, 4),
                "admits_indifference": False,
                "note": (
                    "Net-flow ranking. It emits a strict total order, so it "
                    "cannot report that two contributors are inseparable and "
                    "will order them regardless."
                ),
            },
            {
                "method": "promethee_iii",
                "ranking": cross_iii,
                "top5_agreement": round(agreement_iii, 4),
                "admits_indifference": True,
                "alpha": alpha_iii,
                "indifferent_pairs": sum(
                    r["indifferent_count"] for r in cross_iii
                ) // 2,
                "distinct_positions": len({r["position"] for r in cross_iii}),
                "shared_tier_pairs_top5": shared_tier_pairs,
                "shared_tier_confirmed_top5": shared_tier_confirmed,
                "note": (
                    "Interval order on the same net flow. Where it and "
                    "ELECTRE III both decline to separate two contributors, "
                    "that is a stronger statement than either alone. Where "
                    "PROMETHEE II disagrees with a shared tier, check whether "
                    "it simply had no way to express one."
                ),
            },
        ],
        "outranking_version": VERSION,
    }


def summarise(runs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(runs)
    return {
        "scenarios": len(items),
        "by_scenario": {
            str(r["scenario"]): {
                "alternatives": r["alternatives"],
                "top5": [
                    {"login": x.get("login"), "position": x["position"], "tier": x["tier"]}
                    for x in r["ranking"][:5]
                ],
                "cross_check_agreement": r["cross_check"]["top5_agreement"],
                "cross_check_agreement_by_method": {
                    c["method"]: c["top5_agreement"]
                    for c in (r.get("cross_checks") or [])
                },
            }
            for r in items
        },
        "outranking_version": VERSION,
    }
