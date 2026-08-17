"""Python import extraction via the standard-library AST.

Using ``ast`` rather than a regex matters here: it gets multi-line parenthesised
imports, ``from . import x`` relative levels, and conditional imports inside
``if TYPE_CHECKING:`` right, and it *fails loudly* on a file it cannot parse so
the coverage number is honest.

No PostHog dependency is installed and nothing is executed (spec principle 8) --
``ast.parse`` is a pure parse of the source text.
"""

from __future__ import annotations

import ast
import warnings
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable


@dataclass
class PyImport:
    module: str | None
    names: list[str]
    level: int          # 0 = absolute, >0 = relative dots
    is_type_only: bool
    is_dynamic: bool    # inside a function/try, i.e. not module-level
    lineno: int


def parse_python_imports(source: str) -> tuple[list[PyImport], str | None]:
    """Return (imports, error).  ``error`` is non-None when parsing failed."""
    try:
        # Source files legitimately contain things like "\ " in docstrings;
        # that is the analysed repository's business, not a parse failure, so
        # the warning is suppressed rather than printed 40k times.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return [], f"{type(exc).__name__}: {exc}"[:200]

    out: list[PyImport] = []
    type_checking_lines: set[int] = set()

    for node in ast.walk(tree):
        # `if TYPE_CHECKING:` imports exist only for type checkers; they are a
        # real edge for humans but not at runtime, so they are labelled.
        if isinstance(node, ast.If):
            test = node.test
            name = (
                test.id if isinstance(test, ast.Name)
                else test.attr if isinstance(test, ast.Attribute)
                else None
            )
            if name == "TYPE_CHECKING":
                for child in ast.walk(node):
                    if isinstance(child, (ast.Import, ast.ImportFrom)):
                        type_checking_lines.add(child.lineno)

    module_level_lines = {
        child.lineno
        for child in tree.body
        if isinstance(child, (ast.Import, ast.ImportFrom))
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(
                    PyImport(
                        module=alias.name,
                        names=[],
                        level=0,
                        is_type_only=node.lineno in type_checking_lines,
                        is_dynamic=node.lineno not in module_level_lines,
                        lineno=node.lineno,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            out.append(
                PyImport(
                    module=node.module,
                    names=[a.name for a in node.names],
                    level=node.level or 0,
                    is_type_only=node.lineno in type_checking_lines,
                    is_dynamic=node.lineno not in module_level_lines,
                    lineno=node.lineno,
                )
            )
    return out, None


def module_name_for_path(path: str) -> str | None:
    """Repo path -> dotted module name (``posthog/api/x.py`` -> ``posthog.api.x``)."""
    pure = PurePosixPath(path)
    if pure.suffix not in {".py", ".pyi"}:
        return None
    parts = list(pure.parts)
    if parts[-1] in {"__init__.py", "__init__.pyi"}:
        parts = parts[:-1]
    else:
        parts[-1] = pure.stem
    return ".".join(parts) if parts else None


def build_module_index(paths: Iterable[str]) -> dict[str, str]:
    """Dotted module name -> repo path, for every Python file in the repo."""
    index: dict[str, str] = {}
    for path in paths:
        name = module_name_for_path(path)
        if not name:
            continue
        # A package __init__ should win over a same-named submodule shadow.
        if name in index and path.endswith("__init__.py"):
            index[name] = path
        else:
            index.setdefault(name, path)
    return index


def resolve_python_import(
    imp: PyImport, *, from_path: str, module_index: dict[str, str]
) -> tuple[str | None, str]:
    """Resolve one import to a repo path.

    Returns ``(target_path, resolution)`` where resolution is one of
    ``exact`` / ``package`` / ``prefix`` / ``external`` / ``unresolved``.
    ``external`` means it resolved to nothing in-repo, which for a third-party
    package is the correct answer, not a failure.
    """
    if imp.level and imp.level > 0:
        base = PurePosixPath(from_path).parent
        for _ in range(imp.level - 1):
            base = base.parent
        candidate_parts = list(base.parts)
        if imp.module:
            candidate_parts += imp.module.split(".")
        dotted = ".".join(candidate_parts)
    else:
        dotted = imp.module or ""

    if not dotted:
        return None, "unresolved"

    if dotted in module_index:
        return module_index[dotted], "exact"

    package_init = dotted + ".__init__"
    if package_init in module_index:
        return module_index[package_init], "package"

    # `from posthog.models import Foo` where Foo is a submodule.
    for name in imp.names:
        joined = f"{dotted}.{name}"
        if joined in module_index:
            return module_index[joined], "exact"

    # Longest in-repo prefix: `posthog.api.x.y` -> `posthog/api/x.py`.
    parts = dotted.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:cut])
        if prefix in module_index:
            return module_index[prefix], "prefix"

    root = parts[0]
    in_repo_roots = {m.split(".", 1)[0] for m in module_index}
    if root not in in_repo_roots:
        return None, "external"
    return None, "unresolved"


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
