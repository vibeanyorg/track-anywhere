from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import random
from uuid import UUID

import pytest

from backend.tools.frozen_v1_history.extract import (
    ACCOUNT_UUID_MAP_SHA256,
    ACCOUNT_UUID_MAP_PROTOCOL,
    TABLE_SPECS,
    account_uuid_map_digest,
    canonicalize_table_rows,
    canonicalize_value,
    extract_fixed_source,
    load_audited_sql,
    load_source_contract_sql,
    verify_frozen_table_rows,
    validate_result_columns,
    validate_source_contract,
    validate_source_url,
)
from backend.tools.frozen_v1_history.manifest import read_full_manifest
from backend.tools.frozen_v1_history.inventory import (
    inventory_rows,
    validate_fixed_inventory,
)


FIXTURES = Path(__file__).with_name("fixtures")


def _asset_rows() -> list[dict[str, object]]:
    return [
        {
            "asset_code": "USDT",
            "kind": "crypto",
            "scale": 6,
            "display_scale": 6,
            "name": "Tether",
            "status": "active",
            "version": 1,
        },
        {
            "asset_code": "CNY",
            "kind": "fiat",
            "scale": 2,
            "display_scale": 2,
            "name": "Renminbi",
            "status": "active",
            "version": 1,
        },
    ]


def test_audited_sql_has_exact_columns_order_and_no_parameters_or_writes() -> None:
    assert {spec.table for spec in TABLE_SPECS} == {
        "ledger_books",
        "assets",
        "accounts",
        "categories",
        "category_versions",
        "transactions",
        "postings",
        "transaction_lines",
        "classification_events",
        "investment_events",
        "investment_valuations",
        "counterparties",
    }
    for spec in TABLE_SPECS:
        sql = load_audited_sql(spec)
        lowered = sql.casefold()
        collapsed = " ".join(lowered.split())
        assert lowered.startswith("select\n")
        assert " from public." in collapsed
        assert "*" not in sql
        binds = (":source_book_id",) if spec.book_scoped else ()
        assert sql.count(":source_book_id") == len(binds)
        assert sql.replace(":source_book_id", "").count(":") == 0
        assert "%s" not in sql
        assert "$1" not in sql
        assert "?" not in sql
        assert all(
            keyword not in lowered
            for keyword in (" insert ", " update ", " delete ", " alter ", " drop ")
        )
    source_contract_sql = load_source_contract_sql()
    assert ":source_book_id" in source_contract_sql
    assert ":" not in source_contract_sql.replace(":source_book_id", "")


