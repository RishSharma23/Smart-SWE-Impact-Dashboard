"""Stage: build the Phase 2 artifact package and the machine-readable run manifest.

``artifacts/`` is the *only* thing Phase 2 is allowed to depend on.  It is
self-describing: every table ships with a JSON Schema, a row count and a
content hash, and ``run_manifest.json`` pins the source SHA, the window, every
component version, and the known gaps.

Nothing here re-derives data.  Export copies the normalized and derived tables
verbatim so that "what Phase 2 read" and "what validation checked" are provably
the same bytes.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from .config import Settings, iso
from .hashing import sha256_file
from .ingest.runs import ExtractionRun, env_fingerprint
from .store import read_json, read_table, table_meta, write_json
from .versions import EXTRACTOR_VERSION, FEATURE_VERSIONS, PIPELINE_VERSION, SCHEMA_VERSION

log = logging.getLogger("impact.export")

UTC = dt.timezone.utc

# The contract surface. Adding a table here is a contract change and must be
# reflected in contracts/PHASE_2_CONTRACT.md.
NORMALIZED_TABLES = (
    "actors", "pull_requests", "commits", "commit_parents", "pr_files",
    "reviews", "review_threads", "review_comments", "comments", "issues",
    "references", "feature_flags", "components", "path_map",
    "web_artifacts", "raw_pages", "extraction_runs",
)
DERIVED_TABLES = (
    "pr_change_shape", "pr_blast_radius", "candidate_episode_edges",
    "candidate_episodes", "pr_regression_candidates",
    "review_intervention_candidates", "reviewer_intervention_rollup",
    "pr_anomalies", "dependency_edges", "module_nodes", "component_edges",
)

# Tiny, non-sensitive fixtures committed so tests run without the full clone.
FIXTURE_ROWS = 25


def _arrow_to_json_type(field_type: str) -> str:
    lowered = field_type.lower()
    if lowered.startswith(("int", "uint")):
        return "integer"
    if lowered.startswith(("double", "float", "decimal")):
        return "number"
    if lowered.startswith("bool"):
        return "boolean"
    if lowered.startswith("list"):
        return "array"
    if lowered.startswith("struct") or lowered.startswith("map"):
        return "object"
    return "string"


def _schema_for(path: Path, table: str) -> dict[str, Any]:
    import pyarrow.parquet as pq

    schema = pq.read_schema(path)
    properties: dict[str, Any] = {}
    for field in schema:
        properties[field.name] = {
            "type": [_arrow_to_json_type(str(field.type)), "null"],
            "arrow_type": str(field.type),
        }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://posthog-impact.local/schemas/{table}.schema.json",
        "title": table,
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "x-schema-version": SCHEMA_VERSION,
        "x-pipeline-version": PIPELINE_VERSION,
    }


def run(settings: Settings) -> dict[str, Any]:
    run_rec = ExtractionRun.start(settings, "export")
    artifacts = settings.project_root / "artifacts"
    schemas_dir = settings.project_root / "schemas"
    samples = settings.path("samples")
    for folder in (artifacts, schemas_dir, samples):
        folder.mkdir(parents=True, exist_ok=True)

    tables: dict[str, Any] = {}
    missing: list[str] = []

    for table in NORMALIZED_TABLES + DERIVED_TABLES:
        layer = "normalized" if table in NORMALIZED_TABLES else "derived"
        source = settings.path(layer, f"{table}.parquet")
        if not source.exists():
            missing.append(table)
            continue
        target = artifacts / f"{table}.parquet"
        shutil.copy2(source, target)

        meta = table_meta(source) or {}
        schema = _schema_for(target, table)
        (schemas_dir / f"{table}.schema.json").write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        tables[table] = {
            "layer": layer,
            "path": f"artifacts/{table}.parquet",
            "schema": f"schemas/{table}.schema.json",
            "row_count": meta.get("row_count"),
            "columns": meta.get("columns", []),
            "column_count": len(meta.get("columns", []) or []),
            "sort_keys": meta.get("sort_keys", []),
            # Content hash: order- and writer-independent, so two runs on the
            # same source SHA are comparable. File hash included for integrity.
            "content_sha256": meta.get("content_sha256"),
            "file_sha256": sha256_file(target),
            "file_bytes": target.stat().st_size,
        }

        # Committed fixtures: first N rows, no bodies (they can be long and are
        # not needed to exercise schema/shape in tests).
        rows = read_table(source)[:FIXTURE_ROWS]
        trimmed = [
            {
                k: (v[:200] if isinstance(v, str) and k.endswith("_text") else v)
                for k, v in row.items()
            }
            for row in rows
        ]
        (samples / f"{table}.sample.json").write_text(
            json.dumps(trimmed, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )

    for name in ("_graph_coverage.json", "_feature_summary.json"):
        source = settings.path("derived", name)
        if source.exists():
            shutil.copy2(source, artifacts / name.lstrip("_"))
    for name in ("_component_rules_snapshot.json",):
        source = settings.path("normalized", name)
        if source.exists():
            shutil.copy2(source, artifacts / name.lstrip("_"))

    quality = read_json(settings.path("derived", "_quality_report.json"), {}) or {}
    if quality:
        write_json(artifacts / "quality_report.json", quality)

    clone_info = read_json(settings.path("raw", "git_extract", "clone_info.json"), {}) or {}
    # `_rate_limit_summary.json` only describes the LAST invocation, so after a
    # small resume run it reports 4 requests for a 2-hour extraction. The
    # ledger is the cumulative record, so cost is summed from there and the
    # last-run figures are kept alongside rather than passed off as the total.
    last_run_rate = read_json(
        settings.path("raw", "github", "_rate_limit_summary.json"), {}
    ) or {}
    ledger_rows = read_json(settings.path("raw", "github", "_ledger.json"), []) or []
    if isinstance(ledger_rows, dict):
        ledger_rows = list(ledger_rows.values())
    rate_limit = {
        "cumulative_requests": len(ledger_rows),
        "cumulative_graphql_points": sum(
            int(r.get("rate_limit_cost") or 0) for r in ledger_rows
        ),
        "cumulative_seconds_in_flight": round(
            sum(float(r.get("elapsed_seconds") or 0) for r in ledger_rows), 1
        ),
        "requests_needing_retry": sum(
            1 for r in ledger_rows if int(r.get("attempt_count") or 1) > 1
        ),
        "requests_by_entity": {
            entity: sum(1 for r in ledger_rows if r.get("entity") == entity)
            for entity in sorted({str(r.get("entity")) for r in ledger_rows})
        },
        "last_run": last_run_rate,
    }
    runs = read_json(settings.path("raw", "extraction_runs.json"), []) or []
    features = read_json(settings.path("derived", "_feature_summary.json"), {}) or {}
    graph = read_json(settings.path("derived", "_graph_coverage.json"), {}) or {}

    manifest = {
        "manifest_version": "1.0.0",
        "generated_at": iso(dt.datetime.now(UTC)),
        "source": {
            "repository_url": settings.repository["url"],
            "repository_qualifier": settings.qualifier,
            "default_branch": settings.default_branch,
            "analyzed_head_sha": clone_info.get("analyzed_head_sha"),
            "analyzed_head_committed_at": clone_info.get("analyzed_head_committed_at"),
            "clone_strategy": clone_info.get("clone_strategy"),
            "is_shallow_clone": clone_info.get("is_shallow"),
            "oldest_available_commit_at": clone_info.get("oldest_available_commit_at"),
            "linear_history": clone_info.get("linear_history"),
        },
        "window": settings.window.as_dict(),
        "versions": {
            "pipeline": PIPELINE_VERSION,
            "extractor": EXTRACTOR_VERSION,
            "schema": SCHEMA_VERSION,
            "features": dict(FEATURE_VERSIONS),
        },
        "environment": env_fingerprint(),
        "tables": tables,
        "missing_tables": missing,
        "coverage": {
            "graph": {
                k: graph.get(k)
                for k in (
                    "tracked_files", "graph_nodes", "internal_edges",
                    "python", "typescript_javascript", "not_parsed_files",
                    "not_parsed_languages", "parse_errors",
                )
            },
            "features": {
                k: features.get(k)
                for k in (
                    "change_shape", "blast_radius", "episodes", "regression",
                    "review_intervention", "anomaly",
                )
            },
            "completeness_by_month": features.get("completeness_by_month", []),
        },
        "api_cost": rate_limit,
        "quality_gates": quality.get("gates", []),
        "quality_status": quality.get("status"),
        "known_gaps": quality.get("known_gaps", []),
        "runs": [
            {
                k: r.get(k)
                for k in ("run_id", "stage", "status", "run_started_at",
                          "duration_seconds")
            }
            for r in runs
        ],
        "phase2_contract": "contracts/PHASE_2_CONTRACT.md",
    }
    write_json(artifacts / "run_manifest.json", manifest)

    run_rec.set("tables_exported", len(tables))
    run_rec.set("missing_tables", missing)
    run_rec.set(
        "artifact_bytes", sum(t["file_bytes"] for t in tables.values())
    )
    run_rec.finish("ok" if not missing else "partial")
    run_rec.append_to(settings.path("raw", "extraction_runs.json"))
    log.info(
        "exported %d tables (%.1f MB) to artifacts/; missing=%s",
        len(tables),
        sum(t["file_bytes"] for t in tables.values()) / 1e6,
        missing or "none",
    )
    return run_rec.as_row()
