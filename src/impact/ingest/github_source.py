"""GitHub ingestion: discovery, PR core, PR detail, issues.

Four passes, ordered so each one narrows the work for the next:

1. **discover** -- ``search`` sliced by date.  The search API hard-caps at 1000
   results per query, so a slice that overflows is *split in half recursively*
   until it fits.  Slices are the resume unit.
2. **pr_core** -- 25 PRs per aliased batch (measured: 1 rate-limit point).
   Carries the ``totalCount`` of every expensive connection, which is what
   makes pass 3 cheap.
3. **pr_detail** -- reviews, review threads, issue comments, cross-references.
   Only for PRs whose pass-2 counts say there is something to fetch, and with
   per-PR overflow pagination for the rare PR that exceeds one page.
4. **issues** -- issues linked from PRs plus issues in the window.

Every pass writes immutable raw JSONL before anything is normalized, and every
request goes through the cached client, so a rerun costs no API budget.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterable, Iterator, Sequence

from ..config import Settings, iso, parse_ts
from ..store import RawStore
from . import graphql_queries as Q
from .github_client import GitHubClient
from .runs import ExtractionRun, load_checkpoint, save_checkpoint

log = logging.getLogger("impact.github.source")

UTC = dt.timezone.utc

PR_CORE_BATCH = 25
PR_DETAIL_BATCH = 10
ISSUE_BATCH = 20
SEARCH_PAGE_CAP = 1000


def _chunks(items: Sequence[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _number_buckets(numbers: Iterable[int], size: int) -> list[list[int]]:
    """Group PR numbers into *content-stable* batches.

    Position-based chunking (``numbers[0:25]``, ``[25:50]``, ...) makes every
    batch depend on the whole input list: drop one PR and all downstream
    batches re-chunk, changing their query text and invalidating the entire
    request cache. That turns "narrow the window" into "re-fetch everything".

    Bucketing on ``number // size`` instead means a batch's membership depends
    only on the numbers it contains, so changing the window re-fetches only the
    buckets that actually changed. PR numbers here are ~98% dense, so buckets
    stay close to ``size``.
    """
    buckets: dict[int, list[int]] = {}
    for number in numbers:
        buckets.setdefault(int(number) // size, []).append(int(number))
    return [sorted(buckets[key]) for key in sorted(buckets)]


def _bucket_shard(batch: Sequence[int], size: int) -> str:
    """Shard name derived from the bucket, not from the batch's position."""
    return f"b{(int(batch[0]) // size) * size:08d}"


def _date_slices(
    start: dt.datetime, end: dt.datetime, days: int
) -> list[tuple[dt.date, dt.date]]:
    """Inclusive date-range slices, as GitHub search interprets ``a..b``."""
    out: list[tuple[dt.date, dt.date]] = []
    cursor = start.date()
    last = end.date()
    while cursor <= last:
        stop = min(cursor + dt.timedelta(days=days - 1), last)
        out.append((cursor, stop))
        cursor = stop + dt.timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Pass 1 -- discovery
# ---------------------------------------------------------------------------


def _search_slice(
    client: GitHubClient,
    settings: Settings,
    *,
    cohort: str,
    lo: dt.date,
    hi: dt.date,
    run: ExtractionRun,
    depth: int = 0,
) -> list[dict[str, Any]]:
    """Fetch one date slice, splitting it if it exceeds the 1000-result cap."""
    repo = f"{settings.owner}/{settings.name}"
    kind = "is:pr" if cohort != "issue" else "is:issue"
    field = {"merged": "merged", "created": "created", "updated": "updated",
             "issue": "created"}[cohort]
    query = f"repo:{repo} {kind} {field}:{lo.isoformat()}..{hi.isoformat()}"
    shard = f"{cohort}_{lo.isoformat()}_{hi.isoformat()}"

    first = client.graphql(
        Q.SEARCH_QUERY,
        {"q": query, "cursor": None},
        entity="discovery",
        shard=shard,
        page_index=0,
        query_name=f"discover:{cohort}",
    )
    search = (first.get("data") or {}).get("search") or {}
    total = int(search.get("issueCount") or 0)

    if total > SEARCH_PAGE_CAP and lo < hi:
        # Recursive bisection until every slice fits under the ceiling.
        mid = lo + (hi - lo) / 2
        mid = mid if isinstance(mid, dt.date) else lo
        run.note(f"slice {shard} had {total} results (>{SEARCH_PAGE_CAP}); splitting")
        left = _search_slice(client, settings, cohort=cohort, lo=lo, hi=mid,
                             run=run, depth=depth + 1)
        right = _search_slice(client, settings, cohort=cohort,
                              lo=mid + dt.timedelta(days=1), hi=hi,
                              run=run, depth=depth + 1)
        return left + right

    if total > SEARCH_PAGE_CAP:
        run.note(
            f"UNRESOLVABLE slice {shard}: {total} results in a single day exceeds "
            f"the {SEARCH_PAGE_CAP} search cap; results are truncated"
        )

    nodes: list[dict[str, Any]] = list(search.get("nodes") or [])
    info = search.get("pageInfo") or {}
    cursor = info.get("endCursor")
    page = 1
    while info.get("hasNextPage") and page * 100 < SEARCH_PAGE_CAP:
        payload = client.graphql(
            Q.SEARCH_QUERY,
            {"q": query, "cursor": cursor},
            entity="discovery",
            shard=shard,
            page_index=page,
            query_name=f"discover:{cohort}",
        )
        search = (payload.get("data") or {}).get("search") or {}
        nodes.extend(search.get("nodes") or [])
        info = search.get("pageInfo") or {}
        cursor = info.get("endCursor")
        page += 1

    for node in nodes:
        node["_cohort"] = cohort
        node["_slice"] = shard
        node["_reported_total"] = total
    return [n for n in nodes if n]


