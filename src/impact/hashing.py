"""Stable identifiers and content hashes.

Two rules the rest of the pipeline depends on:

1. Every ID is *repository-qualified* and derived only from immutable upstream
   facts, so two runs against the same source SHA produce byte-identical IDs.
2. Table hashes are computed from canonicalised row content, never from Parquet
   file bytes -- Parquet embeds a writer string and timestamps that would make
   an otherwise-identical rerun look different.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

QUALIFIER = "github.com/PostHog/posthog"


def set_qualifier(qualifier: str) -> None:
    """Point ID generation at a different repository (used by tests)."""
    global QUALIFIER
    QUALIFIER = qualifier


# --------------------------------------------------------------------------
# Entity IDs.  Human-readable on purpose: an ID you can paste into a URL bar
# is an ID an auditor can check.
# --------------------------------------------------------------------------


def pr_id(number: int, qualifier: str | None = None) -> str:
    return f"{qualifier or QUALIFIER}#pr/{int(number)}"


def issue_id(number: int, qualifier: str | None = None) -> str:
    return f"{qualifier or QUALIFIER}#issue/{int(number)}"


def commit_id(sha: str, qualifier: str | None = None) -> str:
    return f"{qualifier or QUALIFIER}#commit/{sha.strip().lower()}"


def actor_id(login: str | None, fallback: str | None = None) -> str:
    """Actor identity.

    A GitHub login is globally unique and stable, so it is preferred.  When a
    Git author has no linked GitHub account we fall back to a hash of the
    normalised email -- never to a display name, which is not unique.
    """
    if login:
        return f"github/user/{login.strip().lower()}"
    if fallback:
        digest = hashlib.sha256(fallback.strip().lower().encode()).hexdigest()[:16]
        return f"git/email/{digest}"
    return "unknown/actor"


def file_id(pr_number: int, path: str, qualifier: str | None = None) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return f"{qualifier or QUALIFIER}#pr/{int(pr_number)}/file/{digest}"


def component_id(name: str) -> str:
    return f"component/{name}"


def edge_id(kind: str, src: str, dst: str) -> str:
    payload = f"{kind}|{src}|{dst}".encode("utf-8")
    return f"edge/{kind}/{hashlib.sha256(payload).hexdigest()[:20]}"


def request_hash(method: str, url: str, body: Any) -> str:
    """Deterministic cache key for one API request."""
    payload = canonical_json({"method": method.upper(), "url": url, "body": body})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Content hashing
# --------------------------------------------------------------------------


def _canonicalise(value: Any) -> Any:
    """Make a value JSON-safe *and* stable across runs.

    Floats are rounded because a NaN or a 1e-17 drift in a ratio would
    otherwise flip a table hash without any real data change.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "__nan__"
        if math.isinf(value):
            return "__inf__" if value > 0 else "__-inf__"
        return round(value, 9)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        return {str(k): _canonicalise(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonicalise(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonicalise(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def content_hash(
    rows: Iterable[Mapping[str, Any]],
    *,
    sort_keys: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
) -> str:
    """Order-independent, writer-independent hash of a table's content.

    ``exclude`` drops operational columns (wall-clock timestamps, local paths)
    that legitimately differ between two otherwise-identical runs.  The
    reproducibility gate relies on this: it is the only honest way to assert
    "same input SHA produced the same data".
    """
    drop = set(exclude)
    materialised = [
        {k: v for k, v in row.items() if k not in drop} for row in rows
    ]
    if sort_keys:
        materialised.sort(key=lambda r: canonical_json([r.get(k) for k in sort_keys]))
    else:
        materialised.sort(key=canonical_json)
    digest = hashlib.sha256()
    for row in materialised:
        digest.update(canonical_json(row).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


# Columns that are operational (wall clock / machine local) and therefore
# excluded from reproducibility hashes by default.
OPERATIONAL_COLUMNS: tuple[str, ...] = (
    "computed_at",
    "extracted_at",
    "retrieved_at",
    "run_id",
    "run_started_at",
    "run_finished_at",
    "duration_seconds",
    "local_path",
    "response_path",
    "rate_limit_remaining",
    "rate_limit_cost",
    "attempt_count",
)
