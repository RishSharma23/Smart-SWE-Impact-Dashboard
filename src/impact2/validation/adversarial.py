"""Validation item 7: adversarial tests against the failure modes by name.

Each test mutates the real dataset in a way that would fool a naive metric, then
re-runs the ranking and measures how much it moved.  The assertion is not "the
ranking never changes" — a genuinely different dataset may deserve a different
answer — but "the ranking is *materially stable*", measured as Kendall tau over
the full order plus overlap of the top five.

The five attacks, and what each one is trying to break:

* **episode splitting** — the same work submitted as ten PRs instead of one.
  Breaks anything that counts PRs. Should not move the ranking at all, because
  the unit of analysis is the episode.
* **generated migration** — a 40,000-line machine-generated file. Breaks
  anything that counts lines. Should not raise a band, because generated paths
  are labelled and excluded from production-file evidence.
* **trivial approvals** — a hundred "LGTM" comments. Breaks anything that
  counts reviews. Should not move collaborative amplification, whose only input
  is causally-confirmed interventions.
* **hidden counts** — every size and count column nulled. If the ranking
  changes, something was quietly reading a count.
* **bot activity** — a plausible bot with a full portfolio. Should be excluded
  entirely, not merely ranked low.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Mapping, Sequence

from ..config import Phase2Config
from ..ids import participant_id
from .agreement import kendall_tau, top_n_overlap

log = logging.getLogger("impact2.validation.adversarial")

# A mutation that leaves the top five untouched and barely reorders the tail is
# a pass. These are the thresholds the report is graded against.
TAU_PASS = 0.90
TOP5_PASS = 1.0


def _order(ranking: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(r["actor_cluster_id"]) for r in ranking]


def _result(
    name: str, description: str, baseline: Sequence[str], mutated: Sequence[str],
    *, expectation: str, extra: Mapping[str, Any] | None = None,
    tau_pass: float = TAU_PASS, top5_pass: float = TOP5_PASS,
) -> dict[str, Any]:
    tau = kendall_tau(baseline, mutated)
    overlap = top_n_overlap(baseline, mutated, 5)
    passed = (tau is None or tau >= tau_pass) and overlap >= top5_pass
    return {
        "test": name,
        "description": description,
        "expectation": expectation,
        "kendall_tau": tau,
        "top5_overlap": overlap,
        "baseline_top5": list(baseline[:5]),
        "mutated_top5": list(mutated[:5]),
        "thresholds": {"kendall_tau_min": tau_pass, "top5_overlap_min": top5_pass},
        "status": "pass" if passed else "fail",
        **(dict(extra) if extra else {}),
    }


def split_largest_episode(pipeline: Any) -> dict[str, Any]:
    """Submit one initiative as many PRs. A PR counter would reward this."""
    ranked = [e for e in pipeline.episodes if e.get("ranked") and int(e["pr_count"]) > 1]
    if not ranked:
        return {"test": "episode_splitting", "status": "skipped",
                "reason": "no multi-PR episode to split"}
    target = max(ranked, key=lambda e: int(e["pr_count"]))
    eid = str(target["episode_id"])

    episodes = [e for e in pipeline.episodes if str(e["episode_id"]) != eid]
    dimensions = [d for d in pipeline.dimensions if str(d["episode_id"]) != eid]
    participants = [p for p in pipeline.participants if str(p["episode_id"]) != eid]

    # One synthetic episode per member PR, each inheriting the original's bands.
    for number in target["pr_numbers"]:
        alias = f"{eid}#split{number}"
        clone = dict(target)
        clone.update({"episode_id": alias, "pr_numbers": [number], "pr_count": 1})
        episodes.append(clone)
        for row in pipeline.dimensions:
            if str(row["episode_id"]) == eid:
                dimensions.append({**row, "episode_id": alias})
        for row in pipeline.participants:
            if str(row["episode_id"]) == eid:
                participants.append(
                    {
                        **row,
                        "episode_id": alias,
                        "participant_id": participant_id(
                            alias, str(row["actor_cluster_id"])
                        ),
                    }
                )

    mutated = pipeline.rank_once(
        episodes=episodes, dimensions=dimensions, participants=participants
    )
    baseline = _order(pipeline.rank_once())
    return _result(
        "episode_splitting",
        f"episode {eid} ({target['pr_count']} PRs) resubmitted as "
        f"{target['pr_count']} single-PR episodes",
        baseline, _order(mutated),
        expectation=(
            "No movement: the aggregation caps corroboration from additional "
            "episodes, so splitting one arc cannot manufacture evidence."
        ),
        extra={"split_episode_id": eid, "split_into": int(target["pr_count"])},
    )


def inject_generated_migration(pipeline: Any, *, lines: int = 40000) -> dict[str, Any]:
    """A huge machine-generated file. A line counter would reward this."""
    baseline_ranking = pipeline.rank_once()
    baseline = _order(baseline_ranking)
    if not baseline:
        return {"test": "generated_migration", "status": "skipped",
                "reason": "no rankable engineer"}

    clone = pipeline.clone()
    victim = baseline[0]
    target_episode = next(
        (
            p["episode_id"] for p in clone.participants
            if str(p["actor_cluster_id"]) == victim
        ),
        None,
    )
    if target_episode is None:
        return {"test": "generated_migration", "status": "skipped",
                "reason": "top engineer has no attributable episode"}

    episode = next(e for e in clone.episodes if str(e["episode_id"]) == str(target_episode))
    number = int(episode["pr_numbers"][0])
    clone.files_by_pr.setdefault(number, [])
    for index in range(200):
        clone.files_by_pr[number].append(
            {
                "pr_number": number,
                "path": f"posthog/migrations/generated/{index:04d}_autogen.py",
                "change_status": "A",
                "additions": lines // 200,
                "deletions": 0,
                "is_generated": True,
                "is_migration": True,
                "is_test": False,
                "is_docs": False,
                "component": "platform:migrations",
                "language": "python",
                "risk_surfaces": ["migration", "schema"],
                "owners": [],
            }
        )
    clone.stage_analytics()
    clone.stage_attribute()
    clone.stage_dimensions()
    mutated = _order(clone.rank_once())

    before = _bands_for(pipeline.dimensions, str(target_episode))
    after = _bands_for(clone.dimensions, str(target_episode))
    raised = {k: (before.get(k), after.get(k)) for k in after if after[k] != before.get(k)}
    return _result(
        "generated_migration",
        f"{lines:,} lines of generated migration added to PR #{number}",
        baseline, mutated,
        expectation=(
            "No band rises: generated paths are labelled by Phase 1 and excluded "
            "from production-file evidence, and no rule reads line counts."
        ),
        extra={
            "injected_pr": number,
            "episode": str(target_episode),
            "bands_before": before,
            "bands_after": after,
            "bands_changed": raised,
            "band_stability": "pass" if not raised else "fail",
        },
    )


def inject_trivial_approvals(pipeline: Any, *, count: int = 100) -> dict[str, Any]:
    """A hundred LGTMs. A review counter would reward this."""
    baseline = _order(pipeline.rank_once())
    if not baseline:
        return {"test": "trivial_approvals", "status": "skipped",
                "reason": "no rankable engineer"}

    clone = pipeline.clone()
    beneficiary = baseline[-1] if len(baseline) > 1 else baseline[0]
    actor_id = next(
        (
            str(p.get("primary_actor_id"))
            for p in clone.participants
            if str(p["actor_cluster_id"]) == beneficiary
        ),
        beneficiary,
    )
    targets = [e for e in clone.episodes if e.get("ranked")][:20]
    injected = 0
    for episode in targets:
        number = int(episode["pr_numbers"][0])
        for index in range(count // max(1, len(targets)) + 1):
            injected += 1
            clone.interventions.append(
                {
                    "intervention_id": f"synthetic/lgtm/{number}/{index}",
                    "candidate_id": f"{number}:synthetic{index}",
                    "artifact_id": f"review_comment/synthetic{number}{index}",
                    "pr_number": number,
                    "thread_id": f"synthetic{number}{index}",
                    "url": None,
                    "commenter_actor_id": actor_id,
                    "pr_author_actor_id": (
                        clone.prs.get(number, {}).get("author_actor_id")
                    ),
                    "created_at": clone.prs.get(number, {}).get("created_at"),
                    "path": None,
                    "component": None,
                    "concern_classes": [],
                    "consequential_classes": [],
                    "concern_method": "deterministic_rule",
                    "comment_precedes_change": False,
                    "change_evidence": [],
                    "acknowledged_or_resolved": False,
                    "causal_confidence": "low",
                    "consequence_band": "none",
                    "is_consequential": False,
                    "body_excerpt": "LGTM",
                    "thread_is_resolved": False,
                }
            )
    clone.stage_attribute()
    clone.stage_dimensions()
    mutated = _order(clone.rank_once())

    collaboration_before = _dimension_values(pipeline, beneficiary,
                                             "collaborative_amplification")
    collaboration_after = _dimension_values(clone, beneficiary,
                                            "collaborative_amplification")
    return _result(
        "trivial_approvals",
        f"{injected} non-substantive approval comments attributed to one engineer",
        baseline, mutated,
        expectation=(
            "No movement: collaborative amplification reads only causally-"
            "confirmed interventions, and comment counts are not an input."
        ),
        extra={
            "injected_comments": injected,
            "beneficiary": beneficiary,
            "collaboration_before": collaboration_before,
            "collaboration_after": collaboration_after,
            "dimension_stability": (
                "pass" if collaboration_before == collaboration_after else "fail"
            ),
        },
    )


def hide_counts(pipeline: Any) -> dict[str, Any]:
    """Null every size and count column. Anything reading them will move."""
    baseline = _order(pipeline.rank_once())
    clone = pipeline.clone()
    hidden = (
        "github_additions", "github_deletions", "github_changed_files",
        "git_additions", "git_deletions", "git_file_count", "review_count",
        "review_thread_count", "comment_count", "commit_count", "reaction_count",
    )
    for pr in clone.prs.values():
        for column in hidden:
            pr[column] = None
    for rows in clone.files_by_pr.values():
        for row in rows:
            row["additions"] = None
            row["deletions"] = None
    clone.stage_analytics()
    clone.stage_attribute()
    clone.stage_dimensions()
    mutated = _order(clone.rank_once())
    return _result(
        "hidden_counts",
        f"nulled {len(hidden)} count/size columns plus per-file line counts",
        baseline, mutated,
        expectation=(
            "No movement: no rule reads a raw count or line total. Movement here "
            "would mean a count leaked into the evidence path."
        ),
        extra={"columns_nulled": list(hidden)},
    )


def inject_bot_activity(pipeline: Any) -> dict[str, Any]:
    """A prolific bot. It must be excluded, not merely ranked low."""
    baseline = _order(pipeline.rank_once())
    clone = pipeline.clone()
    bot_cluster = "github/user/synthetic-impact-bot[bot]"
    clone.actors[bot_cluster] = {
        "actor_id": bot_cluster, "login": "synthetic-impact-bot[bot]",
        "display_name": "Synthetic Impact Bot", "is_bot": True,
        "bot_probability": 1.0, "account_type": "bot",
        "identity_cluster_id": bot_cluster, "identity_cluster_size": 1,
        "ambiguity_status": "resolved", "ambiguity_reasons": [],
    }
    strongest = sorted(
        clone.participants,
        key=lambda p: -float(p.get("max_attribution_factor") or 0.0),
    )[:25]
    for row in strongest:
        clone.participants.append(
            {
                **row,
                "participant_id": participant_id(str(row["episode_id"]), bot_cluster),
                "actor_cluster_id": bot_cluster,
                "primary_actor_id": bot_cluster,
                "login": "synthetic-impact-bot[bot]",
                "display_name": "Synthetic Impact Bot",
                "is_bot": True,
                "share_category": "primary",
            }
        )
    mutated_ranking = clone.rank_once()
    mutated = _order(mutated_ranking)
    bot_present = bot_cluster in mutated
    result = _result(
        "bot_activity",
        "a bot identity given primary credit on the 25 strongest participations",
        baseline, mutated,
        expectation=(
            "The bot never appears in the ranking: bot exclusion happens at "
            "attribution time, and is disclosed rather than silent."
        ),
        extra={"bot_in_ranking": bot_present,
               "bot_position": mutated.index(bot_cluster) + 1 if bot_present else None},
    )
    if bot_present:
        result["status"] = "fail"
    return result


def _bands_for(dimensions: Sequence[Mapping[str, Any]], episode_id: str) -> dict[str, Any]:
    return {
        str(row["dimension"]): row.get("band")
        for row in dimensions
        if str(row["episode_id"]) == episode_id
    }


def _dimension_values(pipeline: Any, actor: str, dimension: str) -> Any:
    for portfolio in pipeline.portfolios or []:
        if str(portfolio["actor_cluster_id"]) == actor:
            return (portfolio.get("dimension_values") or {}).get(dimension)
    return None


def run_all(pipeline: Any) -> dict[str, Any]:
    tests = [
        split_largest_episode,
        inject_generated_migration,
        inject_trivial_approvals,
        hide_counts,
        inject_bot_activity,
    ]
    results: list[dict[str, Any]] = []
    for test in tests:
        try:
            results.append(test(pipeline))
        except Exception as exc:  # noqa: BLE001 - a failing probe is a finding
            log.exception("adversarial test %s errored", test.__name__)
            results.append(
                {"test": test.__name__, "status": "error", "error": str(exc)[:300]}
            )
    passed = sum(1 for r in results if r.get("status") == "pass")
    return {
        "tests": results,
        "passed": passed,
        "failed": sum(1 for r in results if r.get("status") == "fail"),
        "errored": sum(1 for r in results if r.get("status") == "error"),
        "skipped": sum(1 for r in results if r.get("status") == "skipped"),
        "status": (
            "pass" if passed == len([r for r in results if r.get("status") != "skipped"])
            else "fail"
        ),
    }
