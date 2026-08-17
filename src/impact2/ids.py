"""Stable Phase 2 identifiers and deterministic hashing.

Two properties matter and both are load-bearing for the audit story:

1. **An ID an auditor can paste into a URL bar.**  Episode, participant and
   claim IDs are human-readable and repository-qualified, so a published claim
   can be traced without a lookup table.
2. **The same inputs produce the same IDs.**  Episode IDs are derived from the
   *content* of the cluster (its sorted member PR numbers), not from iteration
   order, so a rerun that produces the same cluster produces the same ID even
   if the clustering visited nodes in a different order.

Everything hashed here goes through ``canonical_json`` so floats, mappings and
timestamps serialise identically across runs and platforms.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

QUALIFIER = "github.com/PostHog/posthog"


def set_qualifier(qualifier: str) -> None:
    global QUALIFIER
    QUALIFIER = qualifier


# --------------------------------------------------------------------------
# canonicalisation (mirrors impact.hashing so the two phases agree)
# --------------------------------------------------------------------------


def _canonicalise(value: Any) -> Any:
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
    if isinstance(value, (set, frozenset)):
        return [_canonicalise(v) for v in sorted(value, key=str)]
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


def sha256_file(path: Any, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def content_hash(
    rows: Iterable[Mapping[str, Any]], *, exclude: Sequence[str] = ()
) -> str:
    """Order- and writer-independent hash of a table's content."""
    drop = set(exclude)
    materialised = [{k: v for k, v in row.items() if k not in drop} for row in rows]
    materialised.sort(key=canonical_json)
    digest = hashlib.sha256()
    for row in materialised:
        digest.update(canonical_json(row).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


# Columns excluded from a content hash because they record *when and how* a row
# was produced, not what it says. Two runs over identical source data differ in
# every one of them.
#
# This tuple MUST equal ``impact.hashing.OPERATIONAL_COLUMNS`` exactly: Phase 2
# re-computes Phase 1's published hashes to verify its inputs, so a shorter list
# here makes every provenance table look corrupted. It is mirrored rather than
# imported to keep Phase 2 dependent only on the Parquet contract, and
# `tests/phase2/test_inputs.py` asserts the two agree so drift cannot go unnoticed.
OPERATIONAL_COLUMNS: tuple[str, ...] = (
    "computed_at", "extracted_at", "retrieved_at", "run_id", "run_started_at",
    "run_finished_at", "duration_seconds", "local_path", "response_path",
    "rate_limit_remaining", "rate_limit_cost", "attempt_count",
)


# --------------------------------------------------------------------------
# entity IDs
# --------------------------------------------------------------------------


def episode_id(pr_numbers: Iterable[int], qualifier: str | None = None) -> str:
    """Content-addressed episode ID.

    Derived from the sorted member set so two runs that find the same cluster
    agree on its identity regardless of traversal order. The lowest PR number
    is kept in the readable part because it is the one an auditor will look up
    first; the digest disambiguates clusters that share a root.
    """
    members = sorted({int(n) for n in pr_numbers})
    if not members:
        raise ValueError("an episode must contain at least one PR")
    digest = hashlib.sha256(
        ",".join(str(n) for n in members).encode("utf-8")
    ).hexdigest()[:12]
    return f"{qualifier or QUALIFIER}#episode/{members[0]}-{digest}"


def artifact_id(kind: str, key: Any, qualifier: str | None = None) -> str:
    """ID for an evidence artifact: pr, issue, commit, review_comment, file, flag."""
    return f"{qualifier or QUALIFIER}#{kind}/{key}"


def pr_artifact(number: int, qualifier: str | None = None) -> str:
    return artifact_id("pr", int(number), qualifier)


def issue_artifact(number: int, qualifier: str | None = None) -> str:
    return artifact_id("issue", int(number), qualifier)


def comment_artifact(comment_id: str, qualifier: str | None = None) -> str:
    return artifact_id("review_comment", comment_id, qualifier)


def commit_artifact(sha: str, qualifier: str | None = None) -> str:
    return artifact_id("commit", str(sha).strip().lower(), qualifier)


def file_artifact(path: str, qualifier: str | None = None) -> str:
    return artifact_id("file", path, qualifier)


def flag_artifact(key: str, qualifier: str | None = None) -> str:
    return artifact_id("feature_flag", key, qualifier)


def participant_id(episode: str, actor_cluster: str) -> str:
    digest = hashlib.sha256(f"{episode}|{actor_cluster}".encode()).hexdigest()[:12]
    return f"participant/{digest}"


def dimension_id(episode: str, dimension: str) -> str:
    return f"{episode}/dimension/{dimension}"


def edge_uid(kind: str, source: str, target: str) -> str:
    payload = f"{kind}|{source}|{target}".encode("utf-8")
    return f"edge/{kind}/{hashlib.sha256(payload).hexdigest()[:20]}"


def propagation_edge_id(source: str, target: str, depth: int) -> str:
    return edge_uid(f"propagation_d{depth}", source, target)


def intervention_id(candidate_id: str) -> str:
    return f"intervention/{sha256_text(str(candidate_id))[:16]}"


def portfolio_id(actor_cluster: str) -> str:
    return f"portfolio/{sha256_text(actor_cluster)[:16]}"


def ranking_run_id(scenario: str, config_digest: str) -> str:
    return f"ranking/{scenario}/{config_digest[:12]}"


def claim_id(text: str, subject: str, evidence: Sequence[str]) -> str:
    """Content-addressed claim ID.

    A claim's identity is its text plus its subject plus the evidence it rests
    on. Change any of the three and it is a different claim — which is exactly
    what a reader who filed a correction against `claim_id` needs to be true.
    """
    payload = canonical_json({"t": text, "s": subject, "e": sorted(evidence)})
    return f"claim/{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def config_digest(config: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(config).encode()).hexdigest()


def url_for_pr(number: int, repo_url: str = "https://github.com/PostHog/posthog") -> str:
    return f"{repo_url}/pull/{int(number)}"


def url_for_issue(number: int, repo_url: str = "https://github.com/PostHog/posthog") -> str:
    return f"{repo_url}/issues/{int(number)}"


def url_for_commit(sha: str, repo_url: str = "https://github.com/PostHog/posthog") -> str:
    return f"{repo_url}/commit/{sha}"


def url_for_file(
    path: str, sha: str, repo_url: str = "https://github.com/PostHog/posthog"
) -> str:
    return f"{repo_url}/blob/{sha}/{path}"
