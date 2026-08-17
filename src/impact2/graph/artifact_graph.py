"""The artifact graph: nodes, and edges graded by how much they actually prove.

Nodes are PRs, issues, reviews, review threads, comments, commits, feature
flags, files/modules, components, docs and changelog items, and actors.  Edges
carry an evidence *tier*, because the difference between "GitHub itself created
this link" and "these two PRs use similar words" is the whole ballgame:

    Tier A  deterministic.  Something in the data literally states the link:
            a closing reference, a GitHub cross-reference timeline event, a
            revert, commit/PR membership, a shared feature-flag key, a direct
            URL or #number mention, a changelog link, or a review thread whose
            file changed after the comment.

    Tier B  strong structural.  Not stated, but derivable from structure with
            no interpretation: a newly introduced module that a later PR
            imports, a migration/schema dependency, an explicit
            follow-up/part-of/depends-on phrase attached to a number, a shared
            uniquely-named entity, or a stacked branch (A's head is B's base).

    Tier C  semantic candidate.  Similar text, compatible component, close in
            time.  **A Tier C edge can never merge two episodes on its own.**
            It needs corroboration from another signal or a human approval, and
            it is marked so the reader knows which kind of claim it is.

Guardrails live here rather than in the clustering step because they are
statements about *evidence quality*, not about graph topology.  A feature flag
touched by sixty PRs is infrastructure and says nothing about one initiative;
a title of "fix: flaky test" is not a uniquely named entity.  Both are demoted
here, with the reason recorded on the edge.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping

from ..config import Phase2Config, days_between, iso, parse_ts
from ..ids import edge_uid
from ..versions import derivation_version

log = logging.getLogger("impact2.graph")

VERSION = derivation_version("artifact_graph")

# Node kinds present in the graph.  `symbol` is a file path: this repository is
# analysed at file granularity because that is what the Phase 1 dependency
# graph resolves, and pretending to symbol precision would be a lie.
NODE_KINDS = (
    "pull_request", "issue", "review_thread", "review_comment", "commit",
    "feature_flag", "symbol", "component", "doc", "actor",
)

# Which Phase 1 edge types are deterministic, and what they are called here.
TIER_A_FROM_PHASE1 = {
    "closes_issue": "closes_issue",
    "timeline_connected": "timeline_connected",
    "reverts": "reverts",
    "reapplies": "reapplies",
    "supersedes": "supersedes",
    "references_pr": "direct_mention",
}
TIER_B_FROM_PHASE1 = {
    "follow_up": "follow_up",
    "part_of": "part_of",
    "stacked_on": "depends_on",
}

# Paths that constitute release/documentation evidence.  Kept as patterns
# rather than a config weight because they describe the repository's layout,
# not a tunable preference.
CHANGELOG_PATTERNS = (
    re.compile(r"(^|/)CHANGELOG(\.[a-z]+)?$", re.IGNORECASE),
    re.compile(r"(^|/)changelog/", re.IGNORECASE),
    re.compile(r"(^|/)posthog\.com/content/", re.IGNORECASE),
    re.compile(r"(^|/)contents/(handbook|docs|blog)/", re.IGNORECASE),
)

MECHANICAL_FLAGS = ("is_lockfile", "is_generated", "is_snapshot", "is_vendor",
                    "is_binary_asset")


def _is_changelog(path: str) -> bool:
    return any(p.search(path) for p in CHANGELOG_PATTERNS)


def _mechanical(row: Mapping[str, Any]) -> bool:
    return any(bool(row.get(flag)) for flag in MECHANICAL_FLAGS)


def _production_file(row: Mapping[str, Any]) -> bool:
    """A file whose change is the substance of the work, not its packaging."""
    if _mechanical(row):
        return False
    return not (row.get("is_test") or row.get("is_docs") or row.get("is_config")
                or row.get("is_localization") or row.get("is_styling"))


class Edge(dict):
    """A graph edge is a plain dict so it round-trips through Parquet unchanged."""


def make_edge(
    *,
    source_kind: str,
    source_key: Any,
    target_kind: str,
    target_key: Any,
    edge_type: str,
    tier: str,
    evidence: str,
    evidence_source: str,
    config: Phase2Config,
    guards: Iterable[str] = (),
    usable_for_clustering: bool = True,
    source_time: Any = None,
    target_time: Any = None,
    extra: Mapping[str, Any] | None = None,
) -> Edge:
    base = float(config.get(f"episodes.tier_strength.{tier}"))
    multiplier = float(
        config.get(f"episodes.edge_type_multiplier.{edge_type}", 1.0)
    )
    guard_list = sorted(set(guards))
    # A demotion guard both lowers the tier and is recorded, so a reader can
    # always see why an edge counts for less than its type suggests.
    strength = round(base * multiplier, 6)
    source_dt, target_dt = parse_ts(source_time), parse_ts(target_time)
    return Edge(
        {
            "edge_uid": edge_uid(
                edge_type, f"{source_kind}/{source_key}", f"{target_kind}/{target_key}"
            ),
            "source_kind": source_kind,
            "source_key": str(source_key),
            "target_kind": target_kind,
            "target_key": str(target_key),
            "edge_type": edge_type,
            "tier": tier,
            "provenance": {
                "A": "deterministic", "B": "structural", "C": "semantic"
            }[tier],
            "base_strength": base,
            "type_multiplier": multiplier,
            "strength": strength,
            "evidence": evidence[:400],
            "evidence_source": evidence_source,
            "guards_applied": guard_list,
            "usable_for_clustering": bool(usable_for_clustering and not guard_list
                                          or (usable_for_clustering and
                                              "demoted" not in " ".join(guard_list))),
            "source_time": iso(source_dt),
            "target_time": iso(target_dt),
            "days_apart": (
                round(abs(days_between(source_dt, target_dt) or 0.0), 3)
                if source_dt and target_dt else None
            ),
            "time_respecting": (
                bool(source_dt and target_dt and target_dt >= source_dt)
                if source_dt and target_dt else None
            ),
            "artifact_graph_version": VERSION,
            **(dict(extra) if extra else {}),
        }
    )


# --------------------------------------------------------------------------
# builder
# --------------------------------------------------------------------------


class ArtifactGraphBuilder:
    """Builds the tiered edge set from Phase 1 tables."""

    def __init__(self, config: Phase2Config, tables: Mapping[str, list[dict[str, Any]]]):
        self.config = config
        self.t = tables
        self.prs: dict[int, dict[str, Any]] = {
            int(p["pr_number"]): p for p in tables.get("pull_requests") or []
        }
        self.merged_at: dict[int, Any] = {
            n: parse_ts(p.get("merged_at")) for n, p in self.prs.items()
        }
        self.files_by_pr: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in tables.get("pr_files") or []:
            self.files_by_pr[int(row["pr_number"])].append(row)
        self.edges: list[Edge] = []
        self.notes: dict[str, Any] = {}

    # -- helpers ---------------------------------------------------------
    def _generic_title(self, number: int) -> bool:
        title = str((self.prs.get(number) or {}).get("title_raw") or "").strip()
        for pattern in self.config.get("episodes.guards.generic_title_patterns"):
            if re.search(pattern, title, re.IGNORECASE):
                return True
        return False

    def _mechanical_only(self, number: int) -> bool:
        rows = self.files_by_pr.get(number) or []
        if not rows:
            return False
        return all(_mechanical(r) for r in rows)

    def _add(self, edge: Edge) -> None:
        self.edges.append(edge)

    # -- Tier A ----------------------------------------------------------
    def build_tier_a(self) -> None:
        """Links something in the data literally states."""
        cfg = self.config

        # 1. Phase 1's deterministic PR<->PR / PR->issue edges.
        for row in self.t.get("candidate_episode_edges") or []:
            phase1_type = str(row.get("edge_type"))
            source = row.get("source_pr_number")
            target = row.get("target_number")
            if source is None or target is None:
                continue
            source, target = int(source), int(target)
            target_kind = (
                "pull_request" if row.get("target_kind") == "pull_request" else "issue"
            )
            mapped = TIER_A_FROM_PHASE1.get(phase1_type)
            tier = "A"
            if mapped is None:
                mapped = TIER_B_FROM_PHASE1.get(phase1_type)
                tier = "B"
            if mapped is None:
                continue      # shared_issue / shared_feature_flag handled below

            guards: list[str] = []
            if target_kind == "pull_request" and (
                self._generic_title(source) or self._generic_title(target)
            ):
                # Two generic maintenance PRs mentioning each other is not an
                # initiative arc.
                if mapped in {"direct_mention", "follow_up"}:
                    guards.append("demoted:generic_title")
            self._add(
                make_edge(
                    source_kind="pull_request", source_key=source,
                    target_kind=target_kind, target_key=target,
                    edge_type=mapped, tier=tier,
                    evidence=str(row.get("evidence") or ""),
                    evidence_source=f"phase1:{row.get('evidence_source')}",
                    config=cfg, guards=guards,
                    usable_for_clustering=not guards,
                    source_time=(self.prs.get(source) or {}).get("merged_at"),
                    target_time=(self.prs.get(target) or {}).get("merged_at"),
                    extra={"phase1_strength": row.get("strength"),
                           "phase1_edge_type": phase1_type},
                )
            )

        # 2. Commit / PR membership.
        for commit in self.t.get("commits") or []:
            number = commit.get("pr_number")
            if number is None:
                continue
            self._add(
                make_edge(
                    source_kind="pull_request", source_key=int(number),
                    target_kind="commit", target_key=commit.get("commit_sha"),
                    edge_type="commit_membership", tier="A",
                    evidence=f"mergeCommit.oid maps PR #{number} to "
                             f"{str(commit.get('commit_sha'))[:12]}",
                    evidence_source="phase1:commits.pr_mapping_source",
                    config=cfg, usable_for_clustering=False,
                    source_time=(self.prs.get(int(number)) or {}).get("merged_at"),
                    target_time=commit.get("committed_at"),
                )
            )

        # 3. Shared feature-flag key, with the fan-out guard.
        by_flag: dict[str, set[int]] = defaultdict(set)
        for row in self.t.get("feature_flags") or []:
            key, number = row.get("flag_key"), row.get("pr_number")
            if key and number is not None:
                by_flag[str(key)].add(int(number))
        tier_a_max = int(cfg.get("episodes.guards.flag_tier_a_max_fanout"))
        hard_max = int(cfg.get("episodes.guards.flag_max_fanout"))
        flag_stats = {"tier_a": 0, "demoted_to_b": 0, "excluded": 0}
        for key, numbers in sorted(by_flag.items()):
            if len(numbers) < 2:
                continue
            if len(numbers) <= tier_a_max:
                tier, guards = "A", []
                flag_stats["tier_a"] += 1
            elif len(numbers) <= hard_max:
                tier, guards = "B", [f"demoted:flag_fanout={len(numbers)}"]
                flag_stats["demoted_to_b"] += 1
            else:
                tier, guards = "C", [f"excluded:flag_fanout={len(numbers)}"]
                flag_stats["excluded"] += 1
            ordered = sorted(numbers)
            for index, source in enumerate(ordered):
                for target in ordered[index + 1:]:
                    self._add(
                        make_edge(
                            source_kind="pull_request", source_key=source,
                            target_kind="pull_request", target_key=target,
                            edge_type="shared_feature_flag", tier=tier,
                            evidence=f"both PRs touch feature flag '{key}' "
                                     f"({len(numbers)} PRs touch it in total)",
                            evidence_source="phase1:feature_flags",
                            config=cfg, guards=guards,
                            usable_for_clustering=not any(
                                g.startswith("excluded") for g in guards
                            ),
                            source_time=(self.prs.get(source) or {}).get("merged_at"),
                            target_time=(self.prs.get(target) or {}).get("merged_at"),
                            extra={"flag_key": key, "flag_fanout": len(numbers)},
                        )
                    )
                    # Flag -> PR membership, kept for evidence display.
            for number in ordered:
                self._add(
                    make_edge(
                        source_kind="pull_request", source_key=number,
                        target_kind="feature_flag", target_key=key,
                        edge_type="shared_feature_flag", tier=tier,
                        evidence=f"PR #{number} references feature flag '{key}'",
                        evidence_source="phase1:feature_flags",
                        config=cfg, usable_for_clustering=False,
                        source_time=(self.prs.get(number) or {}).get("merged_at"),
                    )
                )
        self.notes["feature_flag_edges"] = flag_stats

        # 4. Shared closed issue, with its own fan-out guard (epics exist).
        issue_prs: dict[int, set[int]] = defaultdict(set)
        for row in self.t.get("candidate_episode_edges") or []:
            if row.get("edge_type") == "closes_issue" and row.get("target_number"):
                issue_prs[int(row["target_number"])].add(int(row["source_pr_number"]))
        issue_tier_a = int(cfg.get("episodes.guards.issue_tier_a_max_fanout"))
        issue_hard = int(cfg.get("episodes.guards.issue_max_fanout"))
        issue_stats = {"tier_a": 0, "demoted_to_b": 0, "excluded": 0}
        for issue, numbers in sorted(issue_prs.items()):
            if len(numbers) < 2:
                continue
            if len(numbers) <= issue_tier_a:
                tier, guards = "A", []
                issue_stats["tier_a"] += 1
            elif len(numbers) <= issue_hard:
                tier, guards = "B", [f"demoted:issue_fanout={len(numbers)}"]
                issue_stats["demoted_to_b"] += 1
            else:
                tier, guards = "C", [f"excluded:issue_fanout={len(numbers)}"]
                issue_stats["excluded"] += 1
            ordered = sorted(numbers)
            for index, source in enumerate(ordered):
                for target in ordered[index + 1:]:
                    self._add(
                        make_edge(
                            source_kind="pull_request", source_key=source,
                            target_kind="pull_request", target_key=target,
                            edge_type="closes_issue", tier=tier,
                            evidence=f"both PRs close issue #{issue}",
                            evidence_source="phase1:candidate_episode_edges",
                            config=cfg, guards=guards,
                            usable_for_clustering=not any(
                                g.startswith("excluded") for g in guards
                            ),
                            source_time=(self.prs.get(source) or {}).get("merged_at"),
                            target_time=(self.prs.get(target) or {}).get("merged_at"),
                            extra={"shared_issue_number": issue,
                                   "issue_fanout": len(numbers)},
                        )
                    )
        self.notes["shared_issue_edges"] = issue_stats

        # 5. Changelog / documentation evidence.
        docs = 0
        for number, rows in self.files_by_pr.items():
            for row in rows:
                path = str(row.get("path") or "")
                if _is_changelog(path) or row.get("is_docs"):
                    docs += 1
                    self._add(
                        make_edge(
                            source_kind="pull_request", source_key=number,
                            target_kind="doc", target_key=path,
                            edge_type="changelog_link", tier="A",
                            evidence=f"PR #{number} changes documentation file {path}",
                            evidence_source="phase1:pr_files",
                            config=cfg, usable_for_clustering=False,
                            source_time=(self.prs.get(number) or {}).get("merged_at"),
                            extra={"is_changelog": _is_changelog(path)},
                        )
                    )
                    break     # one doc edge per PR is enough to establish the class
        self.notes["doc_edges"] = docs

        # 6. Review thread -> code change linkage.
        threads = 0
        for row in self.t.get("review_intervention_candidates") or []:
            if not row.get("followed_by_change_in_path"):
                continue
            threads += 1
            self._add(
                make_edge(
                    source_kind="review_thread", source_key=row.get("thread_id"),
                    target_kind="pull_request", target_key=int(row["pr_number"]),
                    edge_type="review_thread_change", tier="A",
                    evidence=str(row.get("follow_evidence") or "")[:300],
                    evidence_source="phase1:review_intervention_candidates",
                    config=cfg, usable_for_clustering=False,
                    source_time=row.get("created_at"),
                    target_time=(self.prs.get(int(row["pr_number"])) or {}).get("merged_at"),
                    extra={"candidate_id": row.get("candidate_id"),
                           "path": row.get("path")},
                )
            )
        self.notes["review_thread_change_edges"] = threads

    # -- Tier B ----------------------------------------------------------
    def build_tier_b(self) -> None:
        """Structure, not statement: symbols, migrations, stacks, named entities."""
        cfg = self.config
        self._build_stacked_branches()
        self._build_symbol_downstream()
        self._build_migration_dependency()
        self._build_named_entities()

    def _build_stacked_branches(self) -> None:
        """A's head branch is B's base branch: B is literally built on A."""
        by_head: dict[str, list[int]] = defaultdict(list)
        for number, pr in self.prs.items():
            head = str(pr.get("head_ref") or "").strip()
            if head and head not in {"master", "main"}:
                by_head[head].append(number)
        count = 0
        for number, pr in sorted(self.prs.items()):
            base = str(pr.get("base_ref") or "").strip()
            if not base or base in {"master", "main"}:
                continue
            for parent in sorted(by_head.get(base, [])):
                if parent == number:
                    continue
                count += 1
                self._add(
                    make_edge(
                        source_kind="pull_request", source_key=number,
                        target_kind="pull_request", target_key=parent,
                        edge_type="stacked_branch", tier="B",
                        evidence=f"PR #{number} targets branch '{base}', which is "
                                 f"the head branch of PR #{parent}",
                        evidence_source="phase1:pull_requests.base_ref/head_ref",
                        config=self.config,
                        source_time=pr.get("created_at"),
                        target_time=(self.prs.get(parent) or {}).get("created_at"),
                        extra={"branch": base},
                    )
                )
        self.notes["stacked_branch_edges"] = count

    def _build_symbol_downstream(self) -> None:
        """A module introduced by one PR and imported by a later PR's file.

        The fan-out guard matters here more than anywhere: a new helper in
        ``lib/utils`` adopted by forty PRs is a leverage signal (and is kept as
        one, in the propagation analytics) but it is not evidence that forty
        PRs are one initiative.
        """
        edges = self.t.get("dependency_edges") or []
        if not edges:
            self.notes["symbol_downstream_edges"] = 0
            return

        importers_of: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            target, source = edge.get("target_path"), edge.get("source_path")
            if target and source:
                importers_of[str(target)].add(str(source))

        introduced_by: dict[str, int] = {}
        for number, rows in self.files_by_pr.items():
            when = self.merged_at.get(number)
            if when is None:
                continue
            for row in rows:
                if row.get("change_status") == "A" and _production_file(row):
                    path = str(row.get("path") or "")
                    # First introducer wins; a re-add later is a different fact.
                    previous = introduced_by.get(path)
                    if previous is None or (
                        self.merged_at.get(previous) or when
                    ) > when:
                        introduced_by[path] = number

        touched_by: dict[str, set[int]] = defaultdict(set)
        for number, rows in self.files_by_pr.items():
            for row in rows:
                touched_by[str(row.get("path") or "")].add(number)

        max_adopters = int(self.config.get("episodes.clustering.hard_max"))
        count = 0
        for path, introducer in sorted(introduced_by.items()):
            introduced_at = self.merged_at.get(introducer)
            if introduced_at is None:
                continue
            adopters: set[int] = set()
            for importer_path in sorted(importers_of.get(path, set())):
                for number in touched_by.get(importer_path, set()):
                    when = self.merged_at.get(number)
                    if number != introducer and when and when > introduced_at:
                        adopters.add(number)
            if not adopters:
                continue
            guards = (
                [f"demoted:symbol_fanout={len(adopters)}"]
                if len(adopters) > max_adopters else []
            )
            for adopter in sorted(adopters):
                count += 1
                self._add(
                    make_edge(
                        source_kind="pull_request", source_key=adopter,
                        target_kind="pull_request", target_key=introducer,
                        edge_type="symbol_downstream", tier="B",
                        evidence=f"PR #{adopter} changed a file importing "
                                 f"'{path}', introduced by PR #{introducer}",
                        evidence_source="phase1:dependency_edges + pr_files",
                        config=self.config, guards=guards,
                        usable_for_clustering=not guards,
                        source_time=self.merged_at.get(adopter),
                        target_time=introduced_at,
                        extra={"introduced_path": path,
                               "adopter_count": len(adopters)},
                    )
                )
        self.notes["symbol_downstream_edges"] = count

    def _build_migration_dependency(self) -> None:
        """Two PRs touching the same migration directory, in order."""
        by_dir: dict[str, list[int]] = defaultdict(list)
        for number, rows in self.files_by_pr.items():
            for row in rows:
                if not row.get("is_migration"):
                    continue
                path = str(row.get("path") or "")
                directory = path.rsplit("/", 1)[0] if "/" in path else path
                if number not in by_dir[directory]:
                    by_dir[directory].append(number)
        max_span = float(
            self.config.get("episodes.clustering.max_span_days_for_structural_join")
        )
        count = 0
        for directory, numbers in sorted(by_dir.items()):
            if len(numbers) < 2 or len(numbers) > 12:
                continue
            ordered = sorted(numbers, key=lambda n: (self.merged_at.get(n) is None,
                                                     self.merged_at.get(n)))
            for index, source in enumerate(ordered):
                for target in ordered[index + 1:]:
                    span = days_between(self.merged_at.get(source),
                                        self.merged_at.get(target))
                    if span is None or abs(span) > max_span:
                        continue
                    count += 1
                    self._add(
                        make_edge(
                            source_kind="pull_request", source_key=target,
                            target_kind="pull_request", target_key=source,
                            edge_type="migration_dependency", tier="B",
                            evidence=f"both PRs change migrations under "
                                     f"'{directory}' within {abs(span):.1f} days",
                            evidence_source="phase1:pr_files.is_migration",
                            config=self.config,
                            source_time=self.merged_at.get(target),
                            target_time=self.merged_at.get(source),
                            extra={"migration_dir": directory},
                        )
                    )
        self.notes["migration_dependency_edges"] = count

    def _build_named_entities(self) -> None:
        """A rare, distinctive name shared by two PRs.

        "insights" is not a named entity on this repository; it appears
        everywhere.  ``document frequency <= N`` is what makes the signal mean
        anything, and the threshold is a config value so it can be varied in
        sensitivity analysis.
        """
        max_df = int(self.config.get("episodes.guards.named_entity_max_document_frequency"))
        min_len = int(self.config.get("episodes.guards.named_entity_min_length"))
        entity_prs: dict[str, set[int]] = defaultdict(set)
        for number, pr in self.prs.items():
            scope = str(pr.get("title_scope") or "").strip().lower()
            if scope and len(scope) >= min_len:
                entity_prs[f"scope:{scope}"].add(number)
            subject = str(pr.get("title_subject") or "")
            # Identifier-shaped tokens: snake_case, kebab-case, CamelCase.
            for token in re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)+\b|"
                                    r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b", subject):
                token = token.strip().lower()
                if len(token) >= min_len:
                    entity_prs[f"token:{token}"].add(number)

        count = 0
        for entity, numbers in sorted(entity_prs.items()):
            if len(numbers) < 2 or len(numbers) > max_df:
                continue
            ordered = sorted(numbers)
            if all(self._generic_title(n) for n in ordered):
                continue
            for index, source in enumerate(ordered):
                for target in ordered[index + 1:]:
                    count += 1
                    self._add(
                        make_edge(
                            source_kind="pull_request", source_key=source,
                            target_kind="pull_request", target_key=target,
                            edge_type="shared_named_entity", tier="B",
                            evidence=f"both titles name '{entity.split(':', 1)[1]}', "
                                     f"which appears in only {len(numbers)} PR titles",
                            evidence_source="phase2:title_entity_extraction",
                            config=self.config,
                            source_time=self.merged_at.get(source),
                            target_time=self.merged_at.get(target),
                            extra={"entity": entity, "document_frequency": len(numbers)},
                        )
                    )
        self.notes["named_entity_edges"] = count

    # -- assembly --------------------------------------------------------
    def build(self, semantic_edges: Iterable[Mapping[str, Any]] = ()) -> list[Edge]:
        self.build_tier_a()
        self.build_tier_b()
        for edge in semantic_edges:
            self.edges.append(Edge(dict(edge)))
        deduped = deduplicate(self.edges)
        log.info(
            "artifact graph: %d edges (%d after dedupe) %s",
            len(self.edges), len(deduped), self.notes,
        )
        return deduped

    def nodes(self) -> list[dict[str, Any]]:
        """Node table: one row per artifact that participates in an edge."""
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        for edge in self.edges:
            for kind, key in (
                (edge["source_kind"], edge["source_key"]),
                (edge["target_kind"], edge["target_key"]),
            ):
                seen.setdefault(
                    (kind, key),
                    {"node_kind": kind, "node_key": key, "degree": 0,
                     "artifact_graph_version": VERSION},
                )
                seen[(kind, key)]["degree"] += 1
        # Every eligible PR is a node even if it has no edges: a lone PR is a
        # perfectly valid one-PR episode and must not vanish.
        for number, pr in self.prs.items():
            key = ("pull_request", str(number))
            seen.setdefault(
                key,
                {"node_kind": "pull_request", "node_key": str(number), "degree": 0,
                 "artifact_graph_version": VERSION},
            )
            seen[key].update(
                {
                    "title": pr.get("title_raw"),
                    "url": pr.get("url"),
                    "merged_at": pr.get("merged_at"),
                    "ranking_eligible": pr.get("ranking_eligible"),
                    "author_actor_id": pr.get("author_actor_id"),
                }
            )
        return sorted(seen.values(), key=lambda n: (n["node_kind"], n["node_key"]))


def deduplicate(edges: Iterable[Edge]) -> list[Edge]:
    """Keep the strongest edge per (source, target, type), tier A winning ties.

    Two rules produce the same edge often (a closing reference also shows up as
    a timeline event). Keeping both would double its weight in clustering,
    which would let bookkeeping masquerade as evidence.
    """
    tier_rank = {"A": 0, "B": 1, "C": 2}
    best: dict[tuple[Any, ...], Edge] = {}
    for edge in edges:
        key = (
            edge["source_kind"], edge["source_key"],
            edge["target_kind"], edge["target_key"], edge["edge_type"],
        )
        current = best.get(key)
        if current is None:
            best[key] = edge
            continue
        if (tier_rank[edge["tier"]], -edge["strength"]) < (
            tier_rank[current["tier"]], -current["strength"]
        ):
            best[key] = edge
    return sorted(
        best.values(),
        key=lambda e: (e["source_kind"], e["source_key"], e["target_kind"],
                       e["target_key"], e["edge_type"]),
    )


def pr_pair_edges(edges: Iterable[Mapping[str, Any]]) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """Undirected PR<->PR view used by clustering. Keys are sorted pairs."""
    pairs: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if edge["source_kind"] != "pull_request" or edge["target_kind"] != "pull_request":
            continue
        try:
            a, b = int(edge["source_key"]), int(edge["target_key"])
        except (TypeError, ValueError):
            continue
        if a == b:
            continue
        pairs[(min(a, b), max(a, b))].append(dict(edge))
    return pairs


def summarise(edges: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(edges)
    by_tier: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    guarded = 0
    for edge in items:
        by_tier[str(edge.get("tier"))] += 1
        by_type[str(edge.get("edge_type"))] += 1
        if edge.get("guards_applied"):
            guarded += 1
    return {
        "edges": len(items),
        "by_tier": dict(sorted(by_tier.items())),
        "by_type": dict(sorted(by_type.items())),
        "guarded": guarded,
        "usable_for_clustering": sum(
            1 for e in items if e.get("usable_for_clustering")
        ),
        "artifact_graph_version": VERSION,
    }
