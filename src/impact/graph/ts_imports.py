"""TypeScript/JavaScript import extraction and alias resolution.

The spec rules out a whole-repository compiler build, so this is a lexical
parser: comments and strings are stripped first (so a URL in a comment cannot
masquerade as a specifier), then import/export/require/dynamic-import forms are
matched.  Every edge records ``resolution`` and every specifier that could not
be resolved is counted -- an approximate parser that reports its own coverage is
useful; one that silently drops edges is not.

Alias resolution reads the real ``tsconfig.json`` ``paths`` map at the analysed
commit.  That file is JSON-with-comments, which ``json.loads`` rejects, so a
string-aware comment stripper runs first.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable

RESOLVE_EXTENSIONS = (
    ".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".json", ".scss", ".css",
)
INDEX_FILES = tuple(f"index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx"))

IMPORT_RE = re.compile(
    r"""(?:
          \bimport\s+type\s+[^'"]*?\bfrom\s*['"](?P<type_from>[^'"]+)['"]
        | \bimport\s+[^'"();]*?\bfrom\s*['"](?P<from>[^'"]+)['"]
        | \bimport\s*['"](?P<bare>[^'"]+)['"]
        | \bexport\s+(?:type\s+)?(?:\*|\{[^}]*\})\s*(?:as\s+\w+\s*)?from\s*['"](?P<reexport>[^'"]+)['"]
        | \brequire\s*\(\s*['"](?P<require>[^'"]+)['"]\s*\)
        | \bimport\s*\(\s*['"](?P<dynamic>[^'"]+)['"]\s*\)
        | \bjest\.mock\s*\(\s*['"](?P<mock>[^'"]+)['"]
    )""",
    re.VERBOSE,
)
# import('...') built from a template literal or variable: unresolvable, but the
# fact that the file does dynamic importing is itself uncertainty evidence.
UNRESOLVABLE_DYNAMIC_RE = re.compile(r"\bimport\s*\(\s*[^'\")]")


@dataclass
class TsImport:
    specifier: str
    kind: str          # import | type_import | reexport | require | dynamic | mock
    is_type_only: bool


def strip_comments_and_strings(source: str) -> str:
    """Blank out comments while preserving offsets and string contents.

    String literals are kept (we need the specifier) but comment bodies are
    replaced with spaces so that ``// see import('x')`` cannot create an edge.
    """
    out: list[str] = []
    index, length = 0, len(source)
    while index < length:
        char = source[index]
        nxt = source[index + 1] if index + 1 < length else ""
        if char == "/" and nxt == "/":
            end = source.find("\n", index)
            end = length if end == -1 else end
            out.append(" " * (end - index))
            index = end
            continue
        if char == "/" and nxt == "*":
            end = source.find("*/", index + 2)
            end = length if end == -1 else end + 2
            out.append(re.sub(r"[^\n]", " ", source[index:end]))
            index = end
            continue
        if char in "'\"`":
            quote = char
            out.append(char)
            index += 1
            while index < length:
                if source[index] == "\\":
                    out.append(source[index : index + 2])
                    index += 2
                    continue
                out.append(source[index])
                if source[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def parse_ts_imports(source: str) -> tuple[list[TsImport], bool]:
    cleaned = strip_comments_and_strings(source)
    out: list[TsImport] = []
    for match in IMPORT_RE.finditer(cleaned):
        groups = match.groupdict()
        for key, kind, type_only in (
            ("type_from", "type_import", True),
            ("from", "import", False),
            ("bare", "import", False),
            ("reexport", "reexport", False),
            ("require", "require", False),
            ("dynamic", "dynamic", False),
            ("mock", "mock", False),
        ):
            if groups.get(key):
                out.append(TsImport(groups[key], kind, type_only))
                break
    return out, bool(UNRESOLVABLE_DYNAMIC_RE.search(cleaned))


# --------------------------------------------------------------------------
# tsconfig
# --------------------------------------------------------------------------


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas, respecting strings."""
    out: list[str] = []
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        nxt = text[index + 1] if index + 1 < length else ""
        if char == '"':
            out.append(char)
            index += 1
            while index < length:
                if text[index] == "\\":
                    out.append(text[index : index + 2])
                    index += 2
                    continue
                out.append(text[index])
                if text[index] == '"':
                    index += 1
                    break
                index += 1
            continue
        if char == "/" and nxt == "/":
            end = text.find("\n", index)
            index = length if end == -1 else end
            continue
        if char == "/" and nxt == "*":
            end = text.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        out.append(char)
        index += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def load_tsconfig_paths(source: str | None) -> tuple[dict[str, list[str]], str | None]:
    """Parse one tsconfig's ``compilerOptions.paths`` (targets left as written)."""
    if not source:
        return {}, "tsconfig.json not available at the analysed commit"
    try:
        parsed = json.loads(strip_jsonc(source))
    except json.JSONDecodeError as exc:
        return {}, f"tsconfig.json unparseable: {exc}"[:200]
    options = parsed.get("compilerOptions") or {}
    raw = options.get("paths") or {}
    return {str(k): [str(v) for v in vals] for k, vals in raw.items()}, None


