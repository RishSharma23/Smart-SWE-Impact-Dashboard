"""Blast-radius evidence.

The spec is explicit that this must be *multiple explainable signals, never a
single raw "files changed" proxy*.  So this module emits eight independent
signals and one band, and every one of them can be traced back to a concrete
artefact (a component name, an owning team, a graph edge, a path glob):

    boundaries      distinct components / platforms / product verticals touched
    ownership       distinct owning teams, and whether the change crosses them
    graph           fan-in / fan-out of the changed nodes, hub involvement
    reach           bounded reverse-reachability from the changed files
    surfaces        API / schema / migration / auth / billing / ... touch flags
    downstream      distinct components that depend on what changed
    band            local | component | cross_product | platform_wide | unknown
    uncertainty     why the band might be wrong

``unknown`` is a first-class band.  A change to a Rust file has no parsed
edges, and reporting it as ``local`` would be a lie; it is reported as unknown
with the reason attached.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..versions import feature_version

# Languages whose imports this phase does not parse. A changed file in one of
# these cannot be given a reach band from graph evidence.
UNPARSED_LANGUAGES = {"rust", "go", "sql", "hog", "ruby", "java", "kotlin", "swift"}

# Distinct downstream products that must be reachable before a change is called
# platform-wide on spread alone.
PLATFORM_WIDE_MIN_PRODUCTS = 5


def compute(
    pr: Mapping[str, Any],
    files: list[Mapping[str, Any]],
    *,
    nodes: Mapping[str, Mapping[str, Any]],
    reverse_adjacency: Mapping[str, set[str]],
    max_depth: int = 3,
) -> dict[str, Any]:
    paths = [str(f["path"]) for f in files if f.get("path")]
    components = {str(f.get("component") or "unknown") for f in files}
    components.discard("unknown")
    platforms = {str(f.get("platform") or "unknown") for f in files}
    platforms.discard("unknown")
    products = {c for c in components if c.startswith("product:")}

    owners: set[str] = set()
    unowned = 0
    for row in files:
        row_owners = row.get("owners") or []
        if not row_owners:
            unowned += 1
        for owner in row_owners:
            owners.add(str(owner))

    # -- graph signals ---------------------------------------------------
    fan_in_total = 0
    fan_out_total = 0
    hub_files = 0
    graph_known = 0
    for path in paths:
        node = nodes.get(path)
        if not node:
            continue
        graph_known += 1
        fan_in_total += int(node.get("fan_in") or 0)
        fan_out_total += int(node.get("fan_out") or 0)
        if node.get("is_hub"):
            hub_files += 1

    # Reverse reachability: who would notice if these files broke.
    seen: set[str] = set(paths)
    frontier: set[str] = set(paths)
    for _ in range(max_depth):
        nxt: set[str] = set()
        for path in frontier:
            nxt |= reverse_adjacency.get(path, set()) - seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    dependents = seen - set(paths)
    downstream_components = {
        str((nodes.get(p) or {}).get("component") or "unknown") for p in dependents
    }
    downstream_components.discard("unknown")
    downstream_products = {c for c in downstream_components if c.startswith("product:")}

    # -- uncertainty -----------------------------------------------------
    uncertainty: list[str] = []
    languages = {str(f.get("language") or "unknown") for f in files}
    unparsed = languages & UNPARSED_LANGUAGES
    if unparsed:
        uncertainty.append(
            f"no import parser for {sorted(unparsed)}; graph reach understated"
        )
    if any(f.get("is_generated") for f in files):
        uncertainty.append("generated files changed; their edges may be synthetic")
    if any(f.get("is_binary") for f in files):
        uncertainty.append("binary files changed; no line-level or import evidence")
    dynamic_files = sum(
        1 for p in paths if (nodes.get(p) or {}).get("has_dynamic_imports")
    )
    if dynamic_files:
        uncertainty.append(
            f"{dynamic_files} changed file(s) use dynamic imports; edges incomplete"
        )
    coverage = graph_known / len(paths) if paths else 0.0
    if paths and coverage < 0.5:
        uncertainty.append(
            f"only {graph_known}/{len(paths)} changed files are graph nodes"
        )
    if unowned:
        uncertainty.append(f"{unowned} changed file(s) have no owner rule")

    # -- band ------------------------------------------------------------
    shared_library_touch = any(
        "shared_library" in (f.get("risk_surfaces") or []) for f in files
    )
    platform_surface = any(
        set(f.get("risk_surfaces") or []) & {"deployment", "ingestion", "data_pipeline"}
        for f in files
    )

    # `platform_wide` has to mean something. Requiring only "touches a shared
    # library" or "reaches >=3 products" put 41% of PRs in this band on the
    # real dataset, which is not a discriminating summary -- in a monorepo,
    # almost anything under frontend/src/lib reaches most products. The band
    # therefore needs *corroborated* breadth: a shared-library or platform
    # surface touch AND observed downstream spread, or spread on its own that
    # is wide enough to speak for itself.
    broad_downstream = len(downstream_products) >= PLATFORM_WIDE_MIN_PRODUCTS
    corroborated_shared = (shared_library_touch or platform_surface) and (
        len(downstream_products) >= 2 or hub_files > 0
    )

    if not paths:
        # No file data at all: the PR is unmerged, or its merge commit is
        # outside the cloned history. Either way reach is unmeasured, not small.
        band = "unknown"
        uncertainty.append(
            "no changed-file data for this PR (unmerged, or merge commit "
            "outside the cloned history)"
        )
    elif coverage == 0.0 and not components:
        band = "unknown"
    elif broad_downstream or corroborated_shared:
        band = "platform_wide"
    elif len(products) >= 2 or len(downstream_products) >= 2:
        band = "cross_product"
    elif len(components) > 1:
        band = "component"
    elif len(components) == 1:
        directories = {p.rsplit("/", 1)[0] for p in paths}
        band = "local" if len(directories) <= 1 and not dependents else "component"
    else:
        band = "unknown"

    # A change confined to a language we cannot parse has NO graph evidence.
    # Knowing its component does not tell us its reach, and reporting "local"
    # would assert something we did not measure. Path-based evidence
    # (shared-library / platform surfaces) still overrides, because that is a
    # real observation rather than an absence of one.
    if band in {"local", "component"} and unparsed and graph_known == 0:
        band = "unknown"
        uncertainty.append(
            "band downgraded to unknown: no changed file has parsed imports"
        )

    if band != "unknown" and coverage < 0.5 and not components:
        band = "unknown"

    return {
        "pr_number": pr["pr_number"],
        "pr_id": pr["pr_id"],
        # boundaries
        "distinct_components": len(components),
        "distinct_platforms": len(platforms),
        "distinct_products": len(products),
        "components_touched": sorted(components),
        "products_touched": sorted(products),
        "crosses_component_boundary": len(components) > 1,
        "crosses_product_boundary": len(products) > 1,
        # ownership
        "distinct_owners": len(owners),
        "owners_touched": sorted(owners),
        "crosses_ownership_boundary": len(owners) > 1,
        "files_without_owner": unowned,
        # graph
        "graph_covered_files": graph_known,
        "graph_coverage_share": round(coverage, 4) if paths else None,
        "changed_fan_in_total": fan_in_total if graph_known else None,
        "changed_fan_out_total": fan_out_total if graph_known else None,
        "changed_fan_in_max": max(
            (int((nodes.get(p) or {}).get("fan_in") or 0) for p in paths), default=None
        ) if graph_known else None,
        "hub_files_touched": hub_files,
        # downstream reach
        "downstream_file_count": len(dependents),
        "downstream_component_count": len(downstream_components),
        "downstream_components": sorted(downstream_components)[:50],
        "downstream_product_count": len(downstream_products),
        "reach_depth_limit": max_depth,
        # surfaces
        "risk_surface_count": len(
            {s for f in files for s in (f.get("risk_surfaces") or [])}
        ),
        "touches_shared_library": shared_library_touch,
        "touches_platform_surface": platform_surface,
        # band + uncertainty
        "reachability_band": band,
        "reachability_uncertainty": uncertainty,
        "reachability_is_uncertain": bool(uncertainty),
        "blast_radius_version": feature_version("blast_radius"),
    }


def summarise(
    rows: Iterable[Mapping[str, Any]], eligible: set[int] | None = None
) -> dict[str, Any]:
    """Summarise bands.

    The overall distribution is dominated by unmerged PRs, which have no file
    data and are correctly ``unknown``. The eligible-only distribution is the
    one that says anything about merged work, so both are reported.
    """
    items = list(rows)
    total = len(items) or 1

    def distribution(subset: list[Mapping[str, Any]]) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in subset:
            key = str(row.get("reachability_band"))
            out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items()))

    eligible_rows = (
        [r for r in items if int(r.get("pr_number", -1)) in eligible]
        if eligible is not None
        else []
    )
    payload = {
        "pull_requests": len(items),
        "band_distribution": distribution(items),
        "unknown_band_rate": round(
            distribution(items).get("unknown", 0) / total, 4
        ),
        "crossing_component_boundary": sum(
            1 for r in items if r.get("crosses_component_boundary")
        ),
        "crossing_ownership_boundary": sum(
            1 for r in items if r.get("crosses_ownership_boundary")
        ),
        "with_uncertainty": sum(1 for r in items if r.get("reachability_is_uncertain")),
    }
    if eligible is not None:
        counts = distribution(eligible_rows)
        payload["ranking_eligible_pull_requests"] = len(eligible_rows)
        payload["band_distribution_eligible_only"] = counts
        payload["unknown_band_rate_eligible_only"] = round(
            counts.get("unknown", 0) / max(len(eligible_rows), 1), 4
        )
    return payload
