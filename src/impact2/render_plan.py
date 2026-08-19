"""Which records the dashboard can actually render, decided here rather than there.

The export used to carry every episode and every claim the pipeline produced.
On a large monorepo that is 187 MB across 14 files, of which the site renders a
few per cent: a first-time user is asked for a 6 GB Node heap to build a site
whose visible content would fit in a few tens of megabytes, CI cannot build the
real package at all, and the export is too big to commit anywhere.

The fix is a **projection**. Not a summary: nothing here rounds, aggregates or
truncates a record. A record is either in the package exactly as the pipeline
produced it, or it is not in the package and the manifest says how many were
left out and by which rule. What is left out is what nothing renders.

Deciding that in Python rather than in the UI is the point. The site used to
derive its own episode-page list from the ranking, which meant two
implementations of the same priority rule in two languages, free to disagree
about which episodes exist. Now the export computes the plan, publishes it in
``dashboard_manifest.json``, and the UI renders what it is given. A build
against the full package and a build against the projected package therefore
produce the same pages with the same content, because both read the same plan.

The rule, in order:

1. **Episode pages.** The top five of every available scenario first, since
   that is the two-click path from a ranking to the evidence behind it, then
   everyone else who appears in any ranking. For each of them, their featured
   episodes. Capped, because a page per episode on the reference dataset is
   8,859 pages that nothing links to.
2. **Episodes in a listing.** Every episode a contributor profile shows: the
   featured cards, the current and foundational lists, and the table of
   everything else attributed to them. Each list has the same cap the page
   renders with, so the package holds exactly what the page can display.
3. **Claims.** Every claim rendered from those surfaces. An episode with a page
   renders its narrative, its six dimension rationales and its per-participant
   attribution sentences; an episode that only appears in a listing renders its
   title claim and nothing else. Contributor theses, stability sentences,
   pairwise explanations and the limitations are always rendered, so they are
   always included.
4. **Evidence artifacts.** Those resolved by an included episode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: Written into the manifest so a reader of the package, or of the coverage
#: page, can see the rule that produced it without reading this file.
RULE = (
    "Episode pages: the top five of every available scenario first, then every "
    "other ranked contributor, taking each contributor's featured episodes, "
    "capped at episode_pages. Episodes also included when a contributor profile "
    "lists them, up to the same per-list caps the profile renders with. Claims "
    "included when a rendered surface resolves them: everything for an episode "
    "with a page, the title claim for an episode that only appears in a "
    "listing, plus every contributor thesis, stability sentence, pairwise "
    "explanation and limitation. Evidence artifacts included when an included "
    "episode references them. Records are included whole or not at all."
)


@dataclass(frozen=True)
class RenderBudget:
    """How much of each surface the site renders, and therefore ships.

    These are the UI's own per-page caps, moved to config so that the package
    and the pages that read it cannot disagree about them. Raising one makes
    the site show more and the package bigger, in that order.
    """

    episode_pages: int = 250
    featured: int = 8
    current: int = 6
    foundational: int = 6
    other: int = 40

    @classmethod
    def from_config(cls, section: Mapping[str, Any] | None) -> "RenderBudget":
        section = dict(section or {})
        per_engineer = dict(section.get("per_engineer") or {})
        defaults = cls()
        return cls(
            episode_pages=int(section.get("episode_pages", defaults.episode_pages)),
            featured=int(per_engineer.get("featured", defaults.featured)),
            current=int(per_engineer.get("current", defaults.current)),
            foundational=int(per_engineer.get("foundational", defaults.foundational)),
            other=int(per_engineer.get("other", defaults.other)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "episode_pages": self.episode_pages,
            "per_engineer": {
                "featured": self.featured,
                "current": self.current,
                "foundational": self.foundational,
                "other": self.other,
            },
        }


@dataclass(frozen=True)
class RenderPlan:
    """The decided plan. ``episode_ids`` is everything the site can render."""

    budget: RenderBudget
    episode_page_ids: list[str]
    episode_pages_truncated: int
    episode_ids: set[str] = field(default_factory=set)

    def manifest_block(self) -> dict[str, Any]:
        return {
            **self.budget.as_dict(),
            "episode_page_ids": list(self.episode_page_ids),
            "episode_pages_truncated": self.episode_pages_truncated,
            "rule": RULE,
        }


def featured_episode_ids(engineer: Mapping[str, Any]) -> list[str]:
    """The episodes a contributor profile leads with, in the profile's order.

    Every id comes from a field Phase 2 chose. There is no ordering judgement
    here and no score, which is why the UI can be handed the result.
    """
    out: list[str] = []

    def push(value: Any) -> None:
        identifier = str(value) if value else ""
        if identifier and identifier not in out:
            out.append(identifier)

    push(engineer.get("strongest_evidence_episode_id"))
    for row in engineer.get("dimension_profile") or []:
        push((row or {}).get("top_episode_id"))
    for identifier in engineer.get("current_episode_ids") or []:
        push(identifier)
    for identifier in engineer.get("foundational_episode_ids") or []:
        push(identifier)
    return out


def listing_ids(engineer: Mapping[str, Any], budget: RenderBudget) -> list[str]:
    """Every episode id one contributor profile can put on the page.

    Deduplicated, in profile order. The lists overlap by design (a current
    episode is usually a featured one too) and the UI counts over this result,
    so a repeated id would count a collaborator twice. The TypeScript mirror is
    ``renderedEpisodeIds`` in ``web/src/lib/data.ts``.
    """
    featured = featured_episode_ids(engineer)
    featured_set = set(featured)
    episode_ids = [str(i) for i in (engineer.get("episode_ids") or [])]
    other = [i for i in episode_ids if i not in featured_set]
    ordered = [
        *featured[: budget.featured],
        *[str(i) for i in (engineer.get("current_episode_ids") or [])][: budget.current],
        *[str(i) for i in (engineer.get("foundational_episode_ids") or [])][
            : budget.foundational
        ],
        *other[: budget.other],
    ]
    return list(dict.fromkeys(ordered))


def episode_page_order(
    engineers: Sequence[Mapping[str, Any]],
    scenarios: Sequence[Mapping[str, Any]],
    *,
    budget: RenderBudget,
    known_episode_ids: Iterable[str],
) -> list[str]:
    """Candidate episode pages, most worth having first, before the cap."""
    known = set(known_episode_ids)
    by_actor = {str(e.get("actor_cluster_id")): e for e in engineers}
    order: list[str] = []
    seen: set[str] = set()

    def add_for(actor: Any) -> None:
        engineer = by_actor.get(str(actor))
        if not engineer:
            return
        for identifier in featured_episode_ids(engineer)[: budget.featured]:
            if identifier in known and identifier not in seen:
                seen.add(identifier)
                order.append(identifier)

    # 1. the top five of every available scenario, the two-click evidence path.
    for scenario in scenarios:
        if not scenario.get("available"):
            continue
        positions = sorted(
            scenario.get("positions") or [], key=lambda p: p.get("position", 0)
        )
        for position in positions[:5]:
            add_for(position.get("actor_cluster_id"))
    # 2. everyone else who appears in a ranking at all.
    for scenario in scenarios:
        for position in scenario.get("positions") or []:
            add_for(position.get("actor_cluster_id"))
    return order


def build(
    engineers: Sequence[Mapping[str, Any]],
    scenarios: Sequence[Mapping[str, Any]],
    *,
    budget: RenderBudget,
    known_episode_ids: Iterable[str],
) -> RenderPlan:
    """Compute the plan from the export's own engineer and ranking payloads."""
    known = set(known_episode_ids)
    order = episode_page_order(
        engineers, scenarios, budget=budget, known_episode_ids=known
    )
    page_ids = order[: budget.episode_pages]
    included = set(page_ids)
    for engineer in engineers:
        included.update(i for i in listing_ids(engineer, budget) if i in known)
    return RenderPlan(
        budget=budget,
        episode_page_ids=page_ids,
        episode_pages_truncated=max(0, len(order) - budget.episode_pages),
        episode_ids=included,
    )


