"""Reference extraction: issues, PRs, URLs, feature flags, episode language.

Everything here is *deterministic text evidence*.  A phrase match produces a
candidate edge with a strength band and the matched span, never a conclusion.
Phase 2 decides what a candidate means; Phase 1's job is to make sure the
candidate is findable and auditable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

# --- issue / PR references -------------------------------------------------

# Bare "#123". Excludes "#1" style anchors inside words and markdown headers.
HASH_REF_RE = re.compile(r"(?<![\w/#])#(?P<number>\d{2,7})\b")
GH_REF_RE = re.compile(r"\bGH-(?P<number>\d{2,7})\b", re.IGNORECASE)
URL_REF_RE = re.compile(
    r"https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/"
    r"(?P<kind>issues|pull)/(?P<number>\d+)",
    re.IGNORECASE,
)
# GitHub's own closing keywords, which actually create a link on merge.
CLOSING_RE = re.compile(
    r"\b(?P<verb>close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+"
    r"(?:https?://github\.com/[\w.-]+/[\w.-]+/issues/|#|GH-)(?P<number>\d{1,7})",
    re.IGNORECASE,
)

# --- external corroboration ------------------------------------------------

POSTHOG_URL_RE = re.compile(
    r"https?://(?:www\.)?posthog\.com/(?P<path>[\w./#\-?=&%]+)", re.IGNORECASE
)
GENERIC_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']{6,300}")

DOC_URL_KINDS = (
    ("changelog", re.compile(r"/changelog", re.IGNORECASE)),
    ("docs", re.compile(r"/docs/", re.IGNORECASE)),
    ("handbook", re.compile(r"/handbook", re.IGNORECASE)),
    ("tutorial", re.compile(r"/tutorials?/", re.IGNORECASE)),
    ("blog", re.compile(r"/blog/", re.IGNORECASE)),
    ("roadmap", re.compile(r"/roadmap", re.IGNORECASE)),
)

# --- feature flags ---------------------------------------------------------

# FEATURE_FLAGS.SOME_KEY  (frontend constant lookup)
FLAG_CONST_RE = re.compile(r"\bFEATURE_FLAGS\.(?P<key>[A-Z0-9_]{3,60})\b")
# posthog.isFeatureEnabled('key') / useFeatureFlag('key') / featureFlags['key']
FLAG_CALL_RE = re.compile(
    r"(?:isFeatureEnabled|useFeatureFlag|getFeatureFlag|feature_enabled|"
    r"featureFlags\s*\[)\s*\(?\s*['\"](?P<key>[a-z0-9][\w-]{2,60})['\"]",
    re.IGNORECASE,
)
# The registry line itself: KEY: 'kebab-key', // owner: #team-x
FLAG_REGISTRY_RE = re.compile(
    r"^\s*(?P<const>[A-Z0-9_]{3,60})\s*:\s*['\"](?P<key>[^'\"]{2,80})['\"]\s*,?"
    r"(?:\s*//\s*owner:\s*(?P<owner>[#@][\w./-]+))?",
    re.MULTILINE,
)

# --- episode language ------------------------------------------------------

EDGE_PHRASE_PATTERNS: dict[str, re.Pattern[str]] = {
    "follow_up": re.compile(r"\bfollow[\s-]?up\b", re.IGNORECASE),
    "part_of": re.compile(r"\b(part\s+(?:of|\d+)|first\s+of|series)\b", re.IGNORECASE),
    "stacked_on": re.compile(
        r"\b(stacked\s+(?:on|upon)|builds?\s+on|depends?\s+on|blocked\s+by|requires)\b",
        re.IGNORECASE,
    ),
    "reverts": re.compile(r"\brevert(?:s|ing|ed)?\b", re.IGNORECASE),
    "reapplies": re.compile(r"\b(re-?land|re-?apply|redo\s+of)\b", re.IGNORECASE),
    "supersedes": re.compile(r"\b(supersede[sd]?|replaces?|obsoletes?)\b", re.IGNORECASE),
}


@dataclass
class Reference:
    kind: str            # issue_or_pr | url | feature_flag | edge_phrase
    value: str
    subtype: str | None  # closing | mention | changelog | docs | ...
    strength: str        # strong | medium | weak
    source_field: str    # title | body | comment | commit_message | diff
    evidence: str        # the matched span, trimmed

    def as_dict(self) -> dict[str, Any]:
        return {
            "reference_kind": self.kind,
            "reference_value": self.value,
            "reference_subtype": self.subtype,
            "strength": self.strength,
            "source_field": self.source_field,
            "evidence": self.evidence[:280],
        }


def _span(text: str, start: int, end: int, pad: int = 60) -> str:
    return text[max(0, start - pad) : min(len(text), end + pad)].replace("\n", " ").strip()


def extract_artifact_references(
    text: str | None, *, source_field: str, self_number: int | None = None
) -> list[Reference]:
    """Find issue/PR references and label the ones GitHub would act on."""
    if not text:
        return []
    out: list[Reference] = []
    closing_numbers: set[int] = set()

    for match in CLOSING_RE.finditer(text):
        number = int(match.group("number"))
        closing_numbers.add(number)
        if number == self_number:
            continue
        out.append(
            Reference("issue_or_pr", str(number), "closing", "strong",
                      source_field, _span(text, *match.span()))
        )

    for regex, subtype in ((HASH_REF_RE, "mention"), (GH_REF_RE, "mention")):
        for match in regex.finditer(text):
            number = int(match.group("number"))
            if number == self_number or number in closing_numbers:
                continue
            out.append(
                Reference("issue_or_pr", str(number), subtype, "medium",
                          source_field, _span(text, *match.span()))
            )

    for match in URL_REF_RE.finditer(text):
        number = int(match.group("number"))
        if number == self_number:
            continue
        # A URL into another repository is context, not an in-repo edge.
        same_repo = match.group("repo").lower() == "posthog"
        out.append(
            Reference(
                "issue_or_pr" if same_repo else "external_artifact",
                str(number) if same_repo else match.group(0),
                "url_pull" if match.group("kind") == "pull" else "url_issue",
                "medium" if same_repo else "weak",
                source_field,
                _span(text, *match.span()),
            )
        )

    # Deduplicate on (value, subtype) keeping the strongest.
    rank = {"strong": 0, "medium": 1, "weak": 2}
    best: dict[tuple[str, str], Reference] = {}
    for ref in out:
        key = (ref.kind, ref.value)
        current = best.get(key)
        if current is None or rank[ref.strength] < rank[current.strength]:
            best[key] = ref
    return sorted(best.values(), key=lambda r: (r.kind, int(r.value) if r.value.isdigit() else 0))


def extract_urls(text: str | None, *, source_field: str) -> list[Reference]:
    if not text:
        return []
    out: list[Reference] = []
    seen: set[str] = set()
    for match in GENERIC_URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:)")
        if url in seen or "github.com" in url.lower():
            continue
        seen.add(url)
        subtype = "external"
        for label, pattern in DOC_URL_KINDS:
            if pattern.search(url):
                subtype = label
                break
        strength = "strong" if subtype in {"changelog", "docs", "handbook"} else "weak"
        out.append(Reference("url", url, subtype, strength, source_field,
                             _span(text, *match.span())))
    return out


def extract_feature_flags(
    text: str | None, *, source_field: str, registry: dict[str, str] | None = None
) -> list[Reference]:
    """Extract feature-flag keys.

    Three signal strengths: an explicit ``FEATURE_FLAGS.X`` constant resolved
    through the registry is strong; a literal string passed to a known flag API
    is medium; an unresolved constant is weak.
    """
    if not text:
        return []
    registry = registry or {}
    out: list[Reference] = []
    seen: set[str] = set()

    for match in FLAG_CONST_RE.finditer(text):
        const = match.group("key")
        key = registry.get(const)
        value = key or const
        if value in seen:
            continue
        seen.add(value)
        out.append(
            Reference("feature_flag", value,
                      "constant_resolved" if key else "constant_unresolved",
                      "strong" if key else "weak",
                      source_field, _span(text, *match.span()))
        )

    for match in FLAG_CALL_RE.finditer(text):
        key = match.group("key")
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Reference("feature_flag", key, "api_literal", "medium",
                      source_field, _span(text, *match.span()))
        )
    return out


def parse_flag_registry(source: str | None) -> dict[str, str]:
    """Parse ``FEATURE_FLAGS = { CONST: 'key', ... }`` into CONST -> key."""
    if not source:
        return {}
    start = source.find("export const FEATURE_FLAGS")
    if start == -1:
        return {}
    body = source[start : start + 200_000]
    out: dict[str, str] = {}
    for match in FLAG_REGISTRY_RE.finditer(body):
        out[match.group("const")] = match.group("key")
    return out


def parse_flag_registry_owners(source: str | None) -> dict[str, str]:
    """Flag key -> owning team/person, from the ``// owner:`` annotation."""
    if not source:
        return {}
    start = source.find("export const FEATURE_FLAGS")
    if start == -1:
        return {}
    body = source[start : start + 200_000]
    out: dict[str, str] = {}
    for match in FLAG_REGISTRY_RE.finditer(body):
        if match.group("owner"):
            out[match.group("key")] = match.group("owner")
    return out


