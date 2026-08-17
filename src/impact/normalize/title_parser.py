"""Conventional-commit title parsing.

PostHog uses conventional titles heavily (measured on a 3,000-commit sample:
~99.9% carry a recognisable prefix).  That makes the convention useful and
makes over-trusting it dangerous, so this parser:

* reports a **confidence** rather than a boolean, and always keeps the raw
  title;
* distinguishes *strict* conformance (lowercase type from the known set,
  well-formed scope) from *loose* matches (uppercase, unknown type, missing
  colon) instead of silently normalising them;
* strips GitHub's squash suffix ``(#12345)`` before parsing but records it, and
* never asserts the prefix is *truthful* -- corroboration against paths and
  diff content happens in :mod:`impact.features.change_shape`.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from ..versions import feature_version

# The Conventional Commits type set, plus types actually observed in this repo.
KNOWN_TYPES = {
    "feat", "fix", "chore", "docs", "refactor", "test", "perf", "build", "ci",
    "style", "revert",
}
# Frequently used near-misses that should parse but not count as strict.
ALIAS_TYPES = {
    "feature": "feat",
    "bugfix": "fix",
    "hotfix": "fix",
    "tests": "test",
    "doc": "docs",
    "chores": "chore",
    "deps": "build",
    "dep": "build",
    "release": "chore",
}

SQUASH_SUFFIX_RE = re.compile(r"\s*\(#(?P<number>\d{1,7})\)\s*$")
# type(scope)!: subject   |   type!: subject   |   type: subject
CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[A-Za-z][A-Za-z0-9_-]{1,20})"
    r"(?:\((?P<scope>[^()]{0,80})\))?"
    r"(?P<breaking>!)?"
    r":\s*(?P<subject>.*)$"
)
# "WIP feat: x" / "[fix] x" style leaders that must not be mistaken for a type.
BRACKET_PREFIX_RE = re.compile(r"^\s*\[(?P<tag>[^\]]{1,30})\]\s*(?P<rest>.*)$")
BREAKING_BODY_RE = re.compile(r"^BREAKING[ -]CHANGE\s*:", re.MULTILINE)

# Merge-queue and automation titles that are not human PR titles at all.
NON_PR_TITLE_PATTERNS = (
    (re.compile(r"^trunk-merge/pr-\d+/"), "merge_queue_artifact"),
    (re.compile(r"^(Bump|bump) .+ from .+ to .+$"), "dependency_bump"),
    (re.compile(r"^chore\(deps(-dev)?\):", re.IGNORECASE), "dependency_bump"),
    (re.compile(r"^Revert \"", re.IGNORECASE), "revert"),
    (re.compile(r"^\[?auto(mated)?\]?[: ]", re.IGNORECASE), "automation"),
)


@dataclass
class ParsedTitle:
    raw_title: str
    prefix: str | None
    prefix_normalized: str | None
    scope: str | None
    breaking: bool
    subject: str | None
    squash_pr_number: int | None
    confidence: float
    parser_status: str
    parser_notes: list[str]
    title_class: str | None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["title_parse_version"] = feature_version("title_parse")
        return payload


def parse_title(title: str | None, body: str | None = None) -> ParsedTitle:
    raw = title or ""
    notes: list[str] = []

    title_class = None
    for pattern, label in NON_PR_TITLE_PATTERNS:
        if pattern.search(raw):
            title_class = label
            notes.append(f"matched non-standard title class: {label}")
            break

    working = raw.strip()
    squash_number = None
    squash = SQUASH_SUFFIX_RE.search(working)
    if squash:
        squash_number = int(squash.group("number"))
        working = SQUASH_SUFFIX_RE.sub("", working).strip()

    # A leading [tag] is a real convention here, but it is not a type.
    bracket = BRACKET_PREFIX_RE.match(working)
    if bracket:
        notes.append(f"leading bracket tag: {bracket.group('tag')}")
        working = bracket.group("rest").strip()

    match = CONVENTIONAL_RE.match(working)
    if not match:
        # A merge-queue title has no colon and so lands here, but "this is not
        # a human-authored title" is a stronger and more useful statement than
        # "this did not match the convention".
        return ParsedTitle(
            raw_title=raw, prefix=None, prefix_normalized=None, scope=None,
            breaking=bool(body and BREAKING_BODY_RE.search(body)),
            subject=working or None, squash_pr_number=squash_number,
            confidence=0.0,
            parser_status=(
                "not_a_human_title"
                if title_class == "merge_queue_artifact"
                else "not_conventional"
            ),
            parser_notes=notes, title_class=title_class,
        )

    prefix_raw = match.group("type")
    prefix_lower = prefix_raw.lower()
    scope = (match.group("scope") or "").strip() or None
    subject = (match.group("subject") or "").strip() or None
    breaking = bool(match.group("breaking"))
    if body and BREAKING_BODY_RE.search(body):
        breaking = True
        notes.append("BREAKING CHANGE footer present in body")

    if prefix_lower in KNOWN_TYPES:
        normalized = prefix_lower
        status = "strict"
        confidence = 0.98
    elif prefix_lower in ALIAS_TYPES:
        normalized = ALIAS_TYPES[prefix_lower]
        status = "alias"
        confidence = 0.8
        notes.append(f"non-canonical type {prefix_raw!r} mapped to {normalized!r}")
    else:
        normalized = None
        status = "unknown_type"
        confidence = 0.35
        notes.append(f"type {prefix_raw!r} is not a known conventional type")

    if prefix_raw != prefix_lower:
        confidence -= 0.08
        status = "loose" if status == "strict" else status
        notes.append("type is not lowercase")
    if scope is not None and not re.fullmatch(r"[A-Za-z0-9 _./,+-]{1,80}", scope):
        confidence -= 0.1
        notes.append("scope contains unexpected characters")
    if subject is None:
        confidence -= 0.3
        notes.append("empty subject after the colon")
    if title_class == "merge_queue_artifact":
        confidence = 0.0
        status = "not_a_human_title"

    return ParsedTitle(
        raw_title=raw,
        prefix=prefix_raw,
        prefix_normalized=normalized,
        scope=scope,
        breaking=breaking,
        subject=subject,
        squash_pr_number=squash_number,
        confidence=round(max(0.0, min(1.0, confidence)), 3),
        parser_status=status,
        parser_notes=notes,
        title_class=title_class,
    )


def prefix_distribution(titles: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for title in titles:
        parsed = parse_title(title)
        key = parsed.prefix_normalized or (
            f"<{parsed.parser_status}>" if parsed.prefix is None else f"?{parsed.prefix.lower()}"
        )
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
