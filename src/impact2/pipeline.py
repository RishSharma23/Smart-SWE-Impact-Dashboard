"""The Phase 2 pipeline as one re-runnable object.

Each CLI stage is a method here, and the whole chain can also be run in memory
from a set of Phase 1 tables.  That second property is what makes the
adversarial validation honest: to prove that splitting one episode into ten
PRs, or adding a 40,000-line generated migration, does not move the ranking,
you have to actually mutate the inputs and re-run — not reason about it.

State between stages is held in memory and mirrored to ``data/phase2`` so a
stage can be re-run on its own, exactly like Phase 1's stages.
"""

from __future__ import annotations

import copy
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .analytics import corrective as corrective_mod
from .analytics import novelty as novelty_mod
from .analytics import propagation as propagation_mod
from .analytics import review_causal
from .config import Phase2Config, iso, now, parse_ts
from .dimensions import rubric as rubric_mod
from .episodes import build as episodes_mod
from .episodes import participants as participants_mod
from .graph import artifact_graph, clustering, semantic
from .ids import episode_id as make_episode_id
from .inputs import Phase1Inputs, group_by, index_by
from .portfolio import build as portfolio_mod
from .rank import outranking, scenarios as scenarios_mod
from .store import read_json, write_json
from .versions import all_versions

log = logging.getLogger("impact2.pipeline")