def test_canonical_values_preserve_exact_types_and_reject_float_or_naive_time() -> None:
    assert canonicalize_value(Decimal("1.2300")) == "1.2300"
    assert canonicalize_value(UUID("00000000-0000-4000-8000-000000000001")) == (
        "00000000-0000-4000-8000-000000000001"
    )
    assert canonicalize_value(date(2026, 7, 13)) == "2026-07-13"
    assert canonicalize_value(
        datetime(2026, 7, 13, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    ) == "2026-07-13T02:00:00.000000Z"
    assert canonicalize_value(b"\x00\xff") == {"$bytes_hex": "00ff"}
    assert canonicalize_value({"z": [Decimal("2.00")], "a": True}) == {
        "a": True,
        "z": ["2.00"],
    }

    with pytest.raises(TypeError, match="float"):
        canonicalize_value(1.25)
    with pytest.raises(ValueError, match="naive datetime"):
        canonicalize_value(datetime(2026, 7, 13))
    with pytest.raises(TypeError, match="JSON object keys"):
        canonicalize_value({1: "unsafe"})


def test_canonical_table_rows_are_identical_under_shuffle_and_detect_duplicate_pk() -> None:
    spec = next(spec for spec in TABLE_SPECS if spec.table == "assets")
    rows_a = _asset_rows()
    rows_b = _asset_rows()
    random.Random(731).shuffle(rows_b)

    frozen_a = canonicalize_table_rows(spec, rows_a)
    frozen_b = canonicalize_table_rows(spec, rows_b)

    assert frozen_a == frozen_b
    assert frozen_a.rows[0]["asset_code"] == "CNY"
    assert len(frozen_a.ndjson_sha256) == 64

    with pytest.raises(ValueError, match="duplicate primary key"):
        canonicalize_table_rows(spec, [rows_a[0], rows_a[0]])


def test_frozen_rows_are_deeply_immutable_redacted_and_re_digestible() -> None:
    from backend.tools.frozen_v1_history.extract import TableSpec

    spec = TableSpec(
        table="synthetic",
        columns=("id", "payload"),
        primary_key=("id",),
        book_scoped=False,
    )
    source = [{"id": "one", "payload": {"secret": ["do-not-repr"]}}]
    frozen = canonicalize_table_rows(spec, source)
    source[0]["payload"]["secret"].append("mutated")  # type: ignore[index,union-attr]

    assert frozen.rows[0]["payload"]["secret"] == ("do-not-repr",)  # type: ignore[index]
    assert "do-not-repr" not in repr(frozen)
    assert verify_frozen_table_rows(spec, frozen) == frozen.ndjson_sha256
    with pytest.raises(TypeError):
        frozen.rows[0]["payload"]["new"] = "blocked"  # type: ignore[index]


def test_account_uuid_aggregate_protocol_is_hash_only_and_source_book_bound() -> None:
    count, digest = account_uuid_map_digest(
        (
            {"book_id": "source-book", "account_id": "legacy-b"},
            {"book_id": "source-book", "account_id": "legacy-a"},
        )
    )
    assert count == 2
    assert ACCOUNT_UUID_MAP_PROTOCOL == "frozen-v1-account-uuid-map/v1-naked-pairs"
    assert digest == "19879f80586cbd853580c71b0ce9e460d41d0bc4b7e2a138e5e210645b280ffa"
    assert digest != account_uuid_map_digest(
        ({"book_id": "target-book", "account_id": "legacy-a"},)
    )[1]
    assert len(ACCOUNT_UUID_MAP_SHA256) == 64


def _fixed_source_contract_row() -> dict[str, object]:
    return {
        "source_revision": "0019_posting_constraints",
        "attachments_relation": "attachments",
        "attachments_count": 0,
        **{
            f"{spec.table}_count": {
                "accounts": 121,
                "assets": 20,
                "categories": 37,
                "category_versions": 37,
                "classification_events": 43,
                "counterparties": 2,
                "investment_events": 6,
                "investment_valuations": 0,
                "ledger_books": 1,
                "postings": 284,
                "transaction_lines": 43,
                "transactions": 135,
            }[spec.table]
            for spec in TABLE_SPECS
        },
        **{
            f"{spec.table}_foreign_count": 0
            for spec in TABLE_SPECS
            if spec.book_scoped
        },
    }


def test_global_source_contract_accepts_the_existing_empty_attachments_table() -> None:
    counts = _fixed_source_contract_row()

    assert validate_source_contract(counts) == 0


@pytest.mark.parametrize(
    "mutation",
    (
        {"attachments_count": 1},
        {"attachments_count": False},
        {"attachments_relation": None},
        {"attachments_relation": "public.attachments"},
    ),
)
def test_global_source_contract_blocks_attachment_shape_or_content_drift(
    mutation: dict[str, object],
) -> None:
    counts = {**_fixed_source_contract_row(), **mutation}

    with pytest.raises(ValueError, match="global source contract"):
        validate_source_contract(counts)


def test_global_source_contract_blocks_rows_hidden_by_book_filter() -> None:
    counts = _fixed_source_contract_row()
    counts["postings_count"] = 285
    counts["postings_foreign_count"] = 1
    with pytest.raises(ValueError, match="global source contract"):
        validate_source_contract(counts)


def test_result_columns_must_be_exact_and_unique() -> None:
    spec = next(spec for spec in TABLE_SPECS if spec.table == "assets")

    validate_result_columns(spec, spec.columns)
    with pytest.raises(ValueError, match="duplicate result column"):
        validate_result_columns(spec, (*spec.columns[:-1], spec.columns[-2]))
    with pytest.raises(ValueError, match="do not match audited SQL"):
        validate_result_columns(spec, tuple(reversed(spec.columns)))


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://reader@localhost/source",
        "postgresql+psycopg2://reader@localhost/source",
        "sqlite:///source.db",
        "postgresql+psycopg://reader@localhost/",
    ],
)
def test_source_url_requires_exact_postgresql_psycopg_driver(url: str) -> None:
    with pytest.raises(ValueError, match="source URL"):
        validate_source_url(url)


def test_source_url_is_never_rendered_in_validation_errors() -> None:
    secret = "do-not-echo-password"
    with pytest.raises(ValueError) as exc_info:
        validate_source_url(f"postgresql+psycopg://reader:{secret}@localhost/")
    assert secret not in str(exc_info.value)


@pytest.mark.skipif(
    not os.getenv("TRACK_ANYWHERE_FROZEN_V1_SOURCE_URL"),
    reason="restored fixed V1 PostgreSQL is exercised on the DO rehearsal host",
)
def test_real_fixed_source_is_deterministic_across_runtime_schedules() -> None:
    expected = read_full_manifest(
        Path(os.environ["TRACK_ANYWHERE_FROZEN_V1_MANIFEST_A"])
    )
    run_a = extract_fixed_source(
        os.environ["TRACK_ANYWHERE_FROZEN_V1_SOURCE_URL"],
        expected_manifest=expected,
        batch_size=1,
        workers=1,
        shuffle_seed=17,
        table_order=tuple(spec.table for spec in TABLE_SPECS),
    )
    run_b = extract_fixed_source(
        os.environ["TRACK_ANYWHERE_FROZEN_V1_SOURCE_URL"],
        expected_manifest=expected,
        batch_size=37,
        workers=4,
        shuffle_seed=731,
        table_order=tuple(reversed([spec.table for spec in TABLE_SPECS])),
    )

    assert run_a.manifest == expected == run_b.manifest
    assert run_a.tables == run_b.tables
    assert account_uuid_map_digest(run_a.tables["accounts"].rows) == (
        121,
        ACCOUNT_UUID_MAP_SHA256,
    )
    report = inventory_rows(
        {name: table.rows for name, table in run_a.tables.items()},
        attachments_count=run_a.attachments_count,
    )
    validate_fixed_inventory(report)
