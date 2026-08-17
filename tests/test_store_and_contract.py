"""Storage determinism and the Phase 2 contract surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from impact.config import CONFIG_DIR, PROJECT_ROOT
from impact.hashing import content_hash
from impact.store import RawStore, read_table, table_meta, write_table
from impact.versions import FEATURE_VERSIONS, PIPELINE_VERSION, SCHEMA_VERSION


# ------------------------------------------------------------- storage ----


def test_written_hash_survives_the_parquet_round_trip(tmp_path: Path):
    """The reproducibility gate rehashes rows read back from disk, so the
    sidecar must record the hash of what a READER sees.

    Arrow unions dict keys across a struct column, so rows that omitted a key
    come back with it set to None. Hashing the in-memory rows would make the
    gate permanently red for no real reason.
    """
    rows = [
        {"id": 2, "hist": {"a": 1}},
        {"id": 1, "hist": {"a": 1, "b": 2}},   # extra key only on this row
    ]
    path = tmp_path / "t.parquet"
    meta = write_table(path, rows, sort_keys=["id"])
    reread = read_table(path)
    assert content_hash(reread, exclude=meta["hash_excludes"]) == meta["content_sha256"]


def test_rows_are_sorted_by_the_declared_key(tmp_path: Path):
    path = tmp_path / "t.parquet"
    write_table(path, [{"id": 3}, {"id": 1}, {"id": 2}], sort_keys=["id"])
    assert [r["id"] for r in read_table(path)] == [1, 2, 3]


def test_input_order_does_not_change_the_hash(tmp_path: Path):
    rows = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
    a = write_table(tmp_path / "a.parquet", rows, sort_keys=["id"])
    b = write_table(tmp_path / "b.parquet", list(reversed(rows)), sort_keys=["id"])
    assert a["content_sha256"] == b["content_sha256"]


def test_missing_keys_become_null_not_dropped(tmp_path: Path):
    """A column that first appears in a late row must not be lost."""
    path = tmp_path / "t.parquet"
    write_table(path, [{"id": 1}, {"id": 2, "late": "x"}], sort_keys=["id"])
    rows = read_table(path)
    assert rows[0]["late"] is None
    assert rows[1]["late"] == "x"


def test_sort_key_must_exist(tmp_path: Path):
    with pytest.raises(KeyError):
        write_table(tmp_path / "t.parquet", [{"id": 1}], sort_keys=["nope"])


def test_sidecar_records_provenance(tmp_path: Path):
    path = tmp_path / "t.parquet"
    write_table(path, [{"id": 1}], sort_keys=["id"])
    meta = table_meta(path)
    assert meta["schema_version"] == SCHEMA_VERSION
    assert meta["pipeline_version"] == PIPELINE_VERSION
    assert meta["row_count"] == 1


def test_raw_store_round_trip_and_atomic_replace(tmp_path: Path):
    store = RawStore(tmp_path)
    store.write("e", "s", [{"a": 1}])
    store.write("e", "s", [{"a": 2}])       # replace, not append
    assert store.read("e", "s") == [{"a": 2}]
    assert store.exists("e", "s")
    assert store.read("e", "missing") == []


def test_raw_store_reads_shards_in_deterministic_order(tmp_path: Path):
    store = RawStore(tmp_path)
    store.write("e", "b02", [{"n": 2}])
    store.write("e", "b01", [{"n": 1}])
    assert [r["n"] for r in store.read_entity("e")] == [1, 2]


# ------------------------------------------------------------ contract ----


def test_feature_versions_agree_between_code_and_config():
    """Both places are load-bearing: the YAML is readable without Python and a
    quality gate asserts they match. They must be bumped together."""
    cfg = yaml.safe_load((CONFIG_DIR / "feature_versions.yaml").read_text())
    assert cfg["feature_versions"] == {
        k: v for k, v in FEATURE_VERSIONS.items()
    }, "config/feature_versions.yaml disagrees with versions.py"
    assert cfg["pipeline_version"] == PIPELINE_VERSION
    assert cfg["schema_version"] == SCHEMA_VERSION


def test_every_config_file_parses():
    for name in (
        "repository.yaml", "window.yaml", "components.yaml",
        "bots.yaml", "generated_files.yaml", "feature_versions.yaml",
    ):
        assert yaml.safe_load((CONFIG_DIR / name).read_text()), name


def test_window_config_excludes_the_updated_cohort():
    """A PR merely *updated* in the window can predate it by years; including
    that cohort made '90 days of data' untrue."""
    cfg = yaml.safe_load((CONFIG_DIR / "window.yaml").read_text())
    assert "updated" not in cfg["window"]["also_ingest"]
    assert cfg["window"]["lookback_days"] == 90


def test_contract_document_exists_and_names_every_exported_table():
    from impact.export import DERIVED_TABLES, NORMALIZED_TABLES

    contract = (PROJECT_ROOT / "docs" / "PHASE_2_CONTRACT.md").read_text()
    missing = [
        t for t in NORMALIZED_TABLES + DERIVED_TABLES if t not in contract
    ]
    assert not missing, f"tables absent from the Phase 2 contract: {missing}"


def test_contract_states_the_non_negotiable_rules():
    contract = (PROJECT_ROOT / "docs" / "PHASE_2_CONTRACT.md").read_text().lower()
    for phrase in (
        "ranking_eligible",
        "nothing in phase 1 is a score",
        "trunk",
        "squash",
    ):
        assert phrase in contract, f"contract does not mention {phrase!r}"


def test_committed_fixtures_are_small_enough_to_ship():
    samples = PROJECT_ROOT / "data" / "samples"
    if not samples.exists():
        pytest.skip("samples not generated yet (run `make export`)")
    for path in samples.glob("*.sample.json"):
        rows = json.loads(path.read_text())
        assert len(rows) <= 25, path.name
        assert path.stat().st_size < 2_000_000, path.name