def discover(
    settings: Settings, client: GitHubClient, run: ExtractionRun
) -> dict[str, Any]:
    """Build the artifact index for the window across all configured cohorts."""
    raw = RawStore(settings.path("raw", "github"))
    window = settings.window
    cohorts = [window.primary_cohort, *window.also_ingest, "issue"]
    seen_cohorts: list[str] = []
    for cohort in cohorts:
        if cohort not in seen_cohorts:
            seen_cohorts.append(cohort)

    checkpoint = load_checkpoint(settings, "discovery")
    done: set[str] = set(checkpoint.get("completed_slices", []))
    index: dict[int, dict[str, Any]] = {}
    issue_index: dict[int, dict[str, Any]] = {}
    totals: dict[str, int] = {}

    for cohort in seen_cohorts:
        slices = _date_slices(window.start, window.end, window.slice_days)
        for lo, hi in slices:
            shard = f"{cohort}_{lo.isoformat()}_{hi.isoformat()}"
            if shard in done and raw.exists("discovery", shard):
                nodes = raw.read("discovery", shard)
            else:
                nodes = _search_slice(
                    client, settings, cohort=cohort, lo=lo, hi=hi, run=run
                )
                raw.write("discovery", shard, nodes)
                done.add(shard)
                save_checkpoint(
                    settings, "discovery", {"completed_slices": sorted(done)}
                )
            totals[cohort] = totals.get(cohort, 0) + len(nodes)

            for node in nodes:
                number = node.get("number")
                if number is None:
                    continue
                if node.get("__typename") == "Issue":
                    issue_index.setdefault(number, {"number": number, "cohorts": []})
                    issue_index[number]["cohorts"].append(cohort)
                    issue_index[number].update(
                        {k: v for k, v in node.items() if not k.startswith("_")}
                    )
                    continue
                entry = index.setdefault(
                    number, {"number": number, "cohorts": [], "in_window": {}}
                )
                if cohort not in entry["cohorts"]:
                    entry["cohorts"].append(cohort)
                entry.update(
                    {k: v for k, v in node.items() if not k.startswith("_")}
                )

    # Cohort membership drives ranking eligibility later; compute it here from
    # the timestamps rather than trusting which search returned the row.
    for entry in index.values():
        merged_at = parse_ts(entry.get("mergedAt"))
        created_at = parse_ts(entry.get("createdAt"))
        entry["in_window"] = {
            "merged": window.contains(merged_at),
            "created": window.contains(created_at),
        }

    raw.write("pr_index", "index", sorted(index.values(), key=lambda r: r["number"]))
    raw.write(
        "issue_index", "index", sorted(issue_index.values(), key=lambda r: r["number"])
    )

    summary = {
        "cohort_node_counts": totals,
        "distinct_pull_requests": len(index),
        "distinct_issues": len(issue_index),
        "merged_in_window": sum(
            1 for e in index.values() if e["in_window"]["merged"]
        ),
        "created_in_window": sum(
            1 for e in index.values() if e["in_window"]["created"]
        ),
        "slices_completed": len(done),
    }
    run.set("discovery", summary)
    return summary


# ---------------------------------------------------------------------------
# Pass 2 -- PR core
# ---------------------------------------------------------------------------


