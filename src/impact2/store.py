"""Deterministic table storage for Phase 2.

Same discipline as Phase 1: rows are sorted by an explicit key before writing,
the content hash is computed from canonicalised rows rather than Parquet bytes,
and a ``.meta.json`` sidecar records the row count, hash and derivation
version.  Phase 2 adds one thing — every table carries the ID of the run that
produced it and the digest of the configuration that was in force, because a
Phase 2 table is an *interpretation* and the interpretation's settings are part
of its identity.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from .ids import OPERATIONAL_COLUMNS, canonical_json, content_hash
from .versions import METHODOLOGY_VERSION

PARQUET_COMPRESSION = "zstd"
PARQUET_COMPRESSION_LEVEL = 3


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _normalise_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Give every row the same key set, preserving first-seen column order."""
    materialised = [dict(r) for r in rows]
    if not materialised:
        return []
    columns: list[str] = []
    seen: set[str] = set()
    for row in materialised:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return [{col: row.get(col) for col in columns} for row in materialised]


def _json_safe(value: Any) -> Any:
    """Serialise nested structures Arrow cannot infer a stable type for.

    Phase 2 rows carry heterogeneous evidence payloads (lists of dicts with
    varying keys). Arrow would either fail or invent a union type that differs
    between runs, so those columns are stored as canonical JSON strings and the
    column name is suffixed ``_json`` at the call site.
    """
    return canonical_json(value)


def write_table(
    path: Path | str,
    rows: Iterable[Mapping[str, Any]],
    *,
    sort_keys: Sequence[str],
    json_columns: Sequence[str] = (),
    exclude_from_hash: Sequence[str] = OPERATIONAL_COLUMNS,
    extra_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(path)
    materialised = _normalise_rows(rows)

    if json_columns and materialised:
        for row in materialised:
            for column in json_columns:
                if column in row and not isinstance(row[column], str):
                    row[column] = _json_safe(row[column])

    if sort_keys and materialised:
        missing = [k for k in sort_keys if k not in materialised[0]]
        if missing:
            raise KeyError(f"{path.name}: sort keys absent from rows: {missing}")
        materialised.sort(key=lambda r: canonical_json([r.get(k) for k in sort_keys]))

    digest = content_hash(materialised, exclude=exclude_from_hash)

    table = (
        pa.Table.from_pylist(materialised) if materialised else pa.table({})
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table, path,
        compression=PARQUET_COMPRESSION,
        compression_level=PARQUET_COMPRESSION_LEVEL,
        use_dictionary=True, write_statistics=True, version="2.6",
    )

    meta: dict[str, Any] = {
        "table": path.stem,
        "row_count": len(materialised),
        "columns": list(table.schema.names),
        "sort_keys": list(sort_keys),
        "json_columns": list(json_columns),
        "content_sha256": digest,
        "hash_excludes": list(exclude_from_hash),
        "methodology_version": METHODOLOGY_VERSION,
        "compression": f"{PARQUET_COMPRESSION}:{PARQUET_COMPRESSION_LEVEL}",
    }
    if extra_meta:
        meta.update(dict(extra_meta))
    atomic_write(
        path.with_suffix(".meta.json"),
        (json.dumps(meta, indent=2, sort_keys=True, default=str) + "\n").encode(),
    )
    return meta


def read_table(path: Path | str, *, json_columns: Sequence[str] = ()) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    rows = pq.read_table(path).to_pylist()
    if json_columns:
        for row in rows:
            for column in json_columns:
                value = row.get(column)
                if isinstance(value, str):
                    try:
                        row[column] = json.loads(value)
                    except (TypeError, ValueError):
                        pass
    return rows


def table_meta(path: Path | str) -> dict[str, Any] | None:
    meta_path = Path(path).with_suffix(".meta.json")
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def write_json(path: Path | str, payload: Any) -> Path:
    path = Path(path)
    atomic_write(
        path,
        (json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n").encode(),
    )
    return path


def read_json(path: Path | str, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_compact(path: Path | str, payload: Any) -> Path:
    """Compact writer for the export package, where payload size matters."""
    path = Path(path)
    atomic_write(
        path,
        (json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
         + "\n").encode(),
    )
    return path