def load_tsconfig_baseurl(source: str | None) -> str | None:
    if not source:
        return None
    try:
        parsed = json.loads(strip_jsonc(source))
    except json.JSONDecodeError:
        return None
    value = (parsed.get("compilerOptions") or {}).get("baseUrl")
    return str(value) if value else None


@dataclass
class TsConfigScope:
    """One tsconfig and the directory its alias targets are relative to."""

    directory: str                      # repo-relative, "" for the root config
    aliases: dict[str, list[str]]
    base_url: str | None = None


class TsResolver:
    """Resolve specifiers using the *nearest enclosing* tsconfig.

    PostHog is a multi-workspace monorepo: 110 ``tsconfig.json`` files, and the
    same alias means different things in different workspaces -- ``~/*`` is
    ``frontend/src/*`` at the root but ``nodejs/src/*`` inside ``nodejs/``.
    Resolving everything against the root config mis-resolves thousands of
    edges, so scopes are walked from the importing file outward, exactly as
    TypeScript does.
    """

    def __init__(self, file_set: set[str], scopes: Iterable[TsConfigScope]) -> None:
        self.files = file_set
        prepared: list[tuple[str, list[tuple[str, list[str]]], str | None]] = []
        for scope in scopes:
            ordered = sorted(
                scope.aliases.items(),
                key=lambda kv: (kv[0] == "*", -len(kv[0].rstrip("/*"))),
            )
            prepared.append((scope.directory, ordered, scope.base_url))
        # Deepest directory first so "nearest enclosing" is a first-hit scan.
        self.scopes = sorted(prepared, key=lambda s: -len(s[0]))

    def _exists(self, base: str) -> str | None:
        if base in self.files:
            return base
        for ext in RESOLVE_EXTENSIONS:
            candidate = base + ext
            if candidate in self.files:
                return candidate
        for index in INDEX_FILES:
            candidate = f"{base}/{index}"
            if candidate in self.files:
                return candidate
        return None

    @staticmethod
    def _join(directory: str, target: str) -> str:
        if target.startswith("./"):
            target = target[2:]
        return posixpath.normpath(posixpath.join(directory, target.lstrip("/")))

    def _in_scope(self, directory: str, from_path: str) -> bool:
        return not directory or from_path.startswith(directory + "/")

    def resolve(self, specifier: str, from_path: str) -> tuple[str | None, str]:
        if not specifier:
            return None, "unresolved"

        if specifier.startswith("."):
            # posixpath.normpath, not PurePosixPath: pathlib deliberately does
            # NOT collapse "..", so "a/b/../c" stays literal and every upward
            # relative import silently fails to resolve.
            base = posixpath.normpath(
                posixpath.join(posixpath.dirname(from_path), specifier)
            )
            hit = self._exists(base)
            return (hit, "relative") if hit else (None, "relative_missing")

        saw_node_modules = False
        for directory, aliases, base_url in self.scopes:
            if not self._in_scope(directory, from_path):
                continue
            for pattern, targets in aliases:
                if pattern.endswith("/*"):
                    prefix = pattern[:-1]
                    if not specifier.startswith(prefix):
                        continue
                    remainder = specifier[len(prefix) :]
                elif pattern == "*":
                    remainder = specifier
                else:
                    if specifier != pattern:
                        continue
                    remainder = ""

                for target in targets:
                    base = target[:-1] + remainder if target.endswith("*") else target
                    joined = self._join(directory, base)
                    if "node_modules" in joined:
                        saw_node_modules = True
                        continue
                    hit = self._exists(joined.rstrip("/"))
                    if hit:
                        return hit, "alias"

            if base_url:
                joined = self._join(directory, self._join(base_url, specifier))
                hit = self._exists(joined)
                if hit:
                    return hit, "baseurl"

        if saw_node_modules:
            return None, "external_alias"

        # A bare specifier that happens to be a repo path (rare but valid).
        hit = self._exists(specifier)
        if hit:
            return hit, "baseurl"
        if specifier.startswith("@") or "/" not in specifier:
            return None, "external"
        return None, "unresolved"


def build_scopes(
    tracked: Iterable[str], read_file: "Callable[[str], str | None]"
) -> tuple[list[TsConfigScope], list[str]]:
    """Discover every tsconfig in the repo and build its resolution scope."""
    scopes: list[TsConfigScope] = []
    errors: list[str] = []
    for path in sorted(tracked):
        if not path.endswith("tsconfig.json"):
            continue
        source = read_file(path)
        aliases, error = load_tsconfig_paths(source)
        if error:
            errors.append(f"{path}: {error}")
            continue
        base_url = load_tsconfig_baseurl(source)
        if not aliases and not base_url:
            continue
        directory = str(PurePosixPath(path).parent)
        scopes.append(
            TsConfigScope(
                directory="" if directory == "." else directory,
                aliases=aliases,
                base_url=base_url,
            )
        )
    return scopes, errors


def summarise(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    total = len(rows) or 1
    by_resolution: dict[str, int] = {}
    for row in rows:
        key = str(row.get("resolution", "unknown"))
        by_resolution[key] = by_resolution.get(key, 0) + 1
    internal = sum(1 for r in rows if r.get("target_path"))
    return {
        "import_statements": len(rows),
        "resolved_internal": internal,
        "internal_resolution_rate": round(internal / total, 6),
        "by_resolution": dict(sorted(by_resolution.items())),
    }
