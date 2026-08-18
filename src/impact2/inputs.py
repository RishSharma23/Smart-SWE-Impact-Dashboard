"""Phase 1 input loading, manifest verification and hash checking.

The phase spec opens with an instruction that is easy to skip and expensive to
skip: *verify the input manifest and hashes before analysis*.  This module is
that verification, and it refuses to proceed quietly when something is off.

What it checks
--------------
1. ``artifacts/run_manifest.json`` exists and its ``versions.schema`` matches
   the Phase 1 schema this code was written against.
2. Every table named in the manifest is present, and its file hash matches
   ``file_sha256``.  Cheap, always run.
3. Optionally (``--verify-content-hashes``) every table's rows are re-read and
   re-hashed and compared against ``content_sha256``.  This is the honest
   check — it survives a Parquet rewrite — and it costs a full read.
4. ``known_gaps`` is loaded and carried into every downstream artifact.  The
   contract says it *must* be read; carrying it means it also cannot be
   forgotten between here and the UI.

The fallback
------------
While Phase 1's GitHub extraction is still running there is no ``artifacts/``
directory, only ``data/normalized`` and ``data/derived``.  Phase 2 can be
developed and smoke-tested against those, but doing so silently would be a
contract violation.  So the fallback is opt-in (``--allow-unexported``),
records ``input_source = "unexported_pipeline_layers"``, and injects a
``known_gap`` that propagates all the way into the dashboard manifest, where it
reads *"analysis ran against un-exported pipeline layers; results are
provisional"*.  A run in that mode can never be marked ``final``.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import Paths, iso, now, parse_ts
from .ids import OPERATIONAL_COLUMNS, content_hash, sha256_file
from .store import read_json, read_table, table_meta
from .versions import REQUIRED_PHASE1_SCHEMA

log = logging.getLogger("impact2.inputs")

# The Phase 1 contract surface, contracts/PHASE_2_CONTRACT.md §4 and §5.
NORMALIZED_TABLES = (
    "actors", "pull_requests", "commits", "commit_parents", "pr_files",
    "reviews", "review_threads", "review_comments", "comments", "issues",
    "references", "feature_flags", "components", "path_map",
    "raw_pages", "extraction_runs",
)
DERIVED_TABLES = (
    "pr_change_shape", "pr_blast_radius", "candidate_episode_edges",
    "candidate_episodes", "pr_regression_candidates",
    "review_intervention_candidates", "reviewer_intervention_rollup",
    "pr_anomalies", "dependency_edges", "module_nodes", "component_edges",
)
ALL_TABLES = NORMALIZED_TABLES + DERIVED_TABLES

# Tables Phase 2 cannot do its job without.  Everything else degrades a
# dimension rather than stopping the run.
REQUIRED_TABLES = ("actors", "pull_requests", "pr_files")

# Tables whose absence disables a specific capability, and what it disables.
CAPABILITY_TABLES = {
    "review_comments": "review interventions, collaborative amplification",
    "review_intervention_candidates": "review causality, decision quality via review",
    "review_threads": "review thread resolution evidence",
    "issues": "problem framing, originator role, linked-issue corroboration",
    "dependency_edges": "dependency propagation, engineering leverage",
    "module_nodes": "hub damping, blast-radius corroboration",
    "pr_regression_candidates": "corrective burden, durability counterevidence",
    "pr_blast_radius": "reach evidence for reliability and product bands",
    "pr_change_shape": "change composition, title-claim corroboration",
    "feature_flags": "rollout status, flag-arc episode edges",
    "candidate_episode_edges": "Tier A deterministic episode edges",
}


@dataclass
class TableCheck:
    table: str
    present: bool
    path: str | None
    row_count: int | None
    expected_row_count: int | None
    file_hash_ok: bool | None
    content_hash_ok: bool | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "present": self.present,
            "row_count": self.row_count,
            "expected_row_count": self.expected_row_count,
            "file_hash_ok": self.file_hash_ok,
            "content_hash_ok": self.content_hash_ok,
            "detail": self.detail,
        }


@dataclass
class Phase1Inputs:
    """Everything Phase 2 reads, plus the proof it was read correctly."""

    manifest: dict[str, Any]
    tables: dict[str, list[dict[str, Any]]]
    source_root: Path
    input_source: str                      # artifacts | unexported_pipeline_layers
    checks: list[TableCheck]
    known_gaps: list[dict[str, Any]]
    verification_status: str               # verified | degraded | unverified
    verified_at: str
    content_hashes_verified: bool
    capabilities_disabled: dict[str, str] = field(default_factory=dict)

    # -- convenience -----------------------------------------------------
    def table(self, name: str) -> list[dict[str, Any]]:
        return self.tables.get(name) or []

    @property
    def window_start(self) -> dt.datetime | None:
        return parse_ts((self.manifest.get("window") or {}).get("start"))

    @property
    def window_end(self) -> dt.datetime | None:
        return parse_ts((self.manifest.get("window") or {}).get("end"))

    @property
    def window_days(self) -> int | None:
        start, end = self.window_start, self.window_end
        if start is None or end is None:
            return None
        return int(round((end - start).total_seconds() / 86400.0))

    @property
    def head_sha(self) -> str | None:
        return (self.manifest.get("source") or {}).get("analyzed_head_sha")

    @property
    def repository_url(self) -> str:
        return (self.manifest.get("source") or {}).get(
            "repository_url", "https://github.com/PostHog/posthog"
        )

    @property
    def qualifier(self) -> str:
        return (self.manifest.get("source") or {}).get(
            "repository_qualifier", "github.com/PostHog/posthog"
        )

    @property
    def is_shallow(self) -> bool:
        return bool((self.manifest.get("source") or {}).get("is_shallow_clone"))

    def provenance(self) -> dict[str, Any]:
        """The block every Phase 2 artifact embeds so nothing floats free."""
        return {
            "input_source": self.input_source,
            "verification_status": self.verification_status,
            "verified_at": self.verified_at,
            "content_hashes_verified": self.content_hashes_verified,
            "phase1_schema_version": (self.manifest.get("versions") or {}).get("schema"),
            "phase1_pipeline_version": (self.manifest.get("versions") or {}).get("pipeline"),
            "analyzed_head_sha": self.head_sha,
            "window": self.manifest.get("window"),
            "table_content_hashes": {
                c.table: (self.manifest.get("tables", {}).get(c.table) or {}).get(
                    "content_sha256"
                )
                for c in self.checks
                if c.present
            },
            "capabilities_disabled": dict(self.capabilities_disabled),
            "known_gap_count": len(self.known_gaps),
        }

    def verification_report(self) -> dict[str, Any]:
        return {
            "verified_at": self.verified_at,
            "input_source": self.input_source,
            "source_root": self.source_root.name,
            "status": self.verification_status,
            "content_hashes_verified": self.content_hashes_verified,
            "tables": [c.as_dict() for c in self.checks],
            "tables_present": sum(1 for c in self.checks if c.present),
            "tables_expected": len(ALL_TABLES),
            "file_hash_failures": [
                c.table for c in self.checks if c.file_hash_ok is False
            ],
            "content_hash_failures": [
                c.table for c in self.checks if c.content_hash_ok is False
            ],
            "capabilities_disabled": dict(self.capabilities_disabled),
            "known_gaps": self.known_gaps,
        }


class InputError(RuntimeError):
    """Phase 1 inputs are missing or fail verification."""


def _synthesise_manifest(paths: Paths) -> dict[str, Any]:
    """Build a stand-in manifest from the pipeline layers' own sidecars.

    Used only in the un-exported fallback.  Every field it cannot know is
    ``None`` rather than a plausible-looking guess, and the caller stamps the
    result as provisional.
    """
    clone_info = read_json(
        paths.project_root / "data" / "raw" / "git_extract" / "clone_info.json", {}
    ) or {}
    tables: dict[str, Any] = {}
    for table in ALL_TABLES:
        layer = "normalized" if table in NORMALIZED_TABLES else "derived"
        source = (
            paths.fallback_normalized if layer == "normalized"
            else paths.fallback_derived
        ) / f"{table}.parquet"
        meta = table_meta(source)
        if meta:
            tables[table] = {
                "layer": layer,
                "path": str(source.relative_to(paths.project_root)),
                "row_count": meta.get("row_count"),
                "columns": meta.get("columns", []),
                "content_sha256": meta.get("content_sha256"),
                "file_sha256": None,
                "sort_keys": meta.get("sort_keys", []),
            }
    window_cfg = read_json(paths.project_root / "config" / "window.yaml", None)
    return {
        "manifest_version": "synthesised-from-pipeline-layers",
        "generated_at": iso(now()),
        "source": {
            "repository_url": "https://github.com/PostHog/posthog",
            "repository_qualifier": "github.com/PostHog/posthog",
            "default_branch": clone_info.get("default_branch", "master"),
            "analyzed_head_sha": clone_info.get("analyzed_head_sha"),
            "analyzed_head_committed_at": clone_info.get("analyzed_head_committed_at"),
            "clone_strategy": clone_info.get("clone_strategy"),
            "is_shallow_clone": clone_info.get("is_shallow"),
            "linear_history": clone_info.get("linear_history"),
        },
        "window": _window_from_data(paths) or {},
        "versions": {"schema": REQUIRED_PHASE1_SCHEMA, "pipeline": None,
                     "synthesised": True},
        "tables": tables,
        "quality_status": None,
        "quality_gates": [],
        "known_gaps": [],
        "_synthesised": True,
        "_synthesis_note": (
            "artifacts/run_manifest.json was absent; this manifest was rebuilt "
            "from data/normalized and data/derived sidecars. Fields Phase 1 "
            "records only at export time are null."
        ),
        "_window_config": window_cfg if isinstance(window_cfg, dict) else None,
    }


def _window_from_data(paths: Paths) -> dict[str, Any] | None:
    """Recover the window from the extraction-run records, not from `now()`.

    Deriving it from the wall clock would make two runs on different days
    disagree about what the dataset covers.
    """
    runs = read_json(paths.project_root / "data" / "raw" / "extraction_runs.json", []) or []
    for record in runs:
        window = record.get("window")
        if isinstance(window, dict) and window.get("start"):
            return window
    return None


def load_inputs(
    paths: Paths,
    *,
    allow_unexported: bool = False,
    verify_content_hashes: bool = False,
    tables: Iterable[str] | None = None,
) -> Phase1Inputs:
    """Load and verify Phase 1 outputs.

    Raises :class:`InputError` when nothing usable is on disk, or when the
    exported package is present but internally inconsistent.
    """
    wanted = tuple(tables) if tables else ALL_TABLES
    artifacts = paths.phase1_artifacts
    manifest_path = artifacts / "run_manifest.json"

    if manifest_path.exists():
        manifest = read_json(manifest_path, {}) or {}
        source_root = artifacts
        input_source = "artifacts"
    elif allow_unexported and paths.fallback_normalized.exists():
        manifest = _synthesise_manifest(paths)
        source_root = paths.project_root / "data"
        input_source = "unexported_pipeline_layers"
        log.warning(
            "artifacts/run_manifest.json is absent; falling back to un-exported "
            "pipeline layers. Results are PROVISIONAL and the run cannot be "
            "marked final."
        )
    else:
        raise InputError(
            "No Phase 1 inputs found. Expected artifacts/run_manifest.json "
            "(run `make export`). To work against the un-exported pipeline "
            "layers while ingestion finishes, pass --allow-unexported."
        )

    schema_version = (manifest.get("versions") or {}).get("schema")
    if schema_version and schema_version != REQUIRED_PHASE1_SCHEMA:
        raise InputError(
            f"Phase 1 schema version is {schema_version!r}; this Phase 2 code is "
            f"written against {REQUIRED_PHASE1_SCHEMA!r}. Re-read "
            "contracts/PHASE_2_CONTRACT.md before proceeding."
        )

    manifest_tables = manifest.get("tables") or {}
    loaded: dict[str, list[dict[str, Any]]] = {}
    checks: list[TableCheck] = []

    for table in wanted:
        if input_source == "artifacts":
            path = artifacts / f"{table}.parquet"
        else:
            layer = (
                paths.fallback_normalized if table in NORMALIZED_TABLES
                else paths.fallback_derived
            )
            path = layer / f"{table}.parquet"

        expected = manifest_tables.get(table) or {}
        if not path.exists():
            checks.append(
                TableCheck(table, False, None, None, expected.get("row_count"),
                           None, None, "table file absent")
            )
            continue

        file_ok: bool | None = None
        if expected.get("file_sha256"):
            file_ok = sha256_file(path) == expected["file_sha256"]

        rows = read_table(path)
        loaded[table] = rows

        content_ok: bool | None = None
        if verify_content_hashes and expected.get("content_sha256"):
            # The exported artifacts carry no .meta.json sidecar — those live
            # beside the pipeline layers. When one is absent the manifest hash
            # was computed with the standard operational-column exclusions, so
            # defaulting to an empty exclude set would fail every table that
            # has a computed_at column, which is most of them.
            meta = table_meta(path) or {}
            excludes = meta.get("hash_excludes")
            if excludes is None:
                excludes = expected.get("hash_excludes") or OPERATIONAL_COLUMNS
            content_ok = content_hash(rows, exclude=excludes) == expected["content_sha256"]

        expected_rows = expected.get("row_count")
        detail = "ok"
        if expected_rows is not None and expected_rows != len(rows):
            detail = (
                f"row count {len(rows)} != manifest {expected_rows} — stale artifact"
            )
        checks.append(
            TableCheck(table, True, str(path.name), len(rows), expected_rows,
                       file_ok, content_ok, detail)
        )

    missing_required = [t for t in REQUIRED_TABLES if not loaded.get(t)]
    if missing_required:
        raise InputError(
            f"Phase 1 tables required for any analysis are missing or empty: "
            f"{missing_required}. Run `make normalize` first."
        )

    disabled = {
        table: reason
        for table, reason in CAPABILITY_TABLES.items()
        if not loaded.get(table)
    }
    for table, reason in sorted(disabled.items()):
        log.warning("input table %-32s absent -> disables: %s", table, reason)

    hash_failures = [
        c.table for c in checks if c.file_hash_ok is False or c.content_hash_ok is False
    ]
    stale = [c.table for c in checks if c.detail.startswith("row count")]
    if hash_failures:
        raise InputError(
            f"Phase 1 artifact hashes do not match the manifest for: "
            f"{hash_failures}. The artifacts are stale or were modified after "
            "export; re-run `make export` before analysing them."
        )

    status = "verified"
    if input_source != "artifacts":
        status = "unverified"
    elif stale or disabled:
        status = "degraded"

    known_gaps = list(manifest.get("known_gaps") or [])
    if input_source != "artifacts":
        known_gaps.append(
            {
                "gap": "unexported_pipeline_layers",
                "detail": (
                    "Phase 2 read data/normalized and data/derived directly "
                    "because artifacts/run_manifest.json did not exist. Quality "
                    "gates had not been run, and table hashes could not be "
                    "checked against an export manifest."
                ),
                "consequence": (
                    "All results from this run are PROVISIONAL. Re-run "
                    "`make validate && make export && make p2` before publishing."
                ),
                "severity": "blocking_for_publication",
            }
        )
    if stale:
        known_gaps.append(
            {
                "gap": "stale_artifact_row_counts",
                "detail": f"row counts differ from the manifest for: {stale}",
                "consequence": "some tables are older than the manifest describing them",
                "severity": "blocking_for_publication",
            }
        )
    for table, reason in sorted(disabled.items()):
        known_gaps.append(
            {
                "gap": f"missing_input_table:{table}",
                "detail": f"Phase 1 table '{table}' is absent or empty",
                "consequence": f"disabled: {reason}",
                "severity": "degrades_analysis",
            }
        )

    log.info(
        "inputs: source=%s status=%s tables=%d/%d rows=%d",
        input_source, status, len(loaded), len(wanted),
        sum(len(r) for r in loaded.values()),
    )

    return Phase1Inputs(
        manifest=manifest,
        tables=loaded,
        source_root=source_root,
        input_source=input_source,
        checks=checks,
        known_gaps=known_gaps,
        verification_status=status,
        verified_at=iso(now()) or "",
        content_hashes_verified=bool(verify_content_hashes),
        capabilities_disabled=disabled,
    )


# --------------------------------------------------------------------------
# indexes used everywhere downstream
# --------------------------------------------------------------------------


def index_by(rows: Iterable[Mapping[str, Any]], key: str) -> dict[Any, dict[str, Any]]:
    return {r[key]: dict(r) for r in rows if r.get(key) is not None}


def group_by(rows: Iterable[Mapping[str, Any]], key: str) -> dict[Any, list[dict[str, Any]]]:
    out: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        out.setdefault(value, []).append(dict(row))
    return out
