"""Stage: build the language-aware module dependency graph at the analysed commit.

Scope is bounded deliberately (spec: "Do not attempt a full whole-repository
compiler build").  Python uses the stdlib AST; TypeScript/JavaScript uses a
lexical parser plus the real ``tsconfig.json`` alias map.  Rust, Go and HogQL
files are counted as nodes but their edges are not parsed, and that gap is
reported rather than left implicit.

The output is *context evidence*.  Fan-in, fan-out and reachability bands say
how connected a file is; they are never an impact score.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ..config import Settings, iso
from ..ingest.git_source import read_file_at, run_git
from ..ingest.runs import ExtractionRun
from ..normalize.components import ComponentIndex
from ..store import read_json, write_json, write_table
from ..versions import feature_version
from . import py_imports as PY
from . import ts_imports as TS

log = logging.getLogger("impact.graph")

UTC = dt.timezone.utc

PY_EXT = {".py", ".pyi"}
TS_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
UNPARSED_EXT = {".rs", ".go", ".hog", ".sql", ".java", ".kt", ".rb", ".swift"}

# Vendored or build-output trees. Their edges describe a bundler's output, not
# anyone's design, and they would inflate fan-in on whatever they inline.
SKIP_SEGMENTS = frozenset(
    {"node_modules", "vendor", "third_party", "patches", "dist", "build", ".next"}
)

MAX_FILE_BYTES = 2_000_000


def _is_skipped(path: str) -> bool:
    return any(segment in SKIP_SEGMENTS for segment in path.split("/")[:-1])


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def run(settings: Settings) -> dict[str, Any]:
    run_rec = ExtractionRun.start(settings, "graph")
    repo = settings.clone_path
    clone_info = read_json(settings.path("raw", "git_extract", "clone_info.json"), {})
    head_sha = clone_info.get("analyzed_head_sha", "")

    tracked = [
        p for p in run_git(repo, ["ls-files"]).splitlines() if p and not _is_skipped(p)
    ]
    file_set = set(tracked)
    log.info("graph over %d tracked files at %s", len(tracked), head_sha[:12])

    components = ComponentIndex.build(settings, head_sha)
    module_index = PY.build_module_index(tracked)

    # Every workspace's tsconfig, not just the root one: `~/*` means
    # frontend/src/* at the root and nodejs/src/* inside nodejs/.
    scopes, alias_errors = TS.build_scopes(
        tracked, lambda p: read_file_at(settings, head_sha, p)
    )
    for error in alias_errors:
        run_rec.note(error)
    alias_count = sum(len(s.aliases) for s in scopes)
    log.info(
        "tsconfig scopes: %d files, %d alias patterns, %d unparseable",
        len(scopes), alias_count, len(alias_errors),
    )
    resolver = TS.TsResolver(file_set, scopes)

    edges: list[dict[str, Any]] = []
    nodes: dict[str, dict[str, Any]] = {}
    py_records: list[dict[str, Any]] = []
    ts_records: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []
    dynamic_import_files = 0

    for path in tracked:
        suffix = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        if suffix in PY_EXT:
            language = "python"
        elif suffix in TS_EXT:
            language = "typescript" if suffix in {".ts", ".tsx"} else "javascript"
        elif suffix in UNPARSED_EXT:
            language = {"rs": "rust", "go": "go"}.get(suffix.lstrip("."), suffix.lstrip("."))
            resolution = components.resolve(path)
            nodes[path] = {
                "path": path, "language": language,
                "component": resolution.component, "platform": resolution.platform,
                "parse_status": "not_parsed",
                "parse_error": f"no import parser implemented for {language}",
                "has_dynamic_imports": None,
            }
            continue
        else:
            continue

        resolution = components.resolve(path)
        node = nodes.setdefault(
            path,
            {
                "path": path, "language": language,
                "component": resolution.component, "platform": resolution.platform,
                "parse_status": "ok", "parse_error": None,
                "has_dynamic_imports": False,
            },
        )

        source = _read(repo / path)
        if source is None:
            node["parse_status"] = "unreadable"
            node["parse_error"] = "file missing, unreadable, or over the size cap"
            continue

        if language == "python":
            imports, error = PY.parse_python_imports(source)
            if error:
                node["parse_status"] = "parse_error"
                node["parse_error"] = error
                parse_errors.append({"path": path, "error": error})
                continue
            for imp in imports:
                target, how = PY.resolve_python_import(
                    imp, from_path=path, module_index=module_index
                )
                record = {
                    "source_path": path, "target_path": target,
                    "specifier": imp.module, "resolution": how,
                    "language": "python",
                    "kind": "type_import" if imp.is_type_only else "import",
                    "is_type_only": imp.is_type_only,
                    "is_dynamic": imp.is_dynamic,
                }
                py_records.append(record)
                if imp.is_dynamic:
                    node["has_dynamic_imports"] = True
                if target:
                    edges.append(record)
        else:
            imports, has_dynamic = TS.parse_ts_imports(source)
            node["has_dynamic_imports"] = has_dynamic
            if has_dynamic:
                dynamic_import_files += 1
            for imp in imports:
                target, how = resolver.resolve(imp.specifier, path)
                record = {
                    "source_path": path, "target_path": target,
                    "specifier": imp.specifier, "resolution": how,
                    "language": language, "kind": imp.kind,
                    "is_type_only": imp.is_type_only,
                    "is_dynamic": imp.kind == "dynamic",
                }
                ts_records.append(record)
                if target:
                    edges.append(record)

    log.info(
        "parsed %d python + %d ts/js import statements -> %d internal edges",
        len(py_records), len(ts_records), len(edges),
    )

    # -- degrees ---------------------------------------------------------
    fan_out: dict[str, set[str]] = defaultdict(set)
    fan_in: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        source, target = edge["source_path"], edge["target_path"]
        if source == target:
            continue
        fan_out[source].add(target)
        fan_in[target].add(source)

    edge_rows: list[dict[str, Any]] = []
    for edge in edges:
        source_component = (nodes.get(edge["source_path"]) or {}).get("component", "unknown")
        target_component = (nodes.get(edge["target_path"]) or {}).get("component")
        if target_component is None:
            target_component = components.resolve(edge["target_path"]).component
        edge_rows.append(
            {
                **edge,
                "source_component": source_component,
                "target_component": target_component,
                "crosses_component": source_component != target_component,
                "dependency_graph_version": feature_version("dependency_graph"),
            }
        )

    # "Hub" is a percentile of the observed fan-in distribution, not a magic
    # constant, so it stays meaningful if the window or repository changes.
    fan_in_values = sorted(len(v) for v in fan_in.values())
    percentile = float(settings.param("blast_radius", "hub_fan_in_percentile", 0.95))
    hub_threshold = (
        fan_in_values[min(int(len(fan_in_values) * percentile), len(fan_in_values) - 1)]
        if fan_in_values
        else 0
    )

    node_rows: list[dict[str, Any]] = []
    for path, node in nodes.items():
        node_rows.append(
            {
                **node,
                "fan_in": len(fan_in.get(path, ())),
                "fan_out": len(fan_out.get(path, ())),
                "is_hub": len(fan_in.get(path, ())) >= max(hub_threshold, 1),
                "cross_component_fan_in": len(
                    {
                        s for s in fan_in.get(path, ())
                        if (nodes.get(s) or {}).get("component") != node["component"]
                    }
                ),
                "dependency_graph_version": feature_version("dependency_graph"),
            }
        )

    # -- component-level graph -------------------------------------------
    component_edges: dict[tuple[str, str], int] = defaultdict(int)
    for edge in edge_rows:
        if edge["crosses_component"]:
            component_edges[(edge["source_component"], edge["target_component"])] += 1
    component_edge_rows = [
        {
            "source_component": source, "target_component": target,
            "edge_count": count,
            "dependency_graph_version": feature_version("dependency_graph"),
        }
        for (source, target), count in sorted(component_edges.items())
    ]

    out = settings.path("derived")
    out.mkdir(parents=True, exist_ok=True)
    written = {
        "dependency_edges": write_table(
            out / "dependency_edges.parquet", edge_rows,
            sort_keys=["source_path", "target_path", "kind"],
        ),
        "module_nodes": write_table(
            out / "module_nodes.parquet", node_rows, sort_keys=["path"]
        ),
        "component_edges": write_table(
            out / "component_edges.parquet", component_edge_rows,
            sort_keys=["source_component", "target_component"],
        ),
    }

    coverage = {
        "analyzed_head_sha": head_sha,
        "tracked_files": len(tracked),
        "graph_nodes": len(node_rows),
        "internal_edges": len(edge_rows),
        "python": PY.summarise(py_records),
        "typescript_javascript": TS.summarise(ts_records),
        "parse_errors": len(parse_errors),
        "parse_error_examples": parse_errors[:15],
        "not_parsed_languages": sorted(
            {n["language"] for n in node_rows if n["parse_status"] == "not_parsed"}
        ),
        "not_parsed_files": sum(
            1 for n in node_rows if n["parse_status"] == "not_parsed"
        ),
        "files_with_dynamic_imports": dynamic_import_files,
        "hub_fan_in_threshold": hub_threshold,
        "tsconfig_scopes": len(scopes),
        "tsconfig_alias_patterns": alias_count,
        "tsconfig_errors": alias_errors,
        "computed_at": iso(dt.datetime.now(UTC)),
        "dependency_graph_version": feature_version("dependency_graph"),
    }
    write_json(settings.path("derived", "_graph_coverage.json"), coverage)

    run_rec.set("tables", {k: v["row_count"] for k, v in written.items()})
    run_rec.set("coverage", coverage)
    run_rec.finish("ok")
    run_rec.append_to(settings.path("raw", "extraction_runs.json"))
    log.info("graph coverage: %s", {
        "py_rate": coverage["python"]["internal_resolution_rate"],
        "ts_rate": coverage["typescript_javascript"]["internal_resolution_rate"],
        "edges": len(edge_rows),
    })
    return run_rec.as_row()


def load_graph(settings: Settings) -> tuple[dict[str, set[str]], dict[str, dict[str, Any]]]:
    """Adjacency (importer -> imported) plus node attributes, for feature code."""
    from ..store import read_table

    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in read_table(settings.path("derived", "dependency_edges.parquet")):
        if edge.get("target_path"):
            adjacency[edge["source_path"]].add(edge["target_path"])
    nodes = {
        n["path"]: n
        for n in read_table(settings.path("derived", "module_nodes.parquet"))
    }
    return adjacency, nodes


def reverse_adjacency(adjacency: dict[str, set[str]]) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = defaultdict(set)
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].add(source)
    return reverse


def reachable(
    seeds: Iterable[str], adjacency: dict[str, set[str]], *, max_depth: int = 3
) -> set[str]:
    """Bounded BFS.  Depth is capped because unbounded reachability in a
    monorepo trivially reaches everything and stops being informative."""
    frontier = {s for s in seeds if s}
    seen: set[str] = set(frontier)
    for _ in range(max_depth):
        nxt: set[str] = set()
        for node in frontier:
            nxt |= adjacency.get(node, set()) - seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    return seen
