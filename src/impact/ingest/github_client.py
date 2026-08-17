"""GitHub GraphQL + REST client: cached, resumable, rate-limit aware.

Behaviour that the phase spec requires and this module implements:

* **Cached / idempotent** -- every request is keyed by a hash of
  (method, url, body).  A request whose response is already on disk and marked
  ``ok`` in the ledger is served from disk, so rerunning the whole pipeline
  costs zero API budget and yields byte-identical raw data.
* **Persist before advancing** -- the response body is written and the ledger
  flushed *before* the caller is allowed to move to the next cursor.  An
  interrupt therefore loses at most the in-flight page, never a completed one.
* **Rate-limit aware** -- limits are read from what GitHub actually returns
  (GraphQL ``rateLimit`` block, REST ``x-ratelimit-*`` headers), never
  hard-coded.  We pause before hitting zero rather than after.
* **Secondary limits** -- 403/429 with ``retry-after`` or a "secondary rate
  limit" body is honoured exactly, then retried with exponential backoff and
  full jitter.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence, TypeVar

import requests

from ..config import Settings, github_token, iso
from ..hashing import request_hash, sha256_text
from ..store import RawStore
from ..versions import EXTRACTOR_VERSION
from .runs import RawPageLedger

log = logging.getLogger("impact.github")

UTC = dt.timezone.utc
GRAPHQL_URL = "https://api.github.com/graphql"
REST_ROOT = "https://api.github.com"

USER_AGENT = f"posthog-impact-phase1/{EXTRACTOR_VERSION} (analysis; read-only)"

# Pause when fewer than this many points/requests remain, so a burst of
# in-flight retries cannot push us over into a hard block.
GRAPHQL_FLOOR = 120
REST_FLOOR = 60

RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 6

# Concurrency is bounded by GitHub's *secondary* limits, not by the points
# budget. Measured on this repository:
#
#   * a 25-alias batch costs 1 point but ~5s of server time, and the whole
#     window costs well under one hour's 5,000-point allowance;
#   * GitHub's documented secondary limit is 90 CPU-seconds per 60 seconds of
#     wall clock, so ~5s-per-request work sustains only ~1.5 concurrent;
#   * 12 workers produced continuous HTTP 403 + `Retry-After: 60`, and 4 still
#     produced them intermittently. The backoff recovers correctly every time,
#     but time spent sleeping is time not spent fetching.
#
# 2 is therefore the throughput-optimal setting, not a timid one. Raising it
# makes the run slower, not faster.
DEFAULT_WORKERS = 2

# The ledger rewrites its whole file on flush, under the shared lock, so it is
# batched. Response bodies are already durable before the row is written.
LEDGER_FLUSH_EVERY = 200

T = TypeVar("T")
R = TypeVar("R")


class RateLimited(RuntimeError):
    pass


class GraphQLError(RuntimeError):
    def __init__(self, errors: list[dict[str, Any]], query_name: str) -> None:
        self.errors = errors
        super().__init__(f"{query_name}: {json.dumps(errors)[:800]}")


@dataclass
class RateLimitState:
    """Last-known limits, refreshed from every response."""

    graphql_remaining: int | None = None
    graphql_limit: int | None = None
    graphql_reset_at: dt.datetime | None = None
    graphql_cost_spent: int = 0
    rest_remaining: int | None = None
    rest_limit: int | None = None
    rest_reset_at: dt.datetime | None = None
    rest_requests: int = 0
    graphql_requests: int = 0
    sleep_seconds_total: float = 0.0
    retries: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "graphql_requests": self.graphql_requests,
            "graphql_points_spent": self.graphql_cost_spent,
            "graphql_remaining": self.graphql_remaining,
            "graphql_limit": self.graphql_limit,
            "graphql_reset_at": iso(self.graphql_reset_at),
            "rest_requests": self.rest_requests,
            "rest_remaining": self.rest_remaining,
            "rest_limit": self.rest_limit,
            "rest_reset_at": iso(self.rest_reset_at),
            "rate_limit_sleep_seconds": round(self.sleep_seconds_total, 2),
            "retries": self.retries,
        }


@dataclass
class GitHubClient:
    settings: Settings
    ledger: RawPageLedger
    raw: RawStore
    token: str = field(repr=False, default="")
    state: RateLimitState = field(default_factory=RateLimitState)
    offline: bool = False
    workers: int = DEFAULT_WORKERS
    sleeper: Callable[[float], None] = time.sleep
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _local: threading.local = field(default_factory=threading.local, repr=False)
    _pending_flush: int = field(default=0, repr=False)

    @classmethod
    def build(
        cls, settings: Settings, *, offline: bool = False, workers: int = DEFAULT_WORKERS
    ) -> "GitHubClient":
        raw_root = settings.path("raw", "github")
        ledger = RawPageLedger.load(raw_root / "_ledger.json")
        token = "" if offline else github_token()
        return cls(
            settings=settings,
            ledger=ledger,
            raw=RawStore(raw_root),
            token=token,
            offline=offline,
            workers=max(1, int(workers)),
        )

    @property
    def session(self) -> requests.Session:
        """One session per thread.

        ``requests.Session`` is not documented as thread-safe; sharing one
        across the pool risks interleaved connection state for no benefit,
        since each thread keeps its own keep-alive connection anyway.
        """
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                }
            )
            if self.token:
                session.headers["Authorization"] = f"bearer {self.token}"
            self._local.session = session
        return session

    # -- concurrency -----------------------------------------------------

    def map_concurrent(
        self, items: Sequence[T], fn: Callable[[int, T], R]
    ) -> Iterator[tuple[int, T, R | None, BaseException | None]]:
        """Run ``fn`` over ``items`` in the pool, yielding results as they land.

        Failures are yielded, not raised: one bad batch must not abort an
        extraction that has already paid for thousands of good ones. Ordering
        is by completion, so callers must not depend on input order.
        """
        if self.workers <= 1:
            for index, item in enumerate(items):
                try:
                    yield index, item, fn(index, item), None
                except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                    yield index, item, None, exc
            return

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(fn, index, item): (index, item)
                for index, item in enumerate(items)
            }
            from concurrent.futures import as_completed

            for future in as_completed(futures):
                index, item = futures[future]
                try:
                    yield index, item, future.result(), None
                except BaseException as exc:  # noqa: BLE001
                    yield index, item, None, exc

    # -- internals -------------------------------------------------------

    def _response_path(self, entity: str, req_hash: str) -> Path:
        return self.raw.root / entity / "_responses" / f"{req_hash}.json.gz"

    def _store_response(self, entity: str, req_hash: str, payload: Any) -> tuple[Path, str]:
        import gzip

        path = self._response_path(entity, req_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        path.write_bytes(gzip.compress(body.encode("utf-8"), compresslevel=6))
        return path, sha256_text(body)

    def _load_response(self, entity: str, req_hash: str) -> Any | None:
        import gzip

        path = self._response_path(entity, req_hash)
        if not path.exists():
            return None
        try:
            return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        except (OSError, ValueError, EOFError):
            log.warning("corrupt cached response %s; refetching", path.name)
            return None

    def _sleep(self, seconds: float, why: str) -> None:
        seconds = max(0.0, min(seconds, 3600.0))
        if seconds <= 0:
            return
        with self._lock:
            self.state.sleep_seconds_total += seconds
        log.info("sleeping %.1fs (%s)", seconds, why)
        self.sleeper(seconds)

    def _respect_graphql_floor(self) -> None:
        with self._lock:
            rem = self.state.graphql_remaining
            reset = self.state.graphql_reset_at
        if rem is None or rem > GRAPHQL_FLOOR:
            return
        wait = 60.0
        if reset:
            wait = max(1.0, (reset - dt.datetime.now(UTC)).total_seconds() + 5)
        self._sleep(wait, f"graphql budget {rem} <= floor {GRAPHQL_FLOOR}")
        with self._lock:
            self.state.graphql_remaining = None

    def _respect_rest_floor(self) -> None:
        with self._lock:
            rem = self.state.rest_remaining
            reset = self.state.rest_reset_at
        if rem is None or rem > REST_FLOOR:
            return
        wait = 60.0
        if reset:
            wait = max(1.0, (reset - dt.datetime.now(UTC)).total_seconds() + 5)
        self._sleep(wait, f"rest budget {rem} <= floor {REST_FLOOR}")
        with self._lock:
            self.state.rest_remaining = None

    def _absorb_rest_headers(self, resp: requests.Response) -> None:
        h = resp.headers
        if "x-ratelimit-remaining" in h:
            try:
                with self._lock:
                    self.state.rest_remaining = int(h["x-ratelimit-remaining"])
                    self.state.rest_limit = int(h.get("x-ratelimit-limit", 0)) or None
                    self.state.rest_reset_at = dt.datetime.fromtimestamp(
                        int(h["x-ratelimit-reset"]), UTC
                    )
            except (ValueError, KeyError):
                pass

    def flush_ledger(self, *, force: bool = True) -> None:
        """Persist the ledger.

        Flushing rewrites the whole file, so doing it per request is O(n^2)
        over a run with thousands of pages. Response *bodies* are already on
        disk before the ledger row is written, so a lost tail costs at most a
        re-fetch of the last few pages on resume -- never a silent gap.
        """
        with self._lock:
            if not force and self._pending_flush < LEDGER_FLUSH_EVERY:
                return
            self._pending_flush = 0
            self.ledger.flush()

    def _backoff(self, attempt: int, resp: requests.Response | None) -> float:
        """Honour Retry-After when present, else exponential with full jitter."""
        if resp is not None:
            retry_after = resp.headers.get("retry-after")
            if retry_after:
                try:
                    return float(retry_after) + 1
                except ValueError:
                    pass
            reset = resp.headers.get("x-ratelimit-reset")
            if reset and resp.headers.get("x-ratelimit-remaining") == "0":
                try:
                    delta = int(reset) - time.time()
                    if delta > 0:
                        return min(delta + 5, 3600)
                except ValueError:
                    pass
        return random.uniform(0, min(60.0, 2.0**attempt))

    # -- GraphQL ---------------------------------------------------------

    def graphql(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        entity: str,
        shard: str,
        page_index: int = 0,
        query_name: str = "query",
        force: bool = False,
        tolerate_partial: bool = True,
    ) -> dict[str, Any]:
        """Execute one GraphQL request, serving from cache when possible.

        ``tolerate_partial`` reflects a real GitHub behaviour: a query can come
        back with both ``data`` and ``errors`` (e.g. one deleted user inside a
        page).  Dropping the whole page would create a silent gap, so we keep
        the data and record the errors on the ledger row.
        """
        body = {"query": query, "variables": variables}
        req_hash = request_hash("POST", GRAPHQL_URL, body)

        with self._lock:
            already_ok = self.ledger.succeeded(req_hash)
        if not force and already_ok:
            cached = self._load_response(entity, req_hash)
            if cached is not None:
                return cached

        if self.offline:
            raise RuntimeError(
                f"offline mode: no cached response for {query_name} "
                f"({entity}/{shard} page {page_index})"
            )

        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            self._respect_graphql_floor()
            started = time.monotonic()
            try:
                resp = self.session.post(GRAPHQL_URL, json=body, timeout=90)
            except requests.RequestException as exc:
                last_error = exc
                with self._lock:
                    self.state.retries += 1
                self._sleep(self._backoff(attempt, None), f"network error: {exc}")
                continue

            with self._lock:
                self.state.graphql_requests += 1
            self._absorb_rest_headers(resp)

            if resp.status_code in RETRYABLE_STATUS:
                text = resp.text[:400]
                with self._lock:
                    self.state.retries += 1
                last_error = RateLimited(f"HTTP {resp.status_code}: {text}")
                self._sleep(
                    self._backoff(attempt, resp),
                    f"HTTP {resp.status_code} on {query_name}",
                )
                continue

            if resp.status_code >= 400:
                self._record_page(
                    entity, shard, page_index, req_hash, query_name,
                    status="http_error", http_status=resp.status_code,
                    error=resp.text[:500], response_path=None, response_hash=None,
                    cost=None, elapsed=time.monotonic() - started,
                    attempts=attempt + 1, variables=variables,
                )
                resp.raise_for_status()

            payload = resp.json()
            errors = payload.get("errors") or []
            data = payload.get("data")

            # A rate-limit error arrives as a 200 with an errors block.
            if errors and any(
                e.get("type") in {"RATE_LIMITED", "SERVICE_UNAVAILABLE"} for e in errors
            ):
                with self._lock:
                    self.state.retries += 1
                last_error = RateLimited(json.dumps(errors)[:400])
                self._sleep(self._backoff(attempt, resp), "graphql RATE_LIMITED")
                continue

            rl = (data or {}).get("rateLimit") if isinstance(data, dict) else None
            cost = None
            if isinstance(rl, dict):
                cost = rl.get("cost")
                with self._lock:
                    self.state.graphql_remaining = rl.get("remaining")
                    self.state.graphql_limit = rl.get("limit")
                    self.state.graphql_cost_spent += int(cost or 0)
                    if rl.get("resetAt"):
                        self.state.graphql_reset_at = dt.datetime.fromisoformat(
                            str(rl["resetAt"]).replace("Z", "+00:00")
                        )

            if data is None or (errors and not tolerate_partial):
                # GitHub answers a server-side failure as HTTP 200 with
                # data:null and a generic "Something went wrong while executing
                # your query" message. It is transient and retryable, but it
                # carries no `type`, so the RATE_LIMITED branch above misses it
                # and the first version of this client gave up on attempt 1 --
                # silently losing 3 batches (~30 PRs of review detail) in a
                # 2-hour run. Retry it like any other 5xx.
                transient = data is None and any(
                    "something went wrong" in str(e.get("message", "")).lower()
                    or e.get("type") in {"INTERNAL", "SERVICE_UNAVAILABLE"}
                    for e in errors
                )
                if transient and attempt < MAX_ATTEMPTS - 1:
                    with self._lock:
                        self.state.retries += 1
                    last_error = GraphQLError(errors, query_name)
                    self._sleep(
                        self._backoff(attempt, resp),
                        f"transient graphql failure on {query_name}",
                    )
                    continue

                self._record_page(
                    entity, shard, page_index, req_hash, query_name,
                    status="graphql_error", http_status=resp.status_code,
                    error=json.dumps(errors)[:1000], response_path=None,
                    response_hash=None, cost=cost,
                    elapsed=time.monotonic() - started, attempts=attempt + 1,
                    # A terminal failure must say so, or the resume gate cannot
                    # tell "gave up for a reason" from "silently vanished".
                    terminal_reason=f"graphql error after {attempt + 1} attempts",
                    variables=variables,
                )
                raise GraphQLError(errors, query_name)

            # Persist BEFORE returning so the caller can never advance past an
            # unsaved page.
            path, digest = self._store_response(entity, req_hash, payload)
            self._record_page(
                entity, shard, page_index, req_hash, query_name,
                status="ok", http_status=resp.status_code,
                error=json.dumps(errors)[:1000] if errors else None,
                response_path=str(path.relative_to(self.settings.project_root)),
                response_hash=digest, cost=cost,
                elapsed=time.monotonic() - started, attempts=attempt + 1,
                variables=variables,
            )
            self.flush_ledger(force=False)
            return payload

        raise RateLimited(
            f"{query_name} failed after {MAX_ATTEMPTS} attempts: {last_error}"
        )

    # -- REST ------------------------------------------------------------

    def rest(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        entity: str = "rest",
        shard: str = "misc",
        page_index: int = 0,
        force: bool = False,
    ) -> Any:
        url = path if path.startswith("http") else f"{REST_ROOT}{path}"
        req_hash = request_hash("GET", url, params or {})

        with self._lock:
            already_ok = self.ledger.succeeded(req_hash)
        if not force and already_ok:
            cached = self._load_response(entity, req_hash)
            if cached is not None:
                return cached

        if self.offline:
            raise RuntimeError(f"offline mode: no cached response for GET {url}")

        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            self._respect_rest_floor()
            started = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=90)
            except requests.RequestException as exc:
                last_error = exc
                with self._lock:
                    self.state.retries += 1
                self._sleep(self._backoff(attempt, None), f"network error: {exc}")
                continue

            with self._lock:
                self.state.rest_requests += 1
            self._absorb_rest_headers(resp)

            if resp.status_code in RETRYABLE_STATUS:
                with self._lock:
                    self.state.retries += 1
                last_error = RateLimited(f"HTTP {resp.status_code}")
                self._sleep(self._backoff(attempt, resp), f"HTTP {resp.status_code} on {url}")
                continue

            if resp.status_code == 404:
                self._record_page(
                    entity, shard, page_index, req_hash, "rest",
                    status="not_found", http_status=404, error=None,
                    response_path=None, response_hash=None, cost=None,
                    elapsed=time.monotonic() - started, attempts=attempt + 1,
                    variables={"url": url, "params": params},
                )
                self.flush_ledger(force=False)
                return None

            resp.raise_for_status()
            payload = resp.json()
            stored, digest = self._store_response(entity, req_hash, payload)
            self._record_page(
                entity, shard, page_index, req_hash, "rest",
                status="ok", http_status=resp.status_code, error=None,
                response_path=str(stored.relative_to(self.settings.project_root)),
                response_hash=digest, cost=1,
                elapsed=time.monotonic() - started, attempts=attempt + 1,
                variables={"url": url, "params": params},
            )
            self.flush_ledger(force=False)
            return payload

        raise RateLimited(f"GET {url} failed after {MAX_ATTEMPTS} attempts: {last_error}")

    # -- ledger ----------------------------------------------------------

    def _record_locked(self, row: dict[str, Any]) -> None:
        with self._lock:
            self.ledger.record(row)

    def _record_page(
        self,
        entity: str,
        shard: str,
        page_index: int,
        req_hash: str,
        query_name: str,
        *,
        status: str,
        http_status: int | None,
        error: str | None,
        response_path: str | None,
        response_hash: str | None,
        cost: int | None,
        elapsed: float,
        attempts: int,
        variables: dict[str, Any] | None,
        terminal_reason: str | None = None,
    ) -> None:
        cursor = None
        if variables:
            cursor = variables.get("cursor") or variables.get("after")
        with self._lock:
            self._pending_flush += 1
            remaining = (
                self.state.graphql_remaining if entity != "rest" else self.state.rest_remaining
            )
        self._record_locked(
            {
                "request_hash": req_hash,
                "entity": entity,
                "shard": shard,
                "page_index": page_index,
                "query_name": query_name,
                "cursor": cursor,
                "status": status,
                "http_status": http_status,
                "error": error,
                "response_path": response_path,
                "response_sha256": response_hash,
                "rate_limit_cost": cost,
                "rate_limit_remaining": remaining,
                "elapsed_seconds": round(elapsed, 3),
                "attempt_count": attempts,
                "terminal_reason": terminal_reason,
                "extractor_version": EXTRACTOR_VERSION,
                "extracted_at": iso(dt.datetime.now(UTC)),
            }
        )

    # -- pagination helper ------------------------------------------------

    def paginate(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        entity: str,
        shard: str,
        extract: Callable[[dict[str, Any]], dict[str, Any] | None],
        query_name: str,
        max_pages: int = 500,
        start_cursor: str | None = None,
    ) -> Iterator[tuple[int, list[dict[str, Any]], str | None]]:
        """Yield ``(page_index, nodes, cursor_after_page)`` until exhausted.

        ``extract`` maps a GraphQL payload to the connection dict
        (``{nodes, pageInfo}``).  Returning ``None`` means "no connection
        here" and stops iteration -- used when a node was deleted upstream.
        """
        cursor = start_cursor
        for page_index in range(max_pages):
            payload = self.graphql(
                query,
                {**variables, "cursor": cursor},
                entity=entity,
                shard=shard,
                page_index=page_index,
                query_name=query_name,
            )
            connection = extract(payload)
            if not connection:
                return
            nodes = [n for n in (connection.get("nodes") or []) if n is not None]
            info = connection.get("pageInfo") or {}
            cursor = info.get("endCursor")
            yield page_index, nodes, cursor
            if not info.get("hasNextPage"):
                return
        log.warning(
            "pagination cap %d reached for %s/%s -- data may be truncated",
            max_pages, entity, shard,
        )


def sha_short(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]
