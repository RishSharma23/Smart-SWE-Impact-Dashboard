"""A + B: dependency propagation, time-decay, and foundational persistence.

What this measures
------------------
An episode introduces or changes files.  Later work imports those files.  That
later work is itself imported by work later still.  Following those hops — and
only ever forwards in time — gives a defensible answer to "did this make later
work possible", which is a very different question from "how big was the diff".

Three properties are non-negotiable and each is enforced below:

*Time-respecting.*  An adopter must have merged strictly after the thing it
adopts.  Without this the "propagation" of a change would include everything
that already imported the file before it was touched, which is nonsense.

*Hub-damped.*  ``lib/utils.ts`` is imported by two thousand files.  Touching it
would otherwise produce a propagation score no feature could ever match.  Each
source path is damped by ``1 / (1 + log2(1 + fan_in))``, paths above the
fan-in percentile cut are dropped entirely, and the per-episode total is
capped.  All three are reported so the cap is visible when it binds.

*Decayed and persistent, reported separately.*  An adoption event's weight is
``exp(-ln2 * age_days / H)``.  But foundational work that is *still* being
adopted near the end of the window should not fade just because it is old, so a
survival floor applies when there is recent, repeated adoption.  Raw age, decay
factor and persistence are emitted as three separate fields — never multiplied
into one opaque number.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from ..config import Phase2Config, days_between, iso, parse_ts
from ..ids import propagation_edge_id
from ..versions import derivation_version

log = logging.getLogger("impact2.analytics.propagation")

VERSION = derivation_version("propagation")
DECAY_VERSION = derivation_version("decay")

MECHANICAL_FLAGS = ("is_lockfile", "is_generated", "is_snapshot", "is_vendor",
                    "is_binary_asset")


def _substantive(row: Mapping[str, Any]) -> bool:
    return not any(bool(row.get(flag)) for flag in MECHANICAL_FLAGS)


def decay_factor(age_days: float, half_life_days: float) -> float:
    if age_days <= 0:
        return 1.0
    return round(math.exp(-math.log(2.0) * age_days / half_life_days), 6)


def hub_damping(fan_in: int | None) -> float:
    """``1 / (1 + log2(1 + fan_in))`` — a hub still counts, just not fifty times."""
    value = int(fan_in or 0)
    return round(1.0 / (1.0 + math.log2(1.0 + max(0, value))), 6)


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (q / 100.0) * (len(ordered) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


class PropagationAnalyzer:
    """Builds time-respecting propagation edges from episodes to later work."""

    def __init__(
        self,
        config: Phase2Config,
        *,
        dependency_edges: Sequence[Mapping[str, Any]],
        module_nodes: Mapping[str, Mapping[str, Any]],
        files_by_pr: Mapping[int, Sequence[Mapping[str, Any]]],
        prs: Mapping[int, Mapping[str, Any]],
        window_end: Any,
    ) -> None:
        self.config = config
        self.nodes = module_nodes
        self.files_by_pr = files_by_pr
        self.prs = prs
        self.window_end = parse_ts(window_end)
        self.merged_at = {n: parse_ts(p.get("merged_at")) for n, p in prs.items()}

        # importers_of[X] = files that import X.
        self.importers_of: dict[str, set[str]] = defaultdict(set)
        for edge in dependency_edges:
            target, source = edge.get("target_path"), edge.get("source_path")
            if target and source:
                self.importers_of[str(target)].add(str(source))

        # touched_by[path] = PRs that changed it, in merge order.
        self.touched_by: dict[str, list[int]] = defaultdict(list)
        for number, rows in files_by_pr.items():
            for row in rows:
                path = str(row.get("path") or "")
                if path:
                    self.touched_by[path].append(number)
        for path in self.touched_by:
            self.touched_by[path].sort(
                key=lambda n: (self.merged_at.get(n) is None, self.merged_at.get(n), n)
            )

        fan_ins = [
            float((node or {}).get("fan_in") or 0) for node in module_nodes.values()
        ]
        cut = float(config.get("analytics.propagation.hub_fan_in_percentile_exclude"))
        self.fan_in_cut = percentile(fan_ins, cut) if fan_ins else float("inf")
        log.info(
            "propagation: fan-in exclusion cut at p%.1f = %.0f (over %d graph nodes)",
            cut, self.fan_in_cut, len(module_nodes),
        )

        # Memoised adopter lists. Without this, every episode re-walks
        # `importers_of[path] x touched_by[importer]` from scratch — the same
        # paths recur across thousands of episodes and the expansion depends
        # only on the path, so it is computed once and reused.
        self._adopters_cache: dict[str, list[tuple[str, int, Any]]] = {}

        # Path -> component, resolved once. Looking this up per edge by
        # rescanning the adopting PR's file list turned the walk quadratic on
        # bulk PRs, which is the difference between minutes and hours here.
        self.component_by_path: dict[str, str] = {}
        for path, node in module_nodes.items():
            component = (node or {}).get("component")
            if component:
                self.component_by_path[str(path)] = str(component)
        for rows in files_by_pr.values():
            for row in rows:
                path = str(row.get("path") or "")
                component = row.get("component")
                if path and component and path not in self.component_by_path:
                    self.component_by_path[path] = str(component)

    # -- sources ---------------------------------------------------------
    def episode_sources(self, pr_numbers: Sequence[int]) -> list[dict[str, Any]]:
        """Files the episode introduced or changed, weighted and damped."""
        weights = self.config.get("analytics.propagation.source_weight")
        max_paths = int(self.config.get("analytics.propagation.max_paths_per_episode"))
        seen: dict[str, dict[str, Any]] = {}
        for number in pr_numbers:
            when = self.merged_at.get(number)
            for row in self.files_by_pr.get(number, []):
                if not _substantive(row):
                    continue
                path = str(row.get("path") or "")
                if not path:
                    continue
                introduced = row.get("change_status") == "A"
                base = float(weights["introduced" if introduced else "modified"])
                node = self.nodes.get(path) or {}
                fan_in = int(node.get("fan_in") or 0)
                if fan_in > self.fan_in_cut:
                    continue           # a hub this large tells us nothing specific
                damped = round(base * hub_damping(fan_in), 6)
                current = seen.get(path)
                if current is None or damped > current["weight"]:
                    seen[path] = {
                        "path": path,
                        "introduced": introduced,
                        "source_pr": number,
                        "source_time": when,
                        "fan_in": fan_in,
                        "base_weight": base,
                        "hub_damping": hub_damping(fan_in),
                        "weight": damped,
                    }
        ordered = sorted(seen.values(), key=lambda s: (-s["weight"], s["path"]))
        return ordered[:max_paths]

    # -- adopters --------------------------------------------------------
    def adopters_of(self, path: str) -> list[tuple[str, int, Any]]:
        """(importing_path, pr_number, merged_at) for everything that imports ``path``.

        Time-ordered and cached. The time filter is applied by the caller,
        because the same list is reused at different points in the walk.
        """
        cached = self._adopters_cache.get(path)
        if cached is not None:
            return cached
        out: list[tuple[str, int, Any]] = []
        for importer in sorted(self.importers_of.get(path, ())):
            for number in self.touched_by.get(importer, ()):
                when = self.merged_at.get(number)
                if when is not None:
                    out.append((importer, number, when))
        out.sort(key=lambda item: (item[2], item[1], item[0]))
        self._adopters_cache[path] = out
        return out

    # -- edges -----------------------------------------------------------
    def propagate(
        self, episode: str, pr_numbers: Sequence[int], *, episode_end: Any
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        max_depth = int(self.config.get("analytics.propagation.max_depth"))
        half_life = float(self.config.get("analytics.decay.half_life_days"))
        mass_cap = float(
            self.config.get("analytics.propagation.max_propagation_mass_per_episode")
        )
        member_set = set(pr_numbers)
        end = parse_ts(episode_end)

        sources = self.episode_sources(pr_numbers)
        if not sources:
            return [], _empty_summary(episode, "episode introduced or changed no "
                                               "graph-resolvable production file")

        # The walk aggregates over *every* adoption event, but materialises a
        # full edge record for only the first `max_recorded_edges` of them. The
        # bands read the aggregates; the records exist so a reader can see
        # concrete examples. Building a 17-key dict for each of several million
        # events cost more than the entire rest of the pipeline and told nobody
        # anything, so the counters below carry the real answer and
        # `adoption_events` vs `edges_recorded` makes the sampling explicit.
        edges: list[dict[str, Any]] = []
        max_recorded = int(
            self.config.get("analytics.propagation.max_recorded_edges_per_episode", 60)
        )
        max_events = int(
            self.config.get("analytics.propagation.max_edges_per_episode", 4000)
        )
        max_frontier = int(
            self.config.get("analytics.propagation.max_frontier_per_depth", 150)
        )
        truncated = False

        events = 0
        raw_mass = 0.0
        reach_paths: set[str] = set()
        reach_components: set[str] = set()
        reach_authors: set[str] = set()
        depth_counts: dict[int, int] = {}
        latest_adoption: Any = None
        recent_events = 0
        persistence_window = float(
            self.config.get("analytics.decay.persistence_window_days")
        )

        # frontier: path -> (time it became available, accumulated weight, depth)
        frontier: dict[str, tuple[Any, float, int]] = {
            s["path"]: (s["source_time"] or end, s["weight"], 0) for s in sources
        }
        visited_paths: set[str] = set(frontier)
        visited_prs: set[int] = set(member_set)

        for depth in range(1, max_depth + 1):
            next_frontier: dict[str, tuple[Any, float, int]] = {}
            for path, (available_at, weight, _) in sorted(frontier.items()):
                if available_at is None:
                    continue
                for importer, adopter, adopted_at in self.adopters_of(path):
                    if adopter in visited_prs:
                        continue
                    if adopted_at <= available_at:
                        continue              # time-respecting: strictly later
                    age = days_between(available_at, adopted_at) or 0.0
                    factor = decay_factor(age, half_life)
                    contribution = weight * factor

                    events += 1
                    raw_mass += contribution
                    reach_paths.add(importer)
                    depth_counts[depth] = depth_counts.get(depth, 0) + 1
                    component = self.component_by_path.get(importer)
                    if component:
                        reach_components.add(component)
                    adopter_pr = self.prs.get(adopter) or {}
                    author = adopter_pr.get("author_actor_id")
                    if author:
                        reach_authors.add(str(author))
                    if latest_adoption is None or adopted_at > latest_adoption:
                        latest_adoption = adopted_at
                    if self.window_end is not None and (
                        days_between(adopted_at, self.window_end) or 1e9
                    ) <= persistence_window:
                        recent_events += 1

                    if len(edges) < max_recorded:
                        edges.append(
                            {
                                "propagation_edge_id": propagation_edge_id(
                                    f"{episode}:{path}", f"pr/{adopter}", depth
                                ),
                                "episode_id": episode,
                                "depth": depth,
                                "source_path": path,
                                "adopting_path": importer,
                                "adopting_pr_number": adopter,
                                "adopting_pr_url": adopter_pr.get("url"),
                                "adopting_author_actor_id": author,
                                "adopting_component": component,
                                "source_available_at": iso(available_at),
                                "adopted_at": iso(adopted_at),
                                "age_days": round(age, 3),
                                "decay_factor": factor,
                                "path_weight": weight,
                                "contribution": round(contribution, 6),
                                "provenance": "deterministic:import_graph",
                                "is_sampled_example": True,
                                "propagation_version": VERSION,
                            }
                        )

                    visited_prs.add(adopter)
                    if importer not in visited_paths:
                        visited_paths.add(importer)
                        next_frontier[importer] = (adopted_at, contribution, depth)
                    if events >= max_events:
                        break
                if events >= max_events:
                    break
            if events >= max_events:
                truncated = True
                break
            if not next_frontier:
                break
            # Keep the strongest-carrying paths only. An unbounded frontier in a
            # monorepo reaches everything by depth 3 and stops being evidence of
            # anything; the cap keeps deeper hops meaningful and bounded.
            if len(next_frontier) > max_frontier:
                next_frontier = dict(
                    sorted(
                        next_frontier.items(),
                        key=lambda item: (-item[1][1], item[0]),
                    )[:max_frontier]
                )
                truncated = True
            frontier = next_frontier

        summary = self._summarise_walk(
            episode, sources, mass_cap,
            events=events, raw_mass=raw_mass, reach_paths=reach_paths,
            reach_components=reach_components, reach_authors=reach_authors,
            depth_counts=depth_counts, latest_adoption=latest_adoption,
            recent_events=recent_events, edges_recorded=len(edges),
        )
        summary["walk_truncated"] = truncated
        summary["max_adoption_events"] = max_events
        return edges, summary

    def _summarise_walk(
        self,
        episode: str,
        sources: Sequence[Mapping[str, Any]],
        mass_cap: float,
        *,
        events: int,
        raw_mass: float,
        reach_paths: set[str],
        reach_components: set[str],
        reach_authors: set[str],
        depth_counts: Mapping[int, int],
        latest_adoption: Any,
        recent_events: int,
        edges_recorded: int,
    ) -> dict[str, Any]:
        if not events:
            return _empty_summary(
                episode, "no later change imports anything this episode touched",
                source_paths=len(sources),
            )
        min_persistence = int(
            self.config.get("analytics.decay.min_events_for_persistence")
        )
        floor = float(self.config.get("analytics.decay.survival_floor"))
        half_life = float(self.config.get("analytics.decay.half_life_days"))
        persistent = recent_events >= min_persistence

        oldest_source = min(
            (s["source_time"] for s in sources if s["source_time"]), default=None
        )
        age = days_between(oldest_source, self.window_end)
        raw_decay = decay_factor(age or 0.0, half_life)
        effective_decay = max(raw_decay, floor) if persistent else raw_decay
        capped = round(min(raw_mass, mass_cap), 6)

        return {
            "episode_id": episode,
            "source_path_count": len(sources),
            "introduced_path_count": sum(1 for s in sources if s["introduced"]),
            "adoption_events": events,
            "edges_recorded": edges_recorded,
            "reach_file_count": len(reach_paths),
            "reach_pr_count": events,
            "distinct_component_penetration": len(reach_components),
            "components_reached": sorted(reach_components)[:25],
            "distinct_downstream_authors": len(reach_authors),
            "max_path_depth": max(depth_counts) if depth_counts else 0,
            "depth_histogram": {str(d): depth_counts[d] for d in sorted(depth_counts)},
            "raw_mass": round(raw_mass, 6),
            "mass_after_cap": capped,
            "cap_applied": raw_mass > mass_cap,
            "mass_cap": mass_cap,
            # Reported separately, never multiplied together into one number.
            "source_age_days": round(age, 2) if age is not None else None,
            "raw_decay_factor": raw_decay,
            "persistence_detected": persistent,
            "persistence_events_in_window": recent_events,
            "survival_floor": floor,
            "effective_decay_factor": round(effective_decay, 6),
            "latest_adoption_at": iso(latest_adoption),
            "reason": None,
            "propagation_version": VERSION,
            "decay_version": DECAY_VERSION,
        }

def _empty_summary(episode: str, reason: str, source_paths: int = 0) -> dict[str, Any]:
    return {
        "episode_id": episode,
        "source_path_count": source_paths,
        "introduced_path_count": 0,
        "reach_file_count": 0,
        "reach_pr_count": 0,
        "distinct_component_penetration": 0,
        "components_reached": [],
        "distinct_downstream_authors": 0,
        "max_path_depth": 0,
        "depth_histogram": {},
        "raw_mass": 0.0,
        "mass_after_cap": 0.0,
        "cap_applied": False,
        "mass_cap": None,
        "source_age_days": None,
        "raw_decay_factor": None,
        "persistence_detected": False,
        "persistence_events_in_window": 0,
        "survival_floor": None,
        "effective_decay_factor": None,
        "latest_adoption_at": None,
        # An explicit reason, so "0 downstream" never reads as "we didn't look".
        "reason": reason,
        "propagation_version": VERSION,
        "decay_version": DECAY_VERSION,
    }


def summarise(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    reaching = [r for r in items if int(r.get("reach_file_count") or 0) > 0]
    return {
        "episodes_analysed": len(items),
        "episodes_with_downstream_adoption": len(reaching),
        "episodes_with_no_resolvable_source": sum(
            1 for r in items if r.get("reason") and "graph-resolvable" in str(r["reason"])
        ),
        "cap_applied_count": sum(1 for r in items if r.get("cap_applied")),
        "persistent_episodes": sum(1 for r in items if r.get("persistence_detected")),
        "max_component_penetration": max(
            (int(r.get("distinct_component_penetration") or 0) for r in items),
            default=0,
        ),
        "max_downstream_authors": max(
            (int(r.get("distinct_downstream_authors") or 0) for r in items), default=0
        ),
        "propagation_version": VERSION,
        "decay_version": DECAY_VERSION,
    }
