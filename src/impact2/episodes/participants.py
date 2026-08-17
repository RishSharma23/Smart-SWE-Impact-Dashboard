"""Role-aware attribution: who did what, and how much of it enters whose name.

Two failure modes this module exists to prevent.

**Sole-owner attribution.**  A PR author is not automatically the originator of
the idea, the person who made the key decision, or the one who kept it working.
Roles are inferred from separate evidence — issue authorship, code
contribution, causally-confirmed review interventions, follow-up work,
documentation — and an engineer can hold several, or none.

**Double counting.**  The episode's outcome is scored once, in
``episode_dimensions``.  This module produces *attribution factors*: per
participant, per dimension, how much of that episode's evidentiary mass enters
their portfolio.  Five participants on one band-3 episode do not produce five
band-3 portfolios; the factors are combined with ``max`` across a person's
roles (stacking roles cannot manufacture credit) and the episode's total factor
per dimension is capped, so an episode can never be worth more than itself.

Shared credit is reported to the UI as a category — primary, material,
supporting, unclear — never as a percentage.  The interval behind it exists so
the arithmetic has something to multiply and so uncertainty can be widened; it
is not precision anyone should read.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from ..config import Phase2Config, parse_ts
from ..ids import comment_artifact, issue_artifact, participant_id, pr_artifact
from ..versions import derivation_version

log = logging.getLogger("impact2.episodes.participants")

VERSION = derivation_version("participants")

ROLES = (
    "originator", "core_implementer", "contributing_implementer", "decision_shaper",
    "risk_preventer", "integrator", "rollout_sustainer", "enabler", "documenter",
)

MECHANICAL_FLAGS = ("is_lockfile", "is_generated", "is_snapshot", "is_vendor",
                    "is_binary_asset")


def _production(row: Mapping[str, Any]) -> bool:
    if any(bool(row.get(flag)) for flag in MECHANICAL_FLAGS):
        return False
    return not (row.get("is_test") or row.get("is_docs"))


class AttributionEngine:
    def __init__(
        self,
        config: Phase2Config,
        *,
        actors: Mapping[str, Mapping[str, Any]],
        prs: Mapping[int, Mapping[str, Any]],
        files_by_pr: Mapping[int, Sequence[Mapping[str, Any]]],
        commits_by_pr: Mapping[int, Sequence[Mapping[str, Any]]],
        issues: Mapping[int, Mapping[str, Any]],
        interventions_by_episode: Mapping[str, Sequence[Mapping[str, Any]]],
        propagation_by_episode: Mapping[str, Mapping[str, Any]],
        propagation_edges_by_episode: Mapping[str, Sequence[Mapping[str, Any]]],
        threads_by_pr: Mapping[int, Sequence[Mapping[str, Any]]],
    ) -> None:
        self.config = config
        self.actors = actors
        self.prs = prs
        self.files_by_pr = files_by_pr
        self.commits_by_pr = commits_by_pr
        self.issues = issues
        self.interventions = interventions_by_episode
        self.propagation = propagation_by_episode
        self.propagation_edges = propagation_edges_by_episode
        self.threads_by_pr = threads_by_pr

        self.thresholds = config.get("attribution.thresholds")
        self.shares = config.get("attribution.share_categories")
        self.relevance = config.get("attribution.role_dimension_relevance")
        self.controls = config.get("attribution.controls")
        self.dimensions = list(config.get("outranking.criteria").keys())

    # -- identity --------------------------------------------------------
    def cluster_of(self, actor_id: str | None) -> str | None:
        """One human, one key. Phase 1 clusters logins with Git email identities."""
        if not actor_id:
            return None
        actor = self.actors.get(str(actor_id))
        if not actor:
            return str(actor_id)
        return str(actor.get("identity_cluster_id") or actor_id)

    def _actor_display(self, cluster: str) -> dict[str, Any]:
        members = [
            a for a in self.actors.values()
            if str(a.get("identity_cluster_id")) == cluster
        ]
        with_login = [a for a in members if a.get("login")]
        primary = (with_login or members or [{}])[0]
        return {
            "actor_cluster_id": cluster,
            "primary_actor_id": primary.get("actor_id", cluster),
            "login": primary.get("login"),
            "display_name": primary.get("display_name"),
            "is_bot": bool(primary.get("is_bot")),
            "bot_probability": primary.get("bot_probability"),
            "account_type": primary.get("account_type"),
            "identity_ambiguity": primary.get("ambiguity_status"),
            "identity_ambiguity_reasons": primary.get("ambiguity_reasons") or [],
            "cluster_size": primary.get("identity_cluster_size"),
        }

    # -- role inference ---------------------------------------------------
    def infer_roles(
        self, episode: Mapping[str, Any]
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Return cluster -> role -> evidence records."""
        eid = str(episode["episode_id"])
        numbers = [int(n) for n in episode.get("pr_numbers") or []]
        roles: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )

        def note(cluster: str | None, role: str, detail: str, artifact: str,
                 url: str | None = None) -> None:
            if not cluster:
                return
            roles[cluster][role].append(
                {"detail": detail[:300], "artifact_id": artifact, "url": url}
            )

        # -- originator: whoever framed the problem -----------------------
        for issue_number in episode.get("issue_numbers") or []:
            issue = self.issues.get(int(issue_number))
            if not issue:
                continue
            note(
                self.cluster_of(issue.get("author_actor_id")), "originator",
                f"opened issue #{issue_number}: {str(issue.get('title') or '')[:120]}",
                issue_artifact(int(issue_number)), issue.get("url"),
            )
        if not (episode.get("issue_numbers") or []):
            min_body = int(self.thresholds["originator_problem_framing_min_body_chars"])
            first = sorted(
                numbers,
                key=lambda n: (parse_ts((self.prs.get(n) or {}).get("created_at"))
                               or parse_ts("2100-01-01T00:00:00Z"), n),
            )
            for number in first[:1]:
                pr = self.prs.get(number) or {}
                if len(str(pr.get("body_text") or "")) >= min_body:
                    note(
                        self.cluster_of(pr.get("author_actor_id")), "originator",
                        f"opened the first PR of the arc (#{number}) with a "
                        f"{len(str(pr.get('body_text')))}-character problem "
                        "description and no linked issue",
                        pr_artifact(number), pr.get("url"),
                    )

        # -- implementers: by production-code contribution ----------------
        code_by_cluster: dict[str, int] = defaultdict(int)
        total_code = 0
        for number in numbers:
            pr = self.prs.get(number) or {}
            cluster = self.cluster_of(pr.get("author_actor_id"))
            files = [f for f in self.files_by_pr.get(number, []) if _production(f)]
            total_code += len(files)
            if cluster:
                code_by_cluster[cluster] += len(files)
            # Co-authors are real contributors; Phase 1 keeps the trailers.
            for commit in self.commits_by_pr.get(number, []):
                for co_id in commit.get("co_author_actor_ids") or []:
                    co_cluster = self.cluster_of(co_id)
                    actor = self.actors.get(str(co_id)) or {}
                    if co_cluster and co_cluster != cluster and not actor.get(
                        "is_ai_assistant_identity"
                    ):
                        note(
                            co_cluster, "contributing_implementer",
                            f"Git co-author on the merge commit of PR #{number}",
                            pr_artifact(number), pr.get("url"),
                        )

        largest_pr = max(
            numbers,
            key=lambda n: (len([f for f in self.files_by_pr.get(n, []) if _production(f)]), -n),
            default=None,
        )
        core_share = float(self.thresholds["core_implementer_min_code_share"])
        for number in numbers:
            pr = self.prs.get(number) or {}
            cluster = self.cluster_of(pr.get("author_actor_id"))
            if not cluster:
                continue
            share = (code_by_cluster.get(cluster, 0) / total_code) if total_code else 0.0
            is_core = share >= core_share or number == largest_pr
            role = "core_implementer" if is_core else "contributing_implementer"
            note(
                cluster, role,
                f"authored PR #{number}"
                + (f" ({share:.0%} of the episode's production-code files)"
                   if total_code else " (no production-code diff available)"),
                pr_artifact(number), pr.get("url"),
            )

        # -- reviewers: only with causal evidence -------------------------
        author_clusters = {
            self.cluster_of((self.prs.get(n) or {}).get("author_actor_id"))
            for n in numbers
        }
        helped: dict[str, set[int]] = defaultdict(set)
        components_helped: dict[str, set[str]] = defaultdict(set)
        for intervention in self.interventions.get(eid, []):
            cluster = self.cluster_of(intervention.get("commenter_actor_id"))
            if not cluster or cluster in author_clusters:
                continue
            if not intervention.get("is_consequential"):
                continue
            helped[cluster].add(int(intervention["pr_number"]))
            if intervention.get("component"):
                components_helped[cluster].add(str(intervention["component"]))
            concerns = set(intervention.get("consequential_classes") or [])
            artifact = comment_artifact(str(intervention.get("candidate_id", "")).split(":")[-1])
            if concerns & {"design_architecture", "alternative_approach", "scope"}:
                note(
                    cluster, "decision_shaper",
                    f"review comment on PR #{intervention['pr_number']} raised a "
                    f"{'/'.join(sorted(concerns & {'design_architecture', 'alternative_approach', 'scope'}))} "
                    f"concern; causal confidence {intervention.get('causal_confidence')}, "
                    f"consequence {intervention.get('consequence_band')}",
                    intervention.get("artifact_id") or artifact, intervention.get("url"),
                )
            if concerns & {"security", "privacy", "data_integrity", "migration_safety",
                           "correctness"}:
                note(
                    cluster, "risk_preventer",
                    f"review comment on PR #{intervention['pr_number']} raised a "
                    f"{'/'.join(sorted(concerns & {'security', 'privacy', 'data_integrity', 'migration_safety', 'correctness'}))} "
                    f"concern; consequence {intervention.get('consequence_band')}",
                    intervention.get("artifact_id") or artifact, intervention.get("url"),
                )
            if len(str(intervention.get("body_excerpt") or "")) >= int(
                self.thresholds["documenter_min_comment_chars"]
            ) and intervention.get("thread_is_resolved"):
                note(
                    cluster, "documenter",
                    f"wrote a long explanatory review comment on PR "
                    f"#{intervention['pr_number']} that was resolved",
                    intervention.get("artifact_id") or artifact, intervention.get("url"),
                )

        # -- integrator: connective tissue across the arc -----------------
        min_prs = int(self.thresholds["integrator_min_prs"])
        min_components = int(self.thresholds["integrator_min_components"])
        for cluster, prs_helped in helped.items():
            if len(prs_helped) >= min_prs and len(components_helped[cluster]) >= min_components:
                note(
                    cluster, "integrator",
                    f"engaged with {len(prs_helped)} PRs of this episode across "
                    f"{len(components_helped[cluster])} components without being "
                    "their implementer",
                    pr_artifact(sorted(prs_helped)[0]),
                    (self.prs.get(sorted(prs_helped)[0]) or {}).get("url"),
                )
        for number in numbers:
            for thread in self.threads_by_pr.get(number, []):
                resolver = thread.get("resolved_by_actor_id")
                pr = self.prs.get(number) or {}
                cluster = self.cluster_of(resolver)
                if cluster and cluster != self.cluster_of(pr.get("author_actor_id")):
                    note(
                        cluster, "integrator",
                        f"resolved a review thread on PR #{number} authored by "
                        "someone else",
                        pr_artifact(number), pr.get("url"),
                    )

        # -- rollout / sustainer ------------------------------------------
        ordered = sorted(
            numbers,
            key=lambda n: (parse_ts((self.prs.get(n) or {}).get("merged_at"))
                           or parse_ts("2100-01-01T00:00:00Z"), n),
        )
        for number in ordered[1:]:
            pr = self.prs.get(number) or {}
            prefix = str(pr.get("title_prefix") or "")
            touches_flag = any(
                f.get("path", "").endswith("constants.tsx") for f in self.files_by_pr.get(number, [])
            )
            if prefix in {"fix", "revert", "perf", "chore"} or touches_flag:
                note(
                    self.cluster_of(pr.get("author_actor_id")), "rollout_sustainer",
                    f"authored follow-up PR #{number} ({prefix or 'no prefix'})"
                    + (" touching the feature-flag registry" if touches_flag else ""),
                    pr_artifact(number), pr.get("url"),
                )

        # -- enabler: the propagation source ------------------------------
        summary = self.propagation.get(eid) or {}
        if int(summary.get("distinct_component_penetration") or 0) >= 1:
            source_paths = {
                str(e.get("source_path"))
                for e in self.propagation_edges.get(eid, [])
            }
            for number in numbers:
                pr = self.prs.get(number) or {}
                introduced = {
                    str(f.get("path")) for f in self.files_by_pr.get(number, [])
                    if f.get("change_status") == "A" and _production(f)
                }
                shared = introduced & source_paths
                if shared:
                    note(
                        self.cluster_of(pr.get("author_actor_id")), "enabler",
                        f"introduced {len(shared)} module(s) in PR #{number} that "
                        f"{summary.get('reach_pr_count', 0)} later change(s) depend on, "
                        f"across {summary.get('distinct_component_penetration')} component(s)",
                        pr_artifact(number), pr.get("url"),
                    )

        # -- documenter: docs in the diff ---------------------------------
        for number in numbers:
            pr = self.prs.get(number) or {}
            docs = [f for f in self.files_by_pr.get(number, []) if f.get("is_docs")]
            if docs:
                note(
                    self.cluster_of(pr.get("author_actor_id")), "documenter",
                    f"changed {len(docs)} documentation file(s) in PR #{number}, "
                    f"e.g. {docs[0].get('path')}",
                    pr_artifact(number), pr.get("url"),
                )

        return {k: dict(v) for k, v in roles.items()}

    # -- shared credit ----------------------------------------------------
    def _share_category(
        self, cluster: str, roles: Mapping[str, list], episode: Mapping[str, Any],
        all_roles: Mapping[str, Mapping[str, list]],
    ) -> tuple[str, list[str]]:
        reasons: list[str] = []
        implementers = [
            c for c, r in all_roles.items() if "core_implementer" in r
        ]
        is_core = "core_implementer" in roles
        is_contributing = "contributing_implementer" in roles
        reviewer_only = not (is_core or is_contributing) and bool(
            {"decision_shaper", "risk_preventer", "integrator", "documenter"} & set(roles)
        )

        if is_core and len(implementers) == 1:
            reasons.append("sole core implementer of the episode")
            return "primary", reasons
        if is_core:
            reasons.append(
                f"one of {len(implementers)} core implementers"
            )
            return "material", reasons
        if is_contributing:
            reasons.append("contributed implementation below the core threshold")
            return "supporting", reasons
        if reviewer_only:
            high = any(
                i.get("causal_confidence") == "high"
                for i in self.interventions.get(str(episode["episode_id"]), [])
                if self.cluster_of(i.get("commenter_actor_id")) == cluster
            )
            if high:
                reasons.append(
                    "review intervention with high causal confidence, but no "
                    "implementation contribution"
                )
                return "material", reasons
            reasons.append(
                "review or documentation contribution without confirmed causal "
                "evidence"
            )
            return "supporting", reasons
        if "originator" in roles:
            reasons.append("framed the problem but did not implement it")
            return "material", reasons
        reasons.append("participation recorded but its weight could not be established")
        return "unclear", reasons

    # -- factors ----------------------------------------------------------
    def _factors(
        self, roles: Mapping[str, list], share_category: str
    ) -> dict[str, float]:
        share_factor = float(self.shares[share_category]["factor"])
        combine = str(self.controls.get("combine_roles", "max"))
        factors: dict[str, float] = {}
        for dimension in self.dimensions:
            values = [
                float((self.relevance.get(role) or {}).get(dimension, 0.0)) * share_factor
                for role in roles
            ]
            if not values:
                factors[dimension] = 0.0
            elif combine == "max":
                # max, not sum: holding four roles does not manufacture credit.
                factors[dimension] = round(max(values), 6)
            else:
                factors[dimension] = round(min(1.0, sum(values)), 6)
        return factors

    # -- assembly ---------------------------------------------------------
    def build(self, episodes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        keep_roleless = bool(self.thresholds.get("keep_roleless_participants", True))
        min_factor = float(self.controls["min_recorded_factor"])
        max_total = float(self.controls["max_total_factor_per_dimension"])
        rows: list[dict[str, Any]] = []

        for episode in episodes:
            eid = str(episode["episode_id"])
            all_roles = self.infer_roles(episode)

            # Participants with no role at all — recorded for transparency.
            touched: set[str] = set()
            for number in episode.get("pr_numbers") or []:
                pr = self.prs.get(int(number)) or {}
                for login in pr.get("participant_logins") or []:
                    cluster = self.cluster_of(f"github/user/{str(login).lower()}")
                    if cluster:
                        touched.add(cluster)
            for cluster in touched:
                if cluster not in all_roles and keep_roleless:
                    all_roles.setdefault(cluster, {})

            episode_rows: list[dict[str, Any]] = []
            for cluster, roles in sorted(all_roles.items()):
                identity = self._actor_display(cluster)
                if identity["is_bot"]:
                    continue     # bots are excluded from attribution, disclosed elsewhere
                share_category, share_reasons = (
                    self._share_category(cluster, roles, episode, all_roles)
                    if roles else ("unclear", ["participated with no evidenced role"])
                )
                factors = self._factors(roles, share_category) if roles else {
                    d: 0.0 for d in self.dimensions
                }
                interval = self.shares[share_category]["interval"]
                direct_evidence = [
                    {"role": role, **item}
                    for role, items in sorted(roles.items())
                    for item in items[:4]
                ]
                confidence = _attribution_confidence(roles, share_category, identity)
                episode_rows.append(
                    {
                        "participant_id": participant_id(eid, cluster),
                        "episode_id": eid,
                        **identity,
                        "roles": sorted(roles),
                        "role_count": len(roles),
                        "role_evidence": {r: v[:4] for r, v in sorted(roles.items())},
                        "direct_evidence": direct_evidence,
                        "direct_evidence_count": sum(len(v) for v in roles.values()),
                        "share_category": share_category,
                        "share_reasons": share_reasons,
                        "share_interval_low": float(interval[0]),
                        "share_interval_high": float(interval[1]),
                        "attribution_confidence": confidence,
                        "attribution_factors": factors,
                        "has_any_evidence": bool(roles),
                        "participants_version": VERSION,
                    }
                )

            # Cap the episode's total attributed factor per dimension.
            for dimension in self.dimensions:
                total = sum(float(r["attribution_factors"].get(dimension, 0.0))
                            for r in episode_rows)
                if total > max_total and total > 0:
                    scale = max_total / total
                    for row in episode_rows:
                        row["attribution_factors"][dimension] = round(
                            row["attribution_factors"][dimension] * scale, 6
                        )
                        row.setdefault("factor_scaled_dimensions", []).append(dimension)

            for row in episode_rows:
                row["attribution_factors"] = {
                    k: v for k, v in row["attribution_factors"].items()
                }
                row["max_attribution_factor"] = round(
                    max(row["attribution_factors"].values(), default=0.0), 6
                )
                row["contributes_to_portfolio"] = (
                    row["max_attribution_factor"] >= min_factor
                )
                row.setdefault("factor_scaled_dimensions", [])
            rows.extend(episode_rows)

        log.info(
            "attribution: %d participant rows across %d episodes (%d contribute)",
            len(rows), len(episodes),
            sum(1 for r in rows if r["contributes_to_portfolio"]),
        )
        return rows


def _attribution_confidence(
    roles: Mapping[str, list], share_category: str, identity: Mapping[str, Any]
) -> str:
    """How sure are we that this person did this thing?"""
    if not roles:
        return "low"
    evidence_count = sum(len(v) for v in roles.values())
    if identity.get("identity_ambiguity") == "ambiguous":
        return "low"
    if share_category == "unclear":
        return "low"
    if evidence_count >= 3 and share_category in {"primary", "material"}:
        return "high"
    if evidence_count >= 2:
        return "medium"
    return "medium" if share_category != "supporting" else "low"


def summarise(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    by_role: dict[str, int] = defaultdict(int)
    by_share: dict[str, int] = defaultdict(int)
    by_confidence: dict[str, int] = defaultdict(int)
    for row in items:
        for role in row.get("roles") or []:
            by_role[str(role)] += 1
        by_share[str(row.get("share_category"))] += 1
        by_confidence[str(row.get("attribution_confidence"))] += 1
    return {
        "participant_rows": len(items),
        "distinct_people": len({str(r.get("actor_cluster_id")) for r in items}),
        "by_role": dict(sorted(by_role.items())),
        "by_share_category": dict(sorted(by_share.items())),
        "by_attribution_confidence": dict(sorted(by_confidence.items())),
        "roleless_participants": sum(1 for r in items if not r.get("has_any_evidence")),
        "contributing_to_portfolio": sum(
            1 for r in items if r.get("contributes_to_portfolio")
        ),
        "scaled_by_episode_cap": sum(
            1 for r in items if r.get("factor_scaled_dimensions")
        ),
        "participants_version": VERSION,
    }
