from __future__ import annotations

import json
import random

import pytest

from backend.tools.backfill_v1.extract import (
    TableSpec,
    _read_table,
    extract_canonical_rows,
)


def _source_rows() -> dict[str, list[dict[str, object]]]:
    return {
        "assets": [
            {
                "asset_code": "USDT",
                "kind": "currency",
                "scale": 8,
                "display_scale": 2,
                "name": "Tether",
                "status": "active",
                "version": 1,
            },
            {
                "asset_code": "CNY",
                "kind": "currency",
                "scale": 2,
                "display_scale": 2,
                "name": "Renminbi",
                "status": "active",
                "version": 1,
            },
        ],
        "transactions": [
            {
                "transaction_id": "tx-b",
                "book_id": "book-a",
                "memo": "second",
                "occurred_at": "2026-07-02T00:00:00+00:00",
                "purpose": "expense",
                "reversed_by": None,
                "reverses_transaction_id": None,
                "version": 1,
            },
            {
                "transaction_id": "tx-a",
                "book_id": "book-a",
                "memo": "first",
                "occurred_at": "2026-07-01T00:00:00Z",
                "purpose": "expense",
                "reversed_by": None,
                "reverses_transaction_id": None,
                "version": 1,
            },
        ],
    }


def test_shuffled_input_has_identical_ndjson_and_manifest_hash(tmp_path) -> None:
    rows_a = _source_rows()
    rows_b = _source_rows()
    random.Random(731).shuffle(rows_b["assets"])
    random.Random(37).shuffle(rows_b["transactions"])

    manifest_a = extract_canonical_rows(
        rows_by_table=rows_a,
        output_dir=tmp_path / "run-a",
        dump_sha256="1" * 64,
        source_revision="v1-final",
    )
    manifest_b = extract_canonical_rows(
        rows_by_table=rows_b,
        output_dir=tmp_path / "run-b",
        dump_sha256="1" * 64,
        source_revision="v1-final",
    )

    assert manifest_a.content_sha256 == manifest_b.content_sha256
    assert manifest_a.snapshot_id == manifest_b.snapshot_id
    for table in rows_a:
        assert (tmp_path / "run-a" / "rows" / f"{table}.ndjson").read_bytes() == (
            tmp_path / "run-b" / "rows" / f"{table}.ndjson"
        ).read_bytes()

    persisted = json.loads((tmp_path / "run-a" / "manifest.json").read_text())
    assert persisted["content_sha256"] == manifest_a.content_sha256


def test_extractor_refuses_to_replace_an_existing_output(tmp_path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep-me"
    marker.write_text("untouched")

    with pytest.raises(FileExistsError, match="output directory already exists"):
        extract_canonical_rows(
            rows_by_table=_source_rows(),
            output_dir=output,
            dump_sha256="1" * 64,
            source_revision="v1-final",
        )

    assert marker.read_text() == "untouched"


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value


class _RowsResult:
    def __init__(self) -> None:
        self._batches = [[{"asset_code": "CNY"}], []]

    def mappings(self) -> _RowsResult:
        return self

    def fetchmany(self, size: int) -> list[dict[str, object]]:
        assert size == 37
        return self._batches.pop(0)


class _Context:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None


class _Connection:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def execution_options(self, **options: object) -> _Connection:
        assert options == {"isolation_level": "REPEATABLE READ"}
        return self

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def begin(self) -> _Context:
        self.calls.append("BEGIN")
        return _Context()

    def exec_driver_sql(self, sql: str) -> _ScalarResult:
        self.calls.append(sql)
        return _ScalarResult("on")

    def execute(self, statement: object) -> _RowsResult:
        self.calls.append(str(statement).strip())
        return _RowsResult()


class _Engine:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def connect(self) -> _Connection:
        return _Connection(self.calls)


def test_source_query_is_forced_read_only_before_rows_are_read() -> None:
    engine = _Engine()

    table, rows = _read_table(
        engine,  # type: ignore[arg-type]
        TableSpec("assets", ("asset_code",)),
        batch_size=37,
        shuffle_seed=731,
    )

    assert table == "assets"
    assert rows == [{"asset_code": "CNY"}]
    assert engine.calls[:3] == [
        "BEGIN",
        "SET TRANSACTION READ ONLY",
        "SHOW transaction_read_only",
    ]
    assert engine.calls[3].startswith("SELECT\n    asset_code")