def fetch_pr_core(
    settings: Settings,
    client: GitHubClient,
    run: ExtractionRun,
    numbers: Sequence[int],
    *,
    entity: str = "pr_core",
) -> list[dict[str, Any]]:
    raw = RawStore(settings.path("raw", "github"))
    numbers = sorted(set(int(n) for n in numbers))
    variables = {"owner": settings.owner, "name": settings.name}
    batches = _number_buckets(numbers, PR_CORE_BATCH)

    def fetch(batch_index: int, batch: list[int]) -> tuple[str, list[dict[str, Any]], int]:
        shard = _bucket_shard(batch, PR_CORE_BATCH)
        if raw.exists(entity, shard):
            return shard, raw.read(entity, shard), 0
        query = Q.build_batch_query(batch, Q.PR_CORE_FIELDS, operation="PRCore")
        payload = client.graphql(
            query, variables, entity=entity, shard=shard,
            page_index=batch_index, query_name="pr_core",
        )
        nodes = Q.parse_batch_response(payload, len(batch))
        missing = sorted(set(batch) - {n.get("number") for n in nodes})
        for number in missing:
            nodes.append(
                {"number": number, "_unresolvable": True,
                 "_reason": "GraphQL returned null for this PR number"}
            )
        # Persist before the caller counts it as done.
        raw.write(entity, shard, nodes)
        return shard, nodes, len(missing)

    collected: list[dict[str, Any]] = []
    done = 0
    # Counters and notes are mutated only here, on the consuming thread.
    for _, batch, result, error in client.map_concurrent(batches, fetch):
        done += 1
        if error is not None:
            run.note(
                f"pr_core batch {_bucket_shard(batch, PR_CORE_BATCH)} failed: {error}"
            )
            run.count("pr_core_failed_batches")
            continue
        assert result is not None
        _, nodes, unresolvable = result
        collected.extend(nodes)
        if unresolvable:
            run.count("pr_core_unresolvable", unresolvable)
        if done % 100 == 0:
            log.info(
                "pr_core %d/%d batches (%d records, graphql remaining=%s)",
                done, len(batches), len(collected), client.state.graphql_remaining,
            )

    client.flush_ledger()
    run.set("pr_core_records", len(collected))
    return collected


# ---------------------------------------------------------------------------
# Pass 3 -- PR detail (reviews / threads / comments / cross-references)
# ---------------------------------------------------------------------------


def _needs_detail(core: dict[str, Any]) -> bool:
    def total(key: str) -> int:
        node = core.get(key) or {}
        return int(node.get("totalCount") or 0) if isinstance(node, dict) else 0

    return any(
        total(k) > 0 for k in ("reviews", "reviewThreads", "comments")
    )


def _paginate_pr_connection(
    client: GitHubClient,
    settings: Settings,
    *,
    number: int,
    query: str,
    connection: str,
    cursor: str | None,
    query_name: str,
) -> list[dict[str, Any]]:
    """Drain the remaining pages of one oversized PR connection.

    Without this, the most-discussed PRs -- exactly the ones an impact analysis
    cares about -- would be silently truncated at the first page.
    """
    out: list[dict[str, Any]] = []
    variables = {"owner": settings.owner, "name": settings.name, "number": number}
    for page in range(1, 40):
        payload = client.graphql(
            query, {**variables, "cursor": cursor},
            entity="pr_overflow", shard=f"{number:07d}_{connection}",
            page_index=page, query_name=query_name,
        )
        pr = ((payload.get("data") or {}).get("repository") or {}).get("pullRequest")
        if not pr:
            break
        conn = pr.get(connection) or {}
        out.extend(n for n in (conn.get("nodes") or []) if n)
        info = conn.get("pageInfo") or {}
        cursor = info.get("endCursor")
        if not info.get("hasNextPage"):
            break
    return out