@dataclass
class Phase2Pipeline:
    """Holds every Phase 2 artifact for one run."""

    config: Phase2Config
    inputs: Phase1Inputs

    # -- indexes built once -----------------------------------------------
    prs: dict[int, dict[str, Any]] = field(default_factory=dict)
    files_by_pr: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    commits_by_pr: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    flags_by_pr: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    threads_by_pr: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    interventions_by_pr: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    issues: dict[int, dict[str, Any]] = field(default_factory=dict)
    actors: dict[str, dict[str, Any]] = field(default_factory=dict)
    change_shape: dict[int, dict[str, Any]] = field(default_factory=dict)
    blast: dict[int, dict[str, Any]] = field(default_factory=dict)
    regression: dict[int, dict[str, Any]] = field(default_factory=dict)
    module_nodes: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- stage outputs -----------------------------------------------------
    edges: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    pair_edges: dict[tuple[int, int], list[dict[str, Any]]] = field(default_factory=dict)
    pair_weights: dict[tuple[int, int], float] = field(default_factory=dict)
    clusters: list[list[int]] = field(default_factory=list)
    cluster_audit: list[dict[str, Any]] = field(default_factory=list)
    episodes: list[dict[str, Any]] = field(default_factory=list)
    episode_artifacts: list[dict[str, Any]] = field(default_factory=list)
    episode_review_queue: list[dict[str, Any]] = field(default_factory=list)
    propagation_edges: list[dict[str, Any]] = field(default_factory=list)
    propagation_summary: list[dict[str, Any]] = field(default_factory=list)
    novelty: list[dict[str, Any]] = field(default_factory=list)
    corrective: list[dict[str, Any]] = field(default_factory=list)
    interventions: list[dict[str, Any]] = field(default_factory=list)
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    participants: list[dict[str, Any]] = field(default_factory=list)
    portfolios: list[dict[str, Any]] = field(default_factory=list)
    ranking_runs: list[dict[str, Any]] = field(default_factory=list)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    summaries: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        self.build_indexes()

    def build_indexes(self) -> None:
        t = self.inputs.tables
        self.prs = {int(p["pr_number"]): dict(p) for p in t.get("pull_requests") or []}
        self.actors = index_by(t.get("actors") or [], "actor_id")
        self.issues = {
            int(i["issue_number"]): dict(i) for i in t.get("issues") or []
        }
        self.files_by_pr = defaultdict(list)
        for row in t.get("pr_files") or []:
            self.files_by_pr[int(row["pr_number"])].append(dict(row))
        self.commits_by_pr = defaultdict(list)
        for row in t.get("commits") or []:
            if row.get("pr_number") is not None:
                self.commits_by_pr[int(row["pr_number"])].append(dict(row))
        self.flags_by_pr = defaultdict(list)
        for row in t.get("feature_flags") or []:
            if row.get("pr_number") is not None:
                self.flags_by_pr[int(row["pr_number"])].append(dict(row))
        self.threads_by_pr = defaultdict(list)
        for row in t.get("review_threads") or []:
            if row.get("pr_number") is not None:
                self.threads_by_pr[int(row["pr_number"])].append(dict(row))
        self.interventions_by_pr = defaultdict(list)
        for row in t.get("review_intervention_candidates") or []:
            if row.get("pr_number") is not None:
                self.interventions_by_pr[int(row["pr_number"])].append(dict(row))
        self.change_shape = {
            int(r["pr_number"]): dict(r) for r in t.get("pr_change_shape") or []
        }
        self.blast = {
            int(r["pr_number"]): dict(r) for r in t.get("pr_blast_radius") or []
        }
        self.regression = {
            int(r["pr_number"]): dict(r) for r in t.get("pr_regression_candidates") or []
        }
        self.module_nodes = {
            str(r["path"]): dict(r) for r in t.get("module_nodes") or []
        }

    # -- eligibility ------------------------------------------------------
    def analysable_pr_numbers(self) -> list[int]:
        """PRs that may anchor an episode.

        Contract rule 3.2: filter on ``ranking_eligible``. Merge-queue
        artifacts are excluded there, which on this repository is roughly 40% of
        "PRs created" and would otherwise double everyone's apparent output.
        Context-only PRs stay in the graph as edge targets but never anchor an
        episode of their own.
        """
        return sorted(
            number for number, pr in self.prs.items() if pr.get("ranking_eligible")
        )

    # ==================================================================
    # stage: graph
    # ==================================================================
    def stage_graph(self) -> dict[str, Any]:
        builder = artifact_graph.ArtifactGraphBuilder(self.config, self.inputs.tables)
        builder.build_tier_a()
        builder.build_tier_b()
        deterministic = artifact_graph.deduplicate(builder.edges)
        pairs = artifact_graph.pr_pair_edges(deterministic)

        components_by_pr = {
            number: {str(f.get("component")) for f in rows if f.get("component")}
            for number, rows in self.files_by_pr.items()
        }
        eligible = set(self.analysable_pr_numbers())
        semantic_input = {
            n: p for n, p in self.prs.items() if n in eligible
        }
        semantic_edges = semantic.build_semantic_edges(
            self.config, semantic_input,
            components_by_pr=components_by_pr,
            corroborated_pairs=set(pairs),
        )
        builder.edges.extend(artifact_graph.Edge(dict(e)) for e in semantic_edges)

        self.edges = artifact_graph.deduplicate(builder.edges)
        self.nodes = builder.nodes()
        self.pair_edges = artifact_graph.pr_pair_edges(self.edges)
        self.summaries["graph"] = {
            **artifact_graph.summarise(self.edges),
            **semantic.summarise(self.edges),
            "builder_notes": builder.notes,
            "nodes": len(self.nodes),
        }
        return self.summaries["graph"]

    # ==================================================================
    # stage: episodes
    # ==================================================================
    def stage_episodes(self) -> dict[str, Any]:
        merged_at = {n: parse_ts(p.get("merged_at")) for n, p in self.prs.items()}
        self.pair_weights, dropped = clustering.build_pair_weights(
            self.pair_edges, self.config, merged_at=merged_at
        )
        eligible = self.analysable_pr_numbers()
        # Only pairs where BOTH ends are analysable can form an episode.
        usable = {
            pair: weight for pair, weight in self.pair_weights.items()
            if pair[0] in set(eligible) and pair[1] in set(eligible)
        }
        assignment = clustering.louvain(
            usable, eligible,
            resolution=float(self.config.get("episodes.clustering.resolution")),
            max_passes=int(self.config.get("episodes.clustering.max_passes")),
        )
        proposed: dict[int, list[int]] = defaultdict(list)
        for number, community in assignment.items():
            proposed[community].append(number)

        self.clusters, self.cluster_audit = clustering.apply_constraints(
            proposed, self.pair_edges, usable, self.config
        )
        self.cluster_audit.extend(
            {"action": "drop_pair", **entry,
             "episode_construction_version": clustering.VERSION}
            for entry in dropped[:500]
        )
        self._build_episode_records()
        self.summaries["episodes"] = {
            **clustering.summarise(self.clusters, self.cluster_audit),
            **episodes_mod.summarise(self.episodes),
            "dropped_pairs": len(dropped),
            "review_queue": len(self.episode_review_queue),
        }
        return self.summaries["episodes"]

    def _build_episode_records(self) -> None:
        """Propagation must be known before status; both are computed here."""
        cluster_ids = {
            make_episode_id(members, self.inputs.qualifier): sorted(members)
            for members in self.clusters
        }
        analyzer = propagation_mod.PropagationAnalyzer(
            self.config,
            dependency_edges=self.inputs.table("dependency_edges"),
            module_nodes=self.module_nodes,
            files_by_pr=self.files_by_pr,
            prs=self.prs,
            window_end=self.inputs.window_end,
        )
        self.propagation_edges = []
        self.propagation_summary = []
        downstream_counts: dict[str, int] = {}
        for eid, members in cluster_ids.items():
            ends = [parse_ts(self.prs.get(n, {}).get("merged_at")) for n in members]
            end = max([e for e in ends if e], default=self.inputs.window_end)
            edges, summary = analyzer.propagate(eid, members, episode_end=end)
            self.propagation_edges.extend(edges)
            self.propagation_summary.append(summary)
            downstream_counts[eid] = int(summary.get("reach_pr_count") or 0)

        self.episodes, self.episode_artifacts, self.episode_review_queue = (
            episodes_mod.build_episodes(
                config=self.config,
                clusters=self.clusters,
                pair_edges=self.pair_edges,
                edges=self.edges,
                prs=self.prs,
                files_by_pr=self.files_by_pr,
                issues=self.issues,
                flags_by_pr=self.flags_by_pr,
                regression_by_pr=self.regression,
                change_shape_by_pr=self.change_shape,
                blast_by_pr=self.blast,
                commits_by_pr=self.commits_by_pr,
                interventions_by_pr=self.interventions_by_pr,
                downstream_counts=downstream_counts,
                window_end=self.inputs.window_end,
                qualifier=self.inputs.qualifier,
                repo_url=self.inputs.repository_url,
            )
        )

    # ==================================================================
    # stage: analytics
    # ==================================================================
    def stage_analytics(self) -> dict[str, Any]:
        episode_prs = {
            str(e["episode_id"]): [int(n) for n in e["pr_numbers"]]
            for e in self.episodes
        }

        novelty_analyzer = novelty_mod.NoveltyAnalyzer(
            self.config, prs=self.prs, files_by_pr=self.files_by_pr,
            module_nodes=self.module_nodes,
            is_shallow_clone=self.inputs.is_shallow,
        )
        self.novelty = [
            novelty_analyzer.analyse(eid, numbers)
            for eid, numbers in sorted(episode_prs.items())
        ]

        corrective_analyzer = corrective_mod.CorrectiveAnalyzer(
            self.config, prs=self.prs, regression_by_pr=self.regression,
            change_shape_by_pr=self.change_shape,
        )
        self.corrective = [
            corrective_analyzer.analyse(eid, numbers)
            for eid, numbers in sorted(episode_prs.items())
        ]

        causal = review_causal.ReviewCausalityAnalyzer(
            self.config, prs=self.prs,
            threads={
                str(t["thread_id"]): t
                for t in self.inputs.table("review_threads")
                if t.get("thread_id")
            },
            review_comments=self.inputs.table("review_comments"),
            files_by_pr=self.files_by_pr,
            actors=self.actors,
        )
        self.interventions = causal.analyse_all(
            self.inputs.table("review_intervention_candidates")
        )

        self.summaries["analytics"] = {
            "propagation": propagation_mod.summarise(self.propagation_summary),
            "novelty": novelty_mod.summarise(self.novelty),
            "corrective": corrective_mod.summarise(self.corrective),
            "review_causality": review_causal.summarise(self.interventions),
        }
        return self.summaries["analytics"]

    # ==================================================================
    # stage: attribute
    # ==================================================================
    def stage_attribute(self) -> dict[str, Any]:
        episode_prs = {
            str(e["episode_id"]): [int(n) for n in e["pr_numbers"]]
            for e in self.episodes
        }
        interventions_by_episode = review_causal.by_episode(
            self.interventions, episode_prs
        )
        propagation_by_episode = {
            str(r["episode_id"]): r for r in self.propagation_summary
        }
        propagation_edges_by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in self.propagation_edges:
            propagation_edges_by_episode[str(edge["episode_id"])].append(edge)

        engine = participants_mod.AttributionEngine(
            self.config,
            actors=self.actors, prs=self.prs, files_by_pr=self.files_by_pr,
            commits_by_pr=self.commits_by_pr, issues=self.issues,
            interventions_by_episode=interventions_by_episode,
            propagation_by_episode=propagation_by_episode,
            propagation_edges_by_episode=propagation_edges_by_episode,
            threads_by_pr=self.threads_by_pr,
        )
        self.participants = engine.build(self.episodes)
        self.summaries["attribution"] = participants_mod.summarise(self.participants)
        return self.summaries["attribution"]

    # ==================================================================
    # stage: dimensions
    # ==================================================================
    def stage_dimensions(self) -> dict[str, Any]:
        episode_prs = {
            str(e["episode_id"]): [int(n) for n in e["pr_numbers"]]
            for e in self.episodes
        }
        evaluator = rubric_mod.RubricEvaluator(
            self.config,
            prs=self.prs, files_by_pr=self.files_by_pr, issues=self.issues,
            change_shape=self.change_shape, blast=self.blast,
            regression=self.regression, threads_by_pr=self.threads_by_pr,
            propagation={str(r["episode_id"]): r for r in self.propagation_summary},
            novelty={str(r["episode_id"]): r for r in self.novelty},
            corrective={str(r["episode_id"]): r for r in self.corrective},
            interventions=review_causal.by_episode(self.interventions, episode_prs),
            window_end=self.inputs.window_end,
        )
        self.dimensions = evaluator.evaluate_all(self.episodes)
        self.summaries["dimensions"] = rubric_mod.summarise(self.dimensions)
        return self.summaries["dimensions"]

    # ==================================================================
    # stage: portfolios
    # ==================================================================
    def _portfolio_builder(self, config: Phase2Config | None = None) -> portfolio_mod.PortfolioBuilder:
        return portfolio_mod.PortfolioBuilder(
            config or self.config,
            episodes={str(e["episode_id"]): e for e in self.episodes},
            dimensions=self.dimensions,
            participants=self.participants,
            propagation={str(r["episode_id"]): r for r in self.propagation_summary},
            actors=self.actors,
            window_start=self.inputs.window_start,
            window_end=self.inputs.window_end,
        )

    def stage_portfolios(self, **kwargs: Any) -> dict[str, Any]:
        self.portfolios = self._portfolio_builder().build_all(**kwargs)
        self.summaries["portfolios"] = portfolio_mod.summarise(self.portfolios)
        return self.summaries["portfolios"]

    # ==================================================================
    # stage: rank
    # ==================================================================
    def counterevidence_index(self) -> dict[str, dict[str, Any]]:
        """Severe, high-confidence counterevidence, per engineer.

        Only confirmed, un-reapplied reverts of episodes whose band reached the
        configured threshold count. Proximate regression candidates never do —
        Phase 1 marks them ``requires_human_confirmation`` and the veto is far
        too blunt an instrument for an unconfirmed signal.
        """
        rules = self.config.get("outranking.veto.counterevidence_veto")
        min_band = int(rules["min_reverted_band"])
        corrective_by_episode = {str(r["episode_id"]): r for r in self.corrective}
        bands_by_episode: dict[str, int] = defaultdict(int)
        for row in self.dimensions:
            if row.get("band") is not None:
                bands_by_episode[str(row["episode_id"])] = max(
                    bands_by_episode[str(row["episode_id"])], int(row["band"])
                )

        out: dict[str, dict[str, Any]] = defaultdict(lambda: {"severe_events": []})
        for participant in self.participants:
            if participant.get("share_category") not in {"primary", "material"}:
                continue
            eid = str(participant["episode_id"])
            record = corrective_by_episode.get(eid) or {}
            if not record.get("confirmed_revert"):
                continue
            if bands_by_episode.get(eid, 0) < min_band:
                continue
            out[str(participant["actor_cluster_id"])]["severe_events"].append(
                {
                    "episode_id": eid,
                    "detail": (
                        f"episode '{eid}' reached band {bands_by_episode.get(eid)} and "
                        "was explicitly reverted without being reapplied"
                    ),
                    "confidence": "high",
                }
            )
        return dict(out)

    def stage_rank(self) -> dict[str, Any]:
        self.scenarios = scenarios_mod.resolve(
            self.config,
            window_days=self.inputs.window_days,
            is_shallow_clone=self.inputs.is_shallow,
        )
        counterevidence = self.counterevidence_index()
        builder = self._portfolio_builder()
        runs: list[dict[str, Any]] = []

        for scenario in self.scenarios:
            if not scenario["available"]:
                runs.append(
                    {
                        "ranking_run_id": f"ranking/{scenario['scenario']}/unavailable",
                        "scenario": scenario["scenario"],
                        "available": False,
                        "unavailable_reason": scenario["unavailable_reason"],
                        "remedy": scenario["remedy"],
                        "ranking": [],
                        "comparisons": [],
                    }
                )
                continue
            kwargs = scenarios_mod.portfolio_kwargs(scenario)
            portfolios = builder.build_all(**kwargs)
            run = outranking.run_scenario(
                self.config, portfolios,
                scenario=scenario["scenario"],
                weights=self.config.criterion_weights(scenario["scenario"]),
                counterevidence=counterevidence,
            )
            run["available"] = True
            run["portfolio_kwargs"] = kwargs
            runs.append(run)
            if scenario["scenario"] == "balanced":
                self.portfolios = portfolios

        self.ranking_runs = runs
        self.summaries["ranking"] = outranking.summarise(
            [r for r in runs if r.get("available")]
        )
        return self.summaries["ranking"]

    # ==================================================================
    # re-ranking hooks used by validation
    # ==================================================================
    def rank_once(
        self,
        *,
        weights: Mapping[str, float] | None = None,
        thresholds: Mapping[str, Mapping[str, float]] | None = None,
        config: Phase2Config | None = None,
        decay_mode: str = "decayed",
        participants: Sequence[Mapping[str, Any]] | None = None,
        dimensions: Sequence[Mapping[str, Any]] | None = None,
        episodes: Sequence[Mapping[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """One ranking, from possibly-mutated inputs. Used by every stability test."""
        cfg = config or self.config
        builder = portfolio_mod.PortfolioBuilder(
            cfg,
            episodes={
                str(e["episode_id"]): e for e in (episodes or self.episodes)
            },
            dimensions=list(dimensions or self.dimensions),
            participants=list(participants or self.participants),
            propagation={str(r["episode_id"]): r for r in self.propagation_summary},
            actors=self.actors,
            window_start=self.inputs.window_start,
            window_end=self.inputs.window_end,
        )
        portfolios = builder.build_all(decay_mode=decay_mode)
        # Stability analysis re-runs this model hundreds of times and the model
        # is O(n^2) in engineers. Restricting each trial to the engineers who
        # are plausibly competing for a top-five slot answers the question that
        # is actually being asked — "is THIS top five stable?" — at a fraction
        # of the cost. The pool comes from the base ranking and its size is
        # reported alongside the results.
        pool = self.candidate_pool()
        if pool:
            portfolios = [
                p for p in portfolios if str(p["actor_cluster_id"]) in pool
            ]
        run = outranking.run_scenario(
            cfg, portfolios, scenario="stability_probe",
            weights=weights or cfg.criterion_weights("balanced"),
            thresholds=thresholds,
            counterevidence=self.counterevidence_index(),
        )
        return run["ranking"]

    def candidate_pool(self) -> set[str]:
        """Top-N of the base balanced ranking, used to bound stability trials."""
        size = int(self.config.get("outranking.sensitivity.candidate_pool_size", 0))
        if size <= 0:
            return set()
        balanced = next(
            (r for r in self.ranking_runs
             if r.get("scenario") == "balanced" and r.get("available")),
            None,
        )
        if not balanced:
            return set()
        return {
            str(r["actor_cluster_id"]) for r in (balanced.get("ranking") or [])[:size]
        }

    def episodes_by_actor(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for participant in self.participants:
            if participant.get("contributes_to_portfolio"):
                out[str(participant["actor_cluster_id"])].append(
                    str(participant["episode_id"])
                )
        return dict(out)

    def participants_for_sample(
        self, sample: Mapping[str, Sequence[str]]
    ) -> list[dict[str, Any]]:
        """Rebuild the participant list from a bootstrap resample of episodes."""
        by_key = {
            (str(p["actor_cluster_id"]), str(p["episode_id"])): p
            for p in self.participants
        }
        out: list[dict[str, Any]] = []
        for actor, episode_ids in sample.items():
            seen: dict[str, int] = defaultdict(int)
            for eid in episode_ids:
                row = by_key.get((actor, eid))
                if row is None:
                    continue
                seen[eid] += 1
                # A resampled duplicate is a distinct entry; the OWA coefficients
                # damp it exactly as they damp any additional episode.
                clone = dict(row)
                if seen[eid] > 1:
                    clone["episode_id"] = f"{eid}#resample{seen[eid]}"
                out.append(clone)
        return out

    def dimensions_for_sample(
        self, sample: Mapping[str, Sequence[str]]
    ) -> list[dict[str, Any]]:
        by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self.dimensions:
            by_episode[str(row["episode_id"])].append(row)
        out: list[dict[str, Any]] = list(self.dimensions)
        added: set[str] = set()
        for episode_ids in sample.values():
            counts: dict[str, int] = defaultdict(int)
            for eid in episode_ids:
                counts[eid] += 1
                if counts[eid] > 1:
                    alias = f"{eid}#resample{counts[eid]}"
                    if alias in added:
                        continue
                    added.add(alias)
                    for row in by_episode.get(eid, []):
                        clone = dict(row)
                        clone["episode_id"] = alias
                        out.append(clone)
        return out

    def episodes_for_sample(
        self, sample: Mapping[str, Sequence[str]]
    ) -> list[dict[str, Any]]:
        by_id = {str(e["episode_id"]): e for e in self.episodes}
        out = list(self.episodes)
        added: set[str] = set()
        for episode_ids in sample.values():
            counts: dict[str, int] = defaultdict(int)
            for eid in episode_ids:
                counts[eid] += 1
                if counts[eid] > 1:
                    alias = f"{eid}#resample{counts[eid]}"
                    if alias in added or eid not in by_id:
                        continue
                    added.add(alias)
                    clone = dict(by_id[eid])
                    clone["episode_id"] = alias
                    out.append(clone)
        return out

    def clone(self) -> "Phase2Pipeline":
        """A deep copy for adversarial mutation, sharing nothing mutable."""
        other = Phase2Pipeline.__new__(Phase2Pipeline)
        for key, value in self.__dict__.items():
            if key in {"config", "inputs"}:
                setattr(other, key, value)
            else:
                setattr(other, key, copy.deepcopy(value))
        return other

    # ==================================================================
    def manifest_block(self) -> dict[str, Any]:
        return {
            "generated_at": iso(now()),
            "versions": all_versions(),
            "configuration": self.config.as_dict(),
            "phase1": self.inputs.provenance(),
            "summaries": self.summaries,
        }
