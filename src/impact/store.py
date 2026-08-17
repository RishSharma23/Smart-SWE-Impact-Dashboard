"""Deterministic table storage.

Every table written by the pipeline goes through :func:`write_table`, which

* sorts rows by an explicit key so row order is never an accident of dict
  iteration or API pagination order,
* writes Parquet with fixed codec settings,
* records a *content* hash (see :mod:`impact.hashing`) rather than a file hash,
  because Parquet embeds a writer string that changes between runs, and
* writes a sidecar ``.meta.json`` holding the row count, hash, schema version
  and column list.

The sidecars are what the reproducibility gate and the run manifest read.
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from .hashing import OPERATIONAL_COLUMNS, canonical_json, content_hash
from .versions import PIPELINE_VERSION, SCHEMA_VERSION

PARQUET_COMPRESSION = "zstd"
PARQUET_COMPRESSION_LEVEL = 3


def _atomic_write(path: Path, payload: bytes) -> None:
    """Write via a temp file in the same directory then rename.

    An interrupted run must never leave a half-written table that a later
    resume would happily read.
    """
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
    """Give every row the same key set.

    PyArrow infers a schema from the first rows; a column that only appears in
    row 900 would otherwise be dropped. Absent keys become ``None`` -- which in
    this pipeline means "not recorded", distinct from ``0`` and from an empty
    list (principle 5).
    """
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


def write_table(
    path: Path | str,
    rows: Iterable[Mapping[str, Any]],
    *,
    sort_keys: Sequence[str],
    schema: pa.Schema | None = None,
    exclude_from_hash: Sequence[str] = OPERATIONAL_COLUMNS,
    extra_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write ``rows`` to Parquet and return the sidecar metadata."""
    path = Path(path)
    materialised = _normalise_rows(rows)

    if sort_keys and materialised:
        missing = [k for k in sort_keys if k not in materialised[0]]
        if missing:
            raise KeyError(f"{path.name}: sort keys absent from rows: {missing}")
        materialised.sort(
            key=lambda r: canonical_json([r.get(k) for k in sort_keys])
        )

    table = (
        pa.Table.from_pylist(materialised, schema=schema)
        if materialised
        else pa.Table.from_pylist([], schema=schema)
        if schema is not None
        else pa.table({})
    )

    # Hash what a READER will see, not what the writer held in memory.
    #
    # Arrow does not round-trip Python dicts unchanged: a dict column becomes a
    # struct whose fields are the union of every key seen in the table, so rows
    # that omitted a key come back with that key set to None. Hashing the
    # in-memory rows therefore produces a digest that the same data, read back,
    # can never reproduce -- which made the reproducibility gate fail on four
    # tables for no real reason. Hashing the round-tripped rows makes
    # `hash(written) == hash(read)` an invariant the gate can rely on.
    digest = content_hash(
        table.to_pylist() if materialised else [], exclude=exclude_from_hash
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table,
        path,
        compression=PARQUET_COMPRESSION,
        compression_level=PARQUET_COMPRESSION_LEVEL,
        use_dictionary=True,
        write_statistics=True,
        version="2.6",
    )

    meta: dict[str, Any] = {
        "table": path.stem,
        "path": str(path),
        "row_count": len(materialised),
        "columns": list(table.schema.names),
        "sort_keys": list(sort_keys),
        "content_sha256": digest,
        "hash_excludes": list(exclude_from_hash),
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "compression": f"{PARQUET_COMPRESSION}:{PARQUET_COMPRESSION_LEVEL}",
    }
    if extra_meta:
        meta.update(dict(extra_meta))

    _atomic_write(
        path.with_suffix(".meta.json"),
        (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return meta


def read_table(path: Path | str) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    return pq.read_table(path).to_pylist()


def table_meta(path: Path | str) -> dict[str, Any] | None:
    meta_path = Path(path).with_suffix(".meta.json")
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Raw layer: immutable, append-only, gzipped JSONL.
# Principle 1 -- raw records are kept next to the normalized ones forever.
# --------------------------------------------------------------------------


class RawStore:
    """Append-only gzipped JSONL shards for immutable source records."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def shard_path(self, entity: str, shard: str) -> Path:
        safe = shard.replace("/", "_").replace(":", "-")
        return self.root / entity / f"{safe}.jsonl.gz"

    def append(self, entity: str, shard: str, records: Iterable[Mapping[str, Any]]) -> Path:
        path = self.shard_path(entity, shard)
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "at", encoding="utf-8") as fh:
            for record in records:
                fh.write(canonical_json(record))
                fh.write("\n")
        return path

    def write(self, entity: str, shard: str, records: Iterable[Mapping[str, Any]]) -> Path:
        """Replace a shard atomically (used when re-fetching one page)."""
        path = self.shard_path(entity, shard)
        path.parent.mkdir(parents=True, exist_ok=True)
        buf = "".join(canonical_json(r) + "\n" for r in records)
        _atomic_write(path, gzip.compress(buf.encode("utf-8"), compresslevel=6))
        return path

    def read(self, entity: str, shard: str) -> list[dict[str, Any]]:
        path = self.shard_path(entity, shard)
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def read_entity(self, entity: str) -> list[dict[str, Any]]:
        """Read every shard of an entity in deterministic filename order."""
        folder = self.root / entity
        if not folder.exists():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(folder.glob("*.jsonl.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        out.append(json.loads(line))
        return out

    def exists(self, entity: str, shard: str) -> bool:
        return self.shard_path(entity, shard).exists()


def write_json(path: Path | str, payload: Any) -> Path:
    path = Path(path)
    _atomic_write(
        path, (json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n").encode()
    )
    return path


def read_json(path: Path | str, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))