# -- what the included records pull in with them -------------------------------

def claim_ids_for_episode(
    episode: Mapping[str, Any], *, has_page: bool
) -> set[str]:
    """Claim ids an episode renders. A listing shows its title and no more."""
    out: set[str] = set()
    if episode.get("title_claim_id"):
        out.add(str(episode["title_claim_id"]))
    if not has_page:
        return out
    for key in ("problem_claim_id", "intervention_claim_id", "outcome_claim_id"):
        if episode.get(key):
            out.add(str(episode[key]))
    for dimension in episode.get("dimensions") or []:
        if (dimension or {}).get("rationale_claim_id"):
            out.add(str(dimension["rationale_claim_id"]))
    for participant in episode.get("participants") or []:
        for identifier in (participant or {}).get("claim_ids") or []:
            if identifier:
                out.add(str(identifier))
    return out


def claim_ids_for_engineer(engineer: Mapping[str, Any]) -> set[str]:
    """A contributor profile always renders its thesis and its stability line."""
    out = {str(c) for c in (engineer.get("thesis_claim_ids") or []) if c}
    stability = (engineer.get("uncertainty") or {}).get("claim_id")
    if stability:
        out.add(str(stability))
    return out


def artifact_ids_for_episode(episode: Mapping[str, Any]) -> set[str]:
    """Evidence the episode resolves out of the sharded evidence files."""
    return {str(a) for a in (episode.get("artifact_ids") or []) if a}
