"""Stage: public corroboration for URLs explicitly referenced from PRs/issues.

The spec is precise about scope: ingest *references*, not the entire internet.
So this stage fetches only URLs that an in-window PR or issue actually linked
to, and only those classified as documentation, changelog, handbook, tutorial
or roadmap pages -- the ones that corroborate what a change shipped.

Politeness is not optional here. Every host's ``robots.txt`` is fetched once and
honoured, requests are rate-limited per host, redirects are recorded, and the
whole stage is capped. A URL that is disallowed, unreachable or over the cap is
stored with an explicit ``extraction_status`` rather than dropped -- "we chose
not to fetch this" and "this does not exist" are different facts.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import re
import time
import urllib.robotparser
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

from ..config import Settings, iso
from ..hashing import sha256_text
from ..store import RawStore, read_table, write_table
from ..versions import EXTRACTOR_VERSION
from .runs import ExtractionRun

log = logging.getLogger("impact.web")

UTC = dt.timezone.utc

USER_AGENT = (
    f"posthog-impact-phase1/{EXTRACTOR_VERSION} "
    "(engineering-analysis; read-only; contact via repository)"
)

# Only reference subtypes that corroborate a shipped change.
CORROBORATING_SUBTYPES = {"docs", "changelog", "handbook", "tutorial", "roadmap"}

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
DESCRIPTION_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
    re.IGNORECASE | re.DOTALL,
)

PER_HOST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 20
MAX_BYTES = 2_000_000


class RobotsCache:
    """One robots.txt fetch per host, honoured for every later request."""

    def __init__(self, session: requests.Session) -> None:
        self.session = session
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allows(self, url: str) -> tuple[bool, str]:
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        if host not in self._cache:
            parser = urllib.robotparser.RobotFileParser()
            robots_url = f"{host}/robots.txt"
            try:
                response = self.session.get(robots_url, timeout=REQUEST_TIMEOUT)
                if response.status_code >= 400:
                    # No robots.txt is a permissive answer, but record which.
                    self._cache[host] = None
                else:
                    parser.parse(response.text.splitlines())
                    self._cache[host] = parser
            except requests.RequestException as exc:
                log.warning("robots.txt unreachable for %s: %s", host, exc)
                # Unreachable robots.txt is treated as DISALLOW: we cannot
                # confirm permission, so we do not fetch.
                self._cache[host] = "unreachable"  # type: ignore[assignment]

        entry = self._cache[host]
        if entry is None:
            return True, "no robots.txt published"
        if entry == "unreachable":
            return False, "robots.txt unreachable; not fetching without permission"
        allowed = entry.can_fetch(USER_AGENT, url)  # type: ignore[union-attr]
        return allowed, "allowed by robots.txt" if allowed else "disallowed by robots.txt"


def _extract(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    return " ".join(html.unescape(match.group(1)).split())[:400] or None


def candidate_urls(
    references: Iterable[dict[str, Any]], *, allowed_hosts: set[str] | None = None
) -> list[dict[str, Any]]:
    """Distinct corroborating URLs, each with the artifacts that referenced it."""
    by_url: dict[str, dict[str, Any]] = {}
    for ref in references:
        if ref.get("reference_kind") != "url":
            continue
        if str(ref.get("reference_subtype")) not in CORROBORATING_SUBTYPES:
            continue
        url = str(ref.get("reference_value") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        host = urlparse(url).netloc.lower()
        if allowed_hosts and not any(host == h or host.endswith("." + h) for h in allowed_hosts):
            continue
        entry = by_url.setdefault(
            url,
            {
                "url": url, "host": host,
                "reference_subtype": ref.get("reference_subtype"),
                "referenced_by": [], "reference_count": 0,
            },
        )
        entry["reference_count"] += 1
        origin = f"{ref.get('source_kind')}/{ref.get('source_number')}"
        if origin not in entry["referenced_by"]:
            entry["referenced_by"].append(origin)
    return sorted(by_url.values(), key=lambda r: r["url"])


def run(
    settings: Settings,
    *,
    max_urls: int = 400,
    allowed_hosts: set[str] | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    run_rec = ExtractionRun.start(settings, "ingest_web")
    raw = RawStore(settings.path("raw", "web"))

    references = read_table(settings.path("normalized", "references.parquet"))
    if not references:
        run_rec.note("no references table; run `make normalize` first")
        run_rec.finish("skipped")
        run_rec.append_to(settings.path("raw", "extraction_runs.json"))
        return run_rec.as_row()

    candidates = candidate_urls(references, allowed_hosts=allowed_hosts)
    capped = candidates[:max_urls]
    if len(candidates) > max_urls:
        run_rec.note(
            f"URL cap applied: {len(candidates)} corroborating URLs found, "
            f"{max_urls} fetched; the remainder are recorded as 'skipped_cap'"
        )
    log.info("web corroboration: %d candidate URLs (%d after cap)",
             len(candidates), len(capped))

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    robots = RobotsCache(session)

    rows: list[dict[str, Any]] = []
    last_request_at: dict[str, float] = {}
    cached = raw.read("pages", "index")
    cached_by_url = {r["url"]: r for r in cached if r.get("url")}

    for entry in candidates:
        url = entry["url"]
        base = {
            "url": url,
            "host": entry["host"],
            "reference_subtype": entry["reference_subtype"],
            "referenced_by": entry["referenced_by"],
            "reference_count": entry["reference_count"],
            "extractor_version": EXTRACTOR_VERSION,
        }

        if entry not in capped:
            rows.append({**base, "extraction_status": "skipped_cap", "title": None,
                         "content_sha256": None, "http_status": None,
                         "final_url": None, "content_bytes": None,
                         "description": None, "retrieved_at": None,
                         "error": f"beyond the {max_urls}-URL cap"})
            continue

        previous = cached_by_url.get(url)
        if previous and previous.get("extraction_status") == "ok":
            rows.append(previous)
            continue

        if offline:
            rows.append({**base, "extraction_status": "skipped_offline", "title": None,
                         "content_sha256": None, "http_status": None,
                         "final_url": None, "content_bytes": None,
                         "description": None, "retrieved_at": None,
                         "error": "offline mode"})
            continue

        allowed, reason = robots.allows(url)
        if not allowed:
            rows.append({**base, "extraction_status": "blocked_by_robots", "title": None,
                         "content_sha256": None, "http_status": None,
                         "final_url": None, "content_bytes": None,
                         "description": None,
                         "retrieved_at": iso(dt.datetime.now(UTC)), "error": reason})
            run_rec.count("blocked_by_robots")
            continue

        # Per-host politeness delay.
        elapsed = time.monotonic() - last_request_at.get(entry["host"], 0.0)
        if elapsed < PER_HOST_DELAY_SECONDS:
            time.sleep(PER_HOST_DELAY_SECONDS - elapsed)
        last_request_at[entry["host"]] = time.monotonic()

        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            body = response.text[:MAX_BYTES]
            rows.append({
                **base,
                "extraction_status": "ok" if response.status_code < 400 else "http_error",
                "http_status": response.status_code,
                "final_url": response.url,
                "title": _extract(TITLE_RE, body),
                "description": _extract(DESCRIPTION_RE, body),
                "content_sha256": sha256_text(body),
                "content_bytes": len(response.content),
                "retrieved_at": iso(dt.datetime.now(UTC)),
                "error": None if response.status_code < 400 else f"HTTP {response.status_code}",
            })
            run_rec.count("fetched" if response.status_code < 400 else "http_error")
        except requests.RequestException as exc:
            rows.append({**base, "extraction_status": "fetch_failed", "title": None,
                         "content_sha256": None, "http_status": None,
                         "final_url": None, "content_bytes": None, "description": None,
                         "retrieved_at": iso(dt.datetime.now(UTC)),
                         "error": str(exc)[:300]})
            run_rec.count("fetch_failed")

    raw.write("pages", "index", rows)
    meta = write_table(
        settings.path("normalized", "web_artifacts.parquet"), rows, sort_keys=["url"]
    )

    statuses: dict[str, int] = {}
    for row in rows:
        key = str(row.get("extraction_status"))
        statuses[key] = statuses.get(key, 0) + 1
    run_rec.set("candidate_urls", len(candidates))
    run_rec.set("rows", meta["row_count"])
    run_rec.set("status_distribution", dict(sorted(statuses.items())))
    run_rec.finish("ok")
    run_rec.append_to(settings.path("raw", "extraction_runs.json"))
    log.info("web artifacts: %s", statuses)
    return run_rec.as_row()