def fetch_pr_detail(
    settings: Settings,
    client: GitHubClient,
    run: ExtractionRun,
    cores: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw = RawStore(settings.path("raw", "github"))
    targets = sorted(
        {int(c["number"]) for c in cores if c.get("number") and _needs_detail(c)}
    )
    run.set("pr_detail_targets", len(targets))
    variables = {"owner": settings.owner, "name": settings.name}
    batches = _number_buckets(targets, PR_DETAIL_BATCH)

    def fetch(
        batch_index: int, batch: list[int]
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        shard = _bucket_shard(batch, PR_DETAIL_BATCH)
        counters: dict[str, int] = {}
        if raw.exists("pr_detail", shard):
            return raw.read("pr_detail", shard), counters
        query = Q.build_batch_query(batch, Q.PR_DETAIL_FIELDS, operation="PRDetail")
        payload = client.graphql(
            query, variables, entity="pr_detail", shard=shard,
            page_index=batch_index, query_name="pr_detail",
        )
        nodes = Q.parse_batch_response(payload, len(batch))

        for node in nodes:
            number = node.get("number")
            if number is None:
                continue
            for connection, doc, name in (
                ("reviews", Q.PR_REVIEWS_PAGE, "reviews_page"),
                ("reviewThreads", Q.PR_REVIEW_THREADS_PAGE, "threads_page"),
                ("comments", Q.PR_COMMENTS_PAGE, "comments_page"),
            ):
                conn = node.get(connection) or {}
                info = conn.get("pageInfo") or {}
                if not info.get("hasNextPage"):
                    continue
                counters[f"overflow_{connection}"] = counters.get(
                    f"overflow_{connection}", 0
                ) + 1
                extra = _paginate_pr_connection(
                    client, settings, number=int(number), query=doc,
                    connection=connection, cursor=info.get("endCursor"),
                    query_name=name,
                )
                conn.setdefault("nodes", []).extend(extra)
                conn["_overflow_paginated"] = True

            # A single review thread can also exceed its inline comment page.
            for thread in (node.get("reviewThreads") or {}).get("nodes") or []:
                comments = thread.get("comments") or {}
                if (comments.get("pageInfo") or {}).get("hasNextPage"):
                    counters["overflow_thread_comments"] = counters.get(
                        "overflow_thread_comments", 0
                    ) + 1
                    comments["_truncated"] = True

        raw.write("pr_detail", shard, nodes)
        return nodes, counters

    collected: list[dict[str, Any]] = []
    done = 0
    for _, batch, result, error in client.map_concurrent(batches, fetch):
        done += 1
        if error is not None:
            run.note(
                f"pr_detail batch {_bucket_shard(batch, PR_DETAIL_BATCH)} failed: {error}"
            )
            run.count("pr_detail_failed_batches")
            continue
        assert result is not None
        nodes, counters = result
        collected.extend(nodes)
        for key, value in counters.items():
            run.count(key, value)
        if done % 100 == 0:
            log.info(
                "pr_detail %d/%d batches (%d records, graphql remaining=%s)",
                done, len(batches), len(collected), client.state.graphql_remaining,
            )

    client.flush_ledger()
    run.set("pr_detail_records", len(collected))
    return collected


# ---------------------------------------------------------------------------
# Pass 4 -- issues
# ---------------------------------------------------------------------------


def fetch_issues(
    settings: Settings,
    client: GitHubClient,
    run: ExtractionRun,
    numbers: Iterable[int],
) -> list[dict[str, Any]]:
    raw = RawStore(settings.path("raw", "github"))
    targets = sorted({int(n) for n in numbers})
    variables = {"owner": settings.owner, "name": settings.name}
    batches = _number_buckets(targets, ISSUE_BATCH)

    def fetch(batch_index: int, batch: list[int]) -> list[dict[str, Any]]:
        shard = _bucket_shard(batch, ISSUE_BATCH)
        if raw.exists("issues", shard):
            return raw.read("issues", shard)
        query = Q.build_batch_query(
            batch, Q.ISSUE_FIELDS, node="issue", operation="Issues"
        )
        payload = client.graphql(
            query, variables, entity="issues", shard=shard,
            page_index=batch_index, query_name="issues",
        )
        nodes = Q.parse_batch_response(payload, len(batch))
        raw.write("issues", shard, nodes)
        return nodes

    collected: list[dict[str, Any]] = []
    for _, batch, result, error in client.map_concurrent(batches, fetch):
        if error is not None:
            run.note(f"issues batch {batch[0]}-{batch[-1]} failed: {error}")
            run.count("issue_failed_batches")
            continue
        collected.extend(result or [])

    client.flush_ledger()
    run.set("issue_records", len(collected))
    return collected


def fetch_repository(
    settings: Settings, client: GitHubClient, run: ExtractionRun
) -> dict[str, Any]:
    payload = client.graphql(
        Q.REPOSITORY_QUERY,
        {"owner": settings.owner, "name": settings.name},
        entity="repository", shard="meta", query_name="repository",
    )
    repo = (payload.get("data") or {}).get("repository") or {}
    RawStore(settings.path("raw", "github")).write("repository", "meta", [repo])
    return repo


def referenced_issue_numbers(cores: Iterable[dict[str, Any]]) -> set[int]:
    """Issues a PR explicitly closes -- GitHub's own linkage, strongest signal."""
    out: set[int] = set()
    for core in cores:
        refs = (core.get("closingIssuesReferences") or {}).get("nodes") or []
        for ref in refs:
            if ref and ref.get("number"):
                out.add(int(ref["number"]))
    return out