def extract_edge_phrases(text: str | None, *, source_field: str) -> list[Reference]:
    if not text:
        return []
    out: list[Reference] = []
    for kind, pattern in EDGE_PHRASE_PATTERNS.items():
        match = pattern.search(text)
        if match:
            out.append(
                Reference("edge_phrase", kind, None, "medium", source_field,
                          _span(text, *match.span()))
            )
    return out


def extract_all(
    *,
    title: str | None,
    body: str | None,
    self_number: int | None,
    flag_registry: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Everything extractable from a PR's own text."""
    refs: list[Reference] = []
    for field_name, text in (("title", title), ("body", body)):
        refs += extract_artifact_references(
            text, source_field=field_name, self_number=self_number
        )
        refs += extract_urls(text, source_field=field_name)
        refs += extract_feature_flags(
            text, source_field=field_name, registry=flag_registry
        )
        refs += extract_edge_phrases(text, source_field=field_name)
    return [r.as_dict() for r in refs]


def flags_from_diff(
    patch_text: str | None, registry: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Feature-flag keys appearing on added/removed diff lines.

    Restricting to +/- lines is what distinguishes "this PR touched the flag"
    from "the flag happened to be in the surrounding context".
    """
    if not patch_text:
        return []
    added, removed = [], []
    for line in patch_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])

    out: list[dict[str, Any]] = []
    for label, lines in (("added", added), ("removed", removed)):
        text = "\n".join(lines)
        for ref in extract_feature_flags(
            text, source_field=f"diff_{label}", registry=registry
        ):
            payload = ref.as_dict()
            payload["diff_side"] = label
            out.append(payload)
        for match in FLAG_REGISTRY_RE.finditer(text):
            out.append(
                {
                    "reference_kind": "feature_flag",
                    "reference_value": match.group("key"),
                    "reference_subtype": "registry_line",
                    "strength": "strong",
                    "source_field": f"diff_{label}",
                    "evidence": match.group(0)[:280],
                    "diff_side": label,
                }
            )
    return out


def collect_numbers(refs: Iterable[dict[str, Any]]) -> set[int]:
    out: set[int] = set()
    for ref in refs:
        if ref.get("reference_kind") == "issue_or_pr":
            value = str(ref.get("reference_value", ""))
            if value.isdigit():
                out.add(int(value))
    return out
