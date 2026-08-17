"""Extraction bookkeeping: run records and the raw-page ledger.

Two append-only ledgers back the resumability and auditability guarantees:

``extraction_runs``
    one row per invocation -- what was asked for, what code version answered,
    how long it took, whether it finished.

``raw_pages``
    one row per API request actually issued -- request hash, cursor, where the
    response body landed on disk, its content hash, status, rate-limit cost.
    This is what makes "resume from checkpoint" and "prove we did not silently
    drop a page" possible.
"""

from __future__ import annotations

import datetime as dt
import os
import platform
import socket
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings, iso
from ..store import read_json, write_json, write_table
from ..versions import EXTRACTOR_VERSION, PIPELINE_VERSION, SCHEMA_VERSION

UTC = dt.timezone.utc


@dataclass
class RawPageLedger:
    """Append-only record of every request issued, persisted incrementally."""

    path: Path
    rows: list[dict[str, Any]] = field(default_factory=list)
    _index: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: Path | str) -> "RawPageLedger":
        path = Path(path)
        rows = read_json(path, default=[]) or []
        ledger = cls(path=path, rows=list(rows))
        ledger._index = {
            r["request_hash"]: r for r in ledger.rows if r.get("request_hash")
        }
        return ledger

    def get(self, request_hash: str) -> dict[str, Any] | None:
        return self._index.get(request_hash)

    def succeeded(self, request_hash: str) -> bool:
        row = self._index.get(request_hash)
        return bool(row and row.get("status") == "ok")

    def record(self, row: dict[str, Any]) -> None:
        key = row.get("request_hash")
        if key and key in self._index:
            self._index[key].update(row)
        else:
            self.rows.append(row)
            if key:
                self._index[key] = row

    def flush(self) -> None:
        write_json(self.path, self.rows)

    def to_parquet(self, out_path: Path | str) -> dict[str, Any]:
        return write_table(
            out_path,
            self.rows,
            sort_keys=["entity", "shard", "page_index", "request_hash"],
        )


@dataclass
class ExtractionRun:
    """One pipeline invocation."""

    run_id: str
    stage: str
    settings: Settings
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    status: str = "running"
    counters: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def start(cls, settings: Settings, stage: str) -> "ExtractionRun":
        return cls(
            run_id=uuid.uuid4().hex[:16],
            stage=stage,
            settings=settings,
            started_at=dt.datetime.now(UTC),
        )

    def count(self, key: str, delta: int = 1) -> None:
        self.counters[key] = int(self.counters.get(key, 0)) + delta

    def set(self, key: str, value: Any) -> None:
        self.counters[key] = value

    def note(self, message: str) -> None:
        self.notes.append(message)

    def finish(self, status: str = "ok") -> dict[str, Any]:
        self.finished_at = dt.datetime.now(UTC)
        self.status = status
        return self.as_row()

    def as_row(self) -> dict[str, Any]:
        finished = self.finished_at or dt.datetime.now(UTC)
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "status": self.status,
            "run_started_at": iso(self.started_at),
            "run_finished_at": iso(finished),
            "duration_seconds": round(
                (finished - self.started_at).total_seconds(), 3
            ),
            "window_start": iso(self.settings.window.start),
            "window_end": iso(self.settings.window.end),
            "repository": self.settings.qualifier,
            "extractor_version": EXTRACTOR_VERSION,
            "schema_version": SCHEMA_VERSION,
            "pipeline_version": PIPELINE_VERSION,
            "python_version": platform.python_version(),
            "platform": f"{platform.system()}-{platform.machine()}",
            "host": socket.gethostname(),
            "counters": self.counters,
            "notes": self.notes,
        }

    def append_to(self, path: Path | str) -> None:
        path = Path(path)
        rows = read_json(path, default=[]) or []
        rows = [r for r in rows if r.get("run_id") != self.run_id]
        rows.append(self.as_row())
        write_json(path, rows)


def checkpoint_path(settings: Settings, name: str) -> Path:
    p = settings.path("raw", "_checkpoints", f"{name}.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_checkpoint(settings: Settings, name: str) -> dict[str, Any]:
    return read_json(checkpoint_path(settings, name), default={}) or {}


def save_checkpoint(settings: Settings, name: str, state: dict[str, Any]) -> None:
    state = dict(state)
    state["_updated_at"] = iso(dt.datetime.now(UTC))
    write_json(checkpoint_path(settings, name), state)


def env_fingerprint() -> dict[str, Any]:
    """Non-secret environment facts worth pinning into the run manifest."""
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "extractor_version": EXTRACTOR_VERSION,
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
    }
