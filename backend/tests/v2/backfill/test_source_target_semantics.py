from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.tests.v2.postgres_factory import ProvisionedDatabase
from backend.tests.v2.backfill.credit_card_review_helpers import (
    approved_mechanical_review,
)
from backend.tools.backfill_v1.credit_card_review import credit_card_transaction_scope
from backend.tools.backfill_v1.extract import extract_canonical_rows
from backend.tools.backfill_v1.pipeline import (
    load_extracted_rows_to_target as _load_extracted_rows_to_target,
)
from backend.tools.backfill_v1.manifest import read_manifest
from backend.tools.backfill_v1.verify import (
    _SOURCE_COLUMNS,
    verify_backfill as _verify_backfill,
    verify_target,
)
from track_anywhere.application.credit_cards.record import (
    ChargeCreditCardCommand,
    CreditCardRefundSourceInvalid,
    RefundCreditCardCommand,
    execute_charge_credit_card,
    execute_refund_credit_card,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


Rows = dict[str, list[dict[str, object]]]
Mutation = Callable[[Rows], None]
_REVIEWS = {}


def load_extracted_rows_to_target(
    *,
    target_url: str,
    manifest,
    rows_by_table: Rows,
):
    review = (
        approved_mechanical_review(Path(), manifest=manifest, rows=rows_by_table)
        if credit_card_transaction_scope(rows_by_table)
        else None
    )
    _REVIEWS[manifest.snapshot_id] = review
    return _load_extracted_rows_to_target(
        target_url=target_url,
        manifest=manifest,
        rows_by_table=rows_by_table,
        credit_card_review=review,
    )


def verify_backfill(
    *,
    source_url: str,
    target_url: str,
    manifest_path: Path,
    output_path: Path | None = None,
):
    manifest = read_manifest(manifest_path)
    return _verify_backfill(
        source_url=source_url,
        target_url=target_url,
        manifest_path=manifest_path,
        output_path=output_path,
        credit_card_review=_REVIEWS.get(manifest.snapshot_id),
    )


def _rows() -> Rows:
    return {
        "accounts": [
            {
                "account_id": account_id,
                "book_id": "book-home",
                "currency": currency,
                "institution": None,
                "institution_type": None,
                "name": name,
                "subtype": account_subtype,
                "type": account_type,
                "version": 1,
            }
            for account_id, currency, name, account_type, account_subtype in (
                ("acc-cash", "CNY", "Cash", "asset", None),
                ("acc-cash-alt", "CNY", "Other cash", "asset", None),
                ("acc-expense", "CNY", "Expense", "expense", None),
                ("acc-usdt-wallet", "USDT", "USDT wallet", "asset", None),
                ("acc-usdt-equity", "USDT", "USDT equity", "equity", None),
                (
                    "acc-card",
                    "CNY",
                    "Legacy card",
                    "liability",
                    "legacy_credit_card",
                ),
            )
        ],
        "assets": [
            {
                "asset_code": "CNY",
                "display_scale": 2,
                "kind": "fiat",
                "name": "Renminbi",
                "scale": 2,
                "status": "active",
                "version": 1,
            },
            {
                "asset_code": "USDT",
                "display_scale": 6,
                "kind": "crypto",
                "name": "Tether",
                "scale": 6,
                "status": "active",
                "version": 1,
            },
        ],
        "categories": [
            {
                "book_id": "book-home",
                "category_id": category_id,
                "color": None,
                "icon": None,
                "kind": "expense",
                "level": 1,
                "name": name,
                "normalized_name": name.casefold(),
                "parent_id": None,
                "path_cache": name,
                "sort_order": 0,
                "status": "active",
                "version": 1,
            }
            for category_id, name in (
                ("cat-food", "Food"),
                ("cat-travel", "Travel"),
            )
        ],
        "category_versions": [
            {
                "book_id": "book-home",
                "category_id": category_id,
                "category_version_id": version_id,
                "change_reason": "create",
                "color": None,
                "icon": None,
                "name": name,
                "parent_id": None,
                "path": name,
                "valid_from": "2026-01-01T00:00:00.000000Z",
                "valid_to": None,
                "version": 1,
            }
            for category_id, version_id, name in (
                ("cat-food", "catv-food-1", "Food"),
                ("cat-travel", "catv-travel-1", "Travel"),
            )
        ],
        "classification_events": [],
        "counterparties": [
            {
                "counterparty_id": "counterparty-market",
                "book_id": "book-home",
                "slug": "market",
                "name": "Market",
                "kind": "merchant",
                "status": "active",
                "version": 1,
            }
        ],
        "investment_events": [],
        "investment_valuations": [],
        "ledger_books": [
            {
                "base_currency": "CNY",
                "book_id": "book-home",
                "created_by": "owner-old",
                "kind": "personal",
                "name": "Home",
                "settings": {},
                "status": "active",
                "template_key": None,
                "timezone": "Asia/Shanghai",
                "version": 1,
            }
        ],
        "postings": [
            _posting(1, "txn-lunch", "acc-cash", "credit", "12.34", "CNY", 0),
            _posting(2, "txn-lunch", "acc-expense", "debit", "12.34", "CNY", 1),
            _posting(
                3,
                "txn-usdt",
                "acc-usdt-wallet",
                "debit",
                "1.12345678",
                "USDT",
                0,
            ),
            _posting(
                4,
                "txn-usdt",
                "acc-usdt-equity",
                "credit",
                "1.12345678",
                "USDT",
                1,
            ),
            _posting(
                5,
                "txn-lunch-reversal",
                "acc-cash",
                "debit",
                "12.34",
                "CNY",
                0,
            ),
            _posting(
                6,
                "txn-lunch-reversal",
                "acc-expense",
                "credit",
                "12.34",
                "CNY",
                1,
            ),
        ],
        "transaction_lines": [
            {
                "amount": "12.34",
                "book_id": "book-home",
                "category_id": "cat-food",
                "category_path_snapshot": {"primary": "Food"},
                "category_version_id": "catv-food-1",
                "counterparty_id": "counterparty-market",
                "currency": "CNY",
                "line_id": "line-lunch",
                "line_type": "expense",
                "memo": "",
                "necessity": "unknown",
                "position": 0,
                "project_id": None,
                "reimbursement_status": "none",
                "transaction_id": "txn-lunch",
                "version": 1,
            }
        ],
        "transactions": [
            _transaction("txn-lunch", "2026-01-02T03:04:05.000000Z", "expense"),
            _transaction("txn-usdt", "2026-01-03T03:04:05.000000Z", "opening"),
            _transaction(
                "txn-lunch-reversal",
                "2026-01-04T03:04:05.000000Z",
                "correction",
                reverses="txn-lunch",
            ),
        ],
    }


def _typed_history_rows() -> Rows:
    rows = _rows()
    rows["transaction_lines"].append(
        {
            "amount": "0.1000",
            "book_id": "book-home",
            "category_id": None,
            "category_path_snapshot": None,
            "category_version_id": None,
            "counterparty_id": None,
            "currency": "USDT",
            "line_id": "line-usdt-fx",
            "line_type": "fx_exchange",
            "memo": "must stay private",
            "necessity": "unknown",
            "position": 0,
            "project_id": None,
            "reimbursement_status": "none",
            "transaction_id": "txn-usdt",
            "version": 2,
        }
    )
    rows["classification_events"] = [
        {
            "classification_event_id": "class-create-food",
            "book_id": "book-home",
            "event_type": "create",
            "source_category_id": "cat-food",
            "target_category_id": None,
            "affected_line_count": 0,
            "before": {},
            "after": {"category_id": "cat-food", "name": "Food"},
            "rollback": {},
            "created_by": "owner-source",
            "created_at": "2026-01-05T00:00:00.000000Z",
            "version": 3,
        }
    ]
    rows["investment_events"] = [
        {
            "account_id": "acc-usdt-wallet",
            "amount": "10.00",
            "book_id": "book-home",
            "currency": "USDT",
            "event_id": "inv-buy-history",
            "event_type": "buy",
            "memo": "must stay private",
            "nav": "2.0000",
            "occurred_at": "2026-01-06T00:00:00.000000Z",
            "transaction_id": None,
            "units": "1.25",
            "version": 4,
        }
    ]
    return rows


def _classification_history_rows() -> Rows:
    rows = _typed_history_rows()
    line = rows["transaction_lines"][0]
    line["category_id"] = "cat-travel"
    line["category_version_id"] = "catv-travel-1"
    line["category_path_snapshot"] = {"primary": "Travel"}
    before = {
        "category_id": "cat-food",
        "category_version_id": "catv-food-1",
        "category_path_snapshot": {"primary": "Food"},
        "line_id": "line-lunch",
        "transaction_id": "txn-lunch",
    }
    after = {
        "category_id": "cat-travel",
        "category_version_id": "catv-travel-1",
        "category_path_snapshot": {"primary": "Travel"},
        "line_id": "line-lunch",
        "transaction_id": "txn-lunch",
    }
    rows["classification_events"].extend(
        [
            {
                "classification_event_id": "class-food-to-travel",
                "book_id": "book-home",
                "event_type": "reclassify",
                "source_category_id": "cat-food",
                "target_category_id": "cat-travel",
                "affected_line_count": 1,
                "before": before,
                "after": after,
                "rollback": before,
                "created_by": "classifier-source",
                "created_at": "2026-01-07T00:00:00.000000Z",
                "version": 5,
            },
            {
                "classification_event_id": "class-travel-noop",
                "book_id": "book-home",
                "event_type": "reclassify",
                "source_category_id": "cat-travel",
                "target_category_id": "cat-travel",
                "affected_line_count": 1,
                "before": after,
                "after": after,
                "rollback": after,
                "created_by": "classifier-source",
                "created_at": "2026-01-08T00:00:00.000000Z",
                "version": 6,
            },
        ]
    )
    return rows


def _duplicate_classification_history_rows() -> Rows:
    rows = _classification_history_rows()
    first = next(
        event
        for event in rows["classification_events"]
        if event["classification_event_id"] == "class-food-to-travel"
    )
    duplicate = next(
        event
        for event in rows["classification_events"]
        if event["classification_event_id"] == "class-travel-noop"
    )
    duplicate["source_category_id"] = first["source_category_id"]
    duplicate["target_category_id"] = first["target_category_id"]
    duplicate["before"] = copy.deepcopy(first["before"])
    duplicate["after"] = copy.deepcopy(first["after"])
    duplicate["rollback"] = copy.deepcopy(first["rollback"])
    return rows


def _fx_history_rows() -> Rows:
    rows = _typed_history_rows()
    rows["accounts"].extend(
        [
            {
                "account_id": account_id,
                "book_id": "book-home",
                "currency": currency,
                "institution": None,
                "institution_type": None,
                "name": name,
                "subtype": None,
                "type": "system",
                "version": 1,
            }
            for account_id, currency, name in (
                ("acc-cny-fx", "CNY", "CNY FX clearing"),
                ("acc-usdt-fx", "USDT", "USDT FX clearing"),
            )
        ]
    )
    rows["transactions"].extend(
        [
            _transaction("txn-pure-fx", "2026-01-07T00:00:00.000000Z", "fx"),
            _transaction("txn-mixed-fx", "2026-01-08T00:00:00.000000Z", "fx"),
        ]
    )
    rows["postings"].extend(
        [
            _posting(10, "txn-pure-fx", "acc-cash", "credit", "700.00", "CNY", 0),
            _posting(11, "txn-pure-fx", "acc-cny-fx", "debit", "700.00", "CNY", 1),
            _posting(
                12,
                "txn-pure-fx",
                "acc-usdt-fx",
                "credit",
                "1.00000000",
                "USDT",
                2,
            ),
            _posting(
                13,
                "txn-pure-fx",
                "acc-usdt-wallet",
                "debit",
                "1.00000000",
                "USDT",
                3,
            ),
            _posting(20, "txn-mixed-fx", "acc-cash", "credit", "700.00", "CNY", 0),
            _posting(21, "txn-mixed-fx", "acc-cash-alt", "credit", "10.00", "CNY", 1),
            _posting(22, "txn-mixed-fx", "acc-cny-fx", "debit", "700.00", "CNY", 2),
            _posting(23, "txn-mixed-fx", "acc-expense", "debit", "10.00", "CNY", 3),
            _posting(
                24,
                "txn-mixed-fx",
                "acc-usdt-fx",
                "credit",
                "1.00000000",
                "USDT",
                4,
            ),
            _posting(
                25,
                "txn-mixed-fx",
                "acc-usdt-wallet",
                "debit",
                "1.00000000",
                "USDT",
                5,
            ),
        ]
    )
    for line_id, transaction_id, position, line_type, amount, currency in (
        ("line-pure-fx", "txn-pure-fx", 0, "fx_exchange", "1.0000", "USDT"),
        ("line-mixed-fx", "txn-mixed-fx", 0, "fx_exchange", "1.0000", "USDT"),
        ("line-mixed-fee", "txn-mixed-fx", 1, "fx_fee", "10.00", "CNY"),
    ):
        rows["transaction_lines"].append(
            {
                "amount": amount,
                "book_id": "book-home",
                "category_id": None,
                "category_path_snapshot": None,
                "category_version_id": None,
                "counterparty_id": None,
                "currency": currency,
                "line_id": line_id,
                "line_type": line_type,
                "memo": "must stay private",
                "necessity": "unknown",
                "position": position,
                "project_id": None,
                "reimbursement_status": "none",
                "transaction_id": transaction_id,
                "version": 1,
            }
        )
    return rows


def _fixed_history_shape_rows() -> Rows:
    rows = _fx_history_rows()
    for posting in rows["postings"]:
        if posting["transaction_id"] in {"txn-lunch", "txn-lunch-reversal"}:
            posting["amount"] = "100.00"
    for position in range(1, 38):
        rows["transaction_lines"].append(
            {
                "amount": "0.01",
                "book_id": "book-home",
                "category_id": "cat-food",
                "category_path_snapshot": {"primary": "Food"},
                "category_version_id": "catv-food-1",
                "counterparty_id": None,
                "currency": "CNY",
                "line_id": f"line-lunch-{position:02d}",
                "line_type": "expense",
                "memo": "",
                "necessity": "unknown",
                "position": position,
                "project_id": None,
                "reimbursement_status": "none",
                "transaction_id": "txn-lunch",
                "version": 1,
            }
        )
    rows["transaction_lines"].append(
        {
            "amount": "1.00",
            "book_id": "book-home",
            "category_id": None,
            "category_path_snapshot": None,
            "category_version_id": None,
            "counterparty_id": None,
            "currency": "CNY",
            "line_id": "line-pure-fx-cny",
            "line_type": "fx_exchange",
            "memo": "must stay private",
            "necessity": "unknown",
            "position": 1,
            "project_id": None,
            "reimbursement_status": "none",
            "transaction_id": "txn-pure-fx",
            "version": 1,
        }
    )
    rows["investment_events"].extend(
        {
            "account_id": "acc-usdt-wallet",
            "amount": amount,
            "book_id": "book-home",
            "currency": "USDT",
            "event_id": f"inv-history-{position}",
            "event_type": event_type,
            "memo": "must stay private",
            "nav": nav,
            "occurred_at": f"2026-01-{8 + position:02d}T00:00:00.000000Z",
            "transaction_id": None,
            "units": units,
            "version": 10 + position,
        }
        for position, (event_type, amount, units, nav) in enumerate(
            (
                ("buy", "1", None, None),
                ("sell", "2.0", "0.5", None),
                ("buy", "3.00", None, "1.250"),
                ("sell", "4.000", "0.1250", "2.0"),
                ("buy", "5.0000", "0.06250", "3.00000"),
            ),
            start=1,
        )
    )
    return rows


def _posting(
    source_id: int,
    transaction_id: str,
    account_id: str,
    side: str,
    amount: str,
    currency: str,
    position: int,
) -> dict[str, object]:
    return {
        "account_id": account_id,
        "amount": amount,
        "amount_semantics": "debit_credit",
        "book_id": "book-home",
        "currency": currency,
        "id": source_id,
        "position": position,
        "side": side,
        "transaction_id": transaction_id,
    }


def _transaction(
    transaction_id: str,
    occurred_at: str,
    purpose: str,
    *,
    reverses: str | None = None,
) -> dict[str, object]:
    return {
        "book_id": "book-home",
        "memo": transaction_id,
        "occurred_at": occurred_at,
        "purpose": purpose,
        "reversed_by": None,
        "reverses_transaction_id": reverses,
        "transaction_id": transaction_id,
        "version": 1,
    }


_INTEGER_COLUMNS = {
    "affected_line_count",
    "display_scale",
    "id",
    "level",
    "position",
    "scale",
    "sort_order",
    "version",
}
_JSON_COLUMNS = {
    "after",
    "before",
    "category_path_snapshot",
    "rollback",
    "settings",
}


def _seed_source(database: ProvisionedDatabase, rows: Rows) -> None:
    engine = create_engine(database.migrator_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'SET ROLE "{database.owner_role}"')
            connection.exec_driver_sql(
                "create table public.alembic_version "
                "(version_num varchar(64) primary key)"
            )
            connection.execute(
                text("insert into public.alembic_version values ('v1-synthetic')")
            )
            for table_name, columns in _SOURCE_COLUMNS.items():
                definitions = []
                for column in columns:
                    if column in _INTEGER_COLUMNS:
                        column_type = "bigint"
                    elif column in _JSON_COLUMNS:
                        column_type = "jsonb"
                    else:
                        column_type = "text"
                    definitions.append(f'"{column}" {column_type}')
                connection.exec_driver_sql(
                    f'create table public."{table_name}" ({", ".join(definitions)})'
                )
                for row in rows[table_name]:
                    placeholders = []
                    parameters: dict[str, object] = {}
                    for column in columns:
                        if column in _JSON_COLUMNS:
                            placeholders.append(f"cast(:{column} as jsonb)")
                            value = row[column]
                            parameters[column] = (
                                None
                                if value is None
                                else json.dumps(value, separators=(",", ":"))
                            )
                        else:
                            placeholders.append(f":{column}")
                            parameters[column] = row[column]
                    connection.execute(
                        text(
                            f'insert into public."{table_name}" '
                            f"({', '.join(columns)}) values "
                            f"({', '.join(placeholders)})"
                        ),
                        parameters,
                    )
            connection.exec_driver_sql(
                f"grant select on all tables in schema public "
                f'to "{database.runtime_role}"'
            )
            connection.exec_driver_sql("RESET ROLE")
    finally:
        engine.dispose()


def _account_mapping(rows: Rows) -> None:
    for posting in rows["postings"]:
        if posting["transaction_id"] in {"txn-lunch", "txn-lunch-reversal"} and (
            posting["account_id"] == "acc-cash"
        ):
            posting["account_id"] = "acc-cash-alt"


def _posting_side(rows: Rows) -> None:
    for posting in rows["postings"]:
        if posting["transaction_id"] in {"txn-lunch", "txn-lunch-reversal"}:
            posting["side"] = "debit" if posting["side"] == "credit" else "credit"


def _usdt_units(rows: Rows) -> None:
    for posting in rows["postings"]:
        if posting["transaction_id"] == "txn-usdt":
            posting["amount"] = "1.12345679"


def _effective_time(rows: Rows) -> None:
    next(
        transaction
        for transaction in rows["transactions"]
        if transaction["transaction_id"] == "txn-lunch"
    )["occurred_at"] = "2026-01-01T03:04:05.000000Z"


def _category_version(rows: Rows) -> None:
    line = rows["transaction_lines"][0]
    line["category_id"] = "cat-travel"
    line["category_version_id"] = "catv-travel-1"


def _counterparty(rows: Rows) -> None:
    rows["transaction_lines"][0]["counterparty_id"] = "counterparty-other"
    rows["counterparties"][0]["counterparty_id"] = "counterparty-other"


def _transaction_identity(rows: Rows) -> None:
    transaction = next(
        transaction
        for transaction in rows["transactions"]
        if transaction["transaction_id"] == "txn-usdt"
    )
    transaction["transaction_id"] = "txn-usdt-wrong"
    for posting in rows["postings"]:
        if posting["transaction_id"] == "txn-usdt":
            posting["transaction_id"] = "txn-usdt-wrong"


def _reversal_identity(rows: Rows) -> None:
    transaction = next(
        transaction
        for transaction in rows["transactions"]
        if transaction["transaction_id"] == "txn-lunch-reversal"
    )
    transaction["transaction_id"] = "txn-lunch-reversal-wrong"
    for posting in rows["postings"]:
        if posting["transaction_id"] == "txn-lunch-reversal":
            posting["transaction_id"] = "txn-lunch-reversal-wrong"


def _classification_chain_break(rows: Rows) -> None:
    event = next(
        event
        for event in rows["classification_events"]
        if event["classification_event_id"] == "class-travel-noop"
    )
    event["before"] = {
        "category_id": "cat-food",
        "category_version_id": "catv-food-1",
        "category_path_snapshot": {"primary": "Food"},
        "line_id": "line-lunch",
        "transaction_id": "txn-lunch",
    }
    event["after"] = copy.deepcopy(event["before"])


def _classification_final_drift(rows: Rows) -> None:
    line = next(
        line for line in rows["transaction_lines"] if line["line_id"] == "line-lunch"
    )
    line["category_id"] = "cat-other"
    line["category_version_id"] = "catv-other-1"
    line["category_path_snapshot"] = {"primary": "Other"}


def _investment_decimal_scale(rows: Rows) -> None:
    rows["investment_events"][0]["amount"] = "10.0"


def _classification_actor(rows: Rows) -> None:
    rows["classification_events"][0]["created_by"] = "owner-other"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        pytest.param(_account_mapping, "source_posting_mismatch", id="account"),
        pytest.param(_posting_side, "source_posting_mismatch", id="side"),
        pytest.param(_usdt_units, "source_posting_mismatch", id="usdt-units"),
        pytest.param(
            _effective_time,
            "source_effective_time_mismatch",
            id="effective-time",
        ),
        pytest.param(
            _category_version,
            "source_reporting_line_mismatch",
            id="category-version",
        ),
        pytest.param(
            _counterparty,
            "source_reporting_line_mismatch",
            id="counterparty",
        ),
        pytest.param(
            _transaction_identity,
            "source_transaction_missing",
            id="transaction-identity",
        ),
        pytest.param(
            _reversal_identity,
            "source_reversal_missing",
            id="reversal-identity",
        ),
    ],
)
def test_source_wrong_but_internally_consistent_target_fails_full_verification(
    postgres_database_factory,
    tmp_path: Path,
    mutation: Mutation,
    expected_code: str,
) -> None:
    source = postgres_database_factory.create(purpose="semantic-source", schema="empty")
    target = postgres_database_factory.create(purpose="semantic-target", schema="v2")
    source_rows = _rows()
    _seed_source(source, source_rows)
    manifest = extract_canonical_rows(
        rows_by_table=source_rows,
        output_dir=tmp_path / "extraction",
        dump_sha256="e" * 64,
        source_revision="v1-synthetic",
    )
    target_rows = copy.deepcopy(source_rows)
    mutation(target_rows)
    load_extracted_rows_to_target(
        target_url=target.runtime_url,
        manifest=manifest,
        rows_by_table=target_rows,
    )

    assert verify_target(target.runtime_url).status == "PASS"
    report = verify_backfill(
        source_url=source.runtime_url,
        target_url=target.runtime_url,
        manifest_path=tmp_path / "extraction" / "manifest.json",
    )

    assert report.status == "FAIL"
    assert expected_code in report.issue_codes


def test_source_semantics_accept_the_exact_deterministic_backfill(
    postgres_database_factory,
    tmp_path: Path,
) -> None:
    source = postgres_database_factory.create(purpose="semantic-source", schema="empty")
    target = postgres_database_factory.create(purpose="semantic-target", schema="v2")
    rows = _rows()
    _seed_source(source, rows)
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "extraction",
        dump_sha256="f" * 64,
        source_revision="v1-synthetic",
    )
    load_extracted_rows_to_target(
        target_url=target.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    report = verify_backfill(
        source_url=source.runtime_url,
        target_url=target.runtime_url,
        manifest_path=tmp_path / "extraction" / "manifest.json",
    )

    assert report.status == "PASS", "\n".join(
        f"{issue.code} {issue.scope} {issue.detail}" for issue in report.issues
    )
    assert report.issues == ()

    engine = create_engine(target.runtime_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "select account_subtype from accounts "
                        "where current_name = 'Legacy card'"
                    )
                )
                == "credit_card"
            )
    finally:
        engine.dispose()


def test_full_verifier_rejects_mutated_counterparty_receipt_fields(
    postgres_database_factory,
    tmp_path: Path,
) -> None:
    source = postgres_database_factory.create(
        purpose="receipt-fields-source", schema="empty"
    )
    target = postgres_database_factory.create(
        purpose="receipt-fields-target", schema="v2"
    )
    rows = _rows()
    _seed_source(source, rows)
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "receipt-fields-extraction",
        dump_sha256="6" * 64,
        source_revision="v1-synthetic",
    )
    manifest_path = tmp_path / "receipt-fields-extraction" / "manifest.json"
    load_extracted_rows_to_target(
        target_url=target.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )
    assert (
        verify_backfill(
            source_url=source.runtime_url,
            target_url=target.runtime_url,
            manifest_path=manifest_path,
        ).status
        == "PASS"
    )

    engine = create_engine(target.migrator_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(f'SET ROLE "{target.owner_role}"')
            original = dict(
                connection.execute(
                    text(
                        "select snapshot_id, source_table, source_primary_key, "
                        "canonical_source_key, book_id, source_hash, target_entity_id "
                        "from backfill_source_receipts "
                        "where source_table='counterparties'"
                    )
                )
                .mappings()
                .one()
            )

        field_mutations: tuple[tuple[str, object, str], ...] = (
            ("canonical_source_key", "tampered-counterparty-order", "mismatch"),
            ("book_id", None, "mismatch"),
            ("source_hash", b"x" * 32, "mismatch"),
            ("target_entity_id", uuid4(), "mismatch"),
            ("source_primary_key", '["counterparty-tampered"]', "identity"),
            ("source_table", "counterparties_tampered", "identity"),
            ("snapshot_id", f"sha256:{'9' * 64}", "identity"),
        )
        for field, mutated_value, expected_shape in field_mutations:
            with engine.begin() as connection:
                connection.exec_driver_sql(f'SET ROLE "{target.owner_role}"')
                connection.execute(
                    text(
                        f"update backfill_source_receipts set {field}=:value "
                        "where snapshot_id=:snapshot_id "
                        "and source_table=:source_table "
                        "and source_primary_key=:source_primary_key"
                    ),
                    {
                        "value": mutated_value,
                        "snapshot_id": original["snapshot_id"],
                        "source_table": original["source_table"],
                        "source_primary_key": original["source_primary_key"],
                    },
                )

            report = verify_backfill(
                source_url=source.runtime_url,
                target_url=target.runtime_url,
                manifest_path=manifest_path,
            )
            assert report.status == "FAIL"
            if expected_shape == "mismatch":
                assert any(
                    issue.code == "source_receipt_mismatch" and field in issue.detail
                    for issue in report.issues
                )
            else:
                assert "source_receipt_missing" in report.issue_codes

            mutated_identity = {
                "snapshot_id": (
                    mutated_value if field == "snapshot_id" else original["snapshot_id"]
                ),
                "source_table": (
                    mutated_value
                    if field == "source_table"
                    else original["source_table"]
                ),
                "source_primary_key": (
                    mutated_value
                    if field == "source_primary_key"
                    else original["source_primary_key"]
                ),
            }
            with engine.begin() as connection:
                connection.exec_driver_sql(f'SET ROLE "{target.owner_role}"')
                connection.execute(
                    text(
                        f"update backfill_source_receipts set {field}=:value "
                        "where snapshot_id=:snapshot_id "
                        "and source_table=:source_table "
                        "and source_primary_key=:source_primary_key"
                    ),
                    {
                        "value": original[field],
                        **mutated_identity,
                    },
                )
    finally:
        engine.dispose()


def test_credit_card_cutover_keeps_legacy_history_generic_and_new_writes_typed(
    postgres_database_factory,
    tmp_path: Path,
) -> None:
    source = postgres_database_factory.create(
        purpose="card-cutover-source", schema="empty"
    )
    target = postgres_database_factory.create(
        purpose="card-cutover-target", schema="v2"
    )
    rows = _rows()
    rows["transactions"].append(
        _transaction("txn-legacy-card", "2026-01-05T03:04:05.000000Z", "expense")
    )
    rows["postings"].extend(
        (
            _posting(
                7,
                "txn-legacy-card",
                "acc-card",
                "credit",
                "20.00",
                "CNY",
                0,
            ),
            _posting(
                8,
                "txn-legacy-card",
                "acc-expense",
                "debit",
                "20.00",
                "CNY",
                1,
            ),
        )
    )
    _seed_source(source, rows)
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "card-cutover-extraction",
        dump_sha256="c" * 64,
        source_revision="v1-synthetic",
    )
    load_extracted_rows_to_target(
        target_url=target.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(target.runtime_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with engine.connect() as connection:
            book_id, actor_subject_id = connection.execute(
                text(
                    "select member.book_id, member.user_id from book_members member "
                    "order by member.book_id, member.user_id limit 1"
                )
            ).one()
            card_account_id = connection.scalar(
                text(
                    "select account_id from accounts "
                    "where book_id=:book_id and account_subtype='credit_card'"
                ),
                {"book_id": book_id},
            )
            expense_account_id = connection.scalar(
                text(
                    "select account_id from accounts "
                    "where book_id=:book_id and current_name='Expense'"
                ),
                {"book_id": book_id},
            )
            legacy_transaction_id, legacy_event_type = connection.execute(
                text(
                    "select transaction.transaction_id, event.event_type "
                    "from journal_transactions transaction "
                    "join ledger_events event "
                    "  on event.book_id=transaction.book_id "
                    " and event.event_id=transaction.source_event_id "
                    "join journal_postings posting "
                    "  on posting.book_id=transaction.book_id "
                    " and posting.transaction_id=transaction.transaction_id "
                    "where transaction.book_id=:book_id "
                    "  and posting.account_id=:card_account_id "
                    "order by transaction.source_position desc limit 1"
                ),
                {"book_id": book_id, "card_account_id": card_account_id},
            ).one()
            assert (
                connection.scalar(text("select count(*) from credit_card_transactions"))
                == 0
            )
        assert legacy_event_type == "JournalTransactionPosted"
        assert card_account_id is not None
        assert expense_account_id is not None

        actor = CommandActor(subject_id=actor_subject_id)
        effective_at = datetime(2026, 1, 6, tzinfo=UTC)
        legacy_refund = RefundCreditCardCommand(
            book_id=book_id,
            command_id=uuid4(),
            transaction_id=uuid4(),
            expected_stream_version=0,
            card_account_id=card_account_id,
            original_transaction_id=legacy_transaction_id,
            asset_code="CNY",
            amount="1.00",
            effective_at=effective_at,
        )
        with pytest.raises(CreditCardRefundSourceInvalid):
            execute_refund_credit_card(
                legacy_refund,
                raw_key=f"card-cutover:{legacy_refund.command_id}",
                actor=actor,
                uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
            )

        charge = ChargeCreditCardCommand(
            book_id=book_id,
            command_id=uuid4(),
            transaction_id=uuid4(),
            expected_stream_version=0,
            card_account_id=card_account_id,
            expense_account_id=expense_account_id,
            asset_code="CNY",
            amount="10.00",
            effective_at=effective_at,
        )
        execute_charge_credit_card(
            charge,
            raw_key=f"card-cutover:{charge.command_id}",
            actor=actor,
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        )
        refund = RefundCreditCardCommand(
            book_id=book_id,
            command_id=uuid4(),
            transaction_id=uuid4(),
            expected_stream_version=0,
            card_account_id=card_account_id,
            original_transaction_id=charge.transaction_id,
            asset_code="CNY",
            amount="4.00",
            effective_at=effective_at + timedelta(minutes=1),
        )
        execute_refund_credit_card(
            refund,
            raw_key=f"card-cutover:{refund.command_id}",
            actor=actor,
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        )

        report = verify_target(target.runtime_url)
        assert report.status == "PASS", report.issues
        assert report.counts["credit_card_transactions"] == 2
    finally:
        engine.dispose()


def test_source_semantics_accept_all_three_typed_history_contracts(
    postgres_database_factory,
    tmp_path: Path,
) -> None:
    source = postgres_database_factory.create(
        purpose="typed-history-semantic-source", schema="empty"
    )
    target = postgres_database_factory.create(
        purpose="typed-history-semantic-target", schema="v2"
    )
    rows = _typed_history_rows()
    _seed_source(source, rows)
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "typed-history-extraction",
        dump_sha256="a" * 64,
        source_revision="v1-synthetic",
    )
    load_extracted_rows_to_target(
        target_url=target.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    report = verify_backfill(
        source_url=source.runtime_url,
        target_url=target.runtime_url,
        manifest_path=tmp_path / "typed-history-extraction" / "manifest.json",
    )

    assert report.status == "PASS", "\n".join(
        f"{issue.code} {issue.scope} {issue.detail}" for issue in report.issues
    )

    engine = create_engine(target.runtime_url)
    try:
        with engine.connect() as connection:
            investment_payload = connection.scalar(
                text(
                    "select payload from ledger_events "
                    "where event_type = 'HistoricalInvestmentActivityImported'"
                )
            )
            category_payload = connection.scalar(
                text(
                    "select payload from ledger_events "
                    "where event_type = 'HistoricalCategoryActivityImported'"
                )
            )
    finally:
        engine.dispose()
    assert investment_payload["cash_amount"] == {
        "unscaled_units": "1000",
        "scale": 2,
    }
    assert investment_payload["quantity"] == {
        "unscaled_units": "125",
        "scale": 2,
    }
    assert investment_payload["nav"] == {
        "unscaled_units": "20000",
        "scale": 4,
    }
    assert (
        category_payload["source_actor_hash"]
        == hashlib.sha256(
            b"track-anywhere:v2:backfill:source-actor:v1\x00owner-source"
        ).hexdigest()
    )
    assert (
        category_payload["source_actor_hash"]
        != hashlib.sha256(b"owner-source").hexdigest()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(_investment_decimal_scale, id="exact-decimal-scale"),
        pytest.param(_classification_actor, id="domain-separated-actor"),
    ],
)
def test_source_semantics_reject_history_payload_mutation(
    postgres_database_factory,
    tmp_path: Path,
    mutation: Mutation,
) -> None:
    source = postgres_database_factory.create(
        purpose="history-payload-mutation-source", schema="empty"
    )
    target = postgres_database_factory.create(
        purpose="history-payload-mutation-target", schema="v2"
    )
    target_rows = _typed_history_rows()
    source_rows = copy.deepcopy(target_rows)
    mutation(source_rows)
    _seed_source(source, source_rows)
    manifest = extract_canonical_rows(
        rows_by_table=source_rows,
        output_dir=tmp_path / "history-payload-mutation-extraction",
        dump_sha256="8" * 64,
        source_revision="v1-synthetic",
    )
    load_extracted_rows_to_target(
        target_url=target.runtime_url,
        manifest=manifest,
        rows_by_table=target_rows,
    )

    assert verify_target(target.runtime_url).status == "PASS"
    report = verify_backfill(
        source_url=source.runtime_url,
        target_url=target.runtime_url,
        manifest_path=tmp_path
        / "history-payload-mutation-extraction"
        / "manifest.json",
    )

    assert report.status == "FAIL"
    assert any(
        issue.code == "source_event_mismatch" and "payload" in issue.detail
        for issue in report.issues
    )


def test_source_semantics_accept_full_classification_revision_history(
    postgres_database_factory,
    tmp_path: Path,
) -> None:
    source = postgres_database_factory.create(
        purpose="classification-semantic-source", schema="empty"
    )
    target = postgres_database_factory.create(
        purpose="classification-semantic-target", schema="v2"
    )
    rows = _classification_history_rows()
    _seed_source(source, rows)
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "classification-semantic-extraction",
        dump_sha256="b" * 64,
        source_revision="v1-synthetic",
    )
    load_extracted_rows_to_target(
        target_url=target.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    report = verify_backfill(
        source_url=source.runtime_url,
        target_url=target.runtime_url,
        manifest_path=tmp_path / "classification-semantic-extraction" / "manifest.json",
    )

    assert report.status == "PASS", "\n".join(
        f"{issue.code} {issue.scope} {issue.detail}" for issue in report.issues
    )


def test_source_semantics_accept_adjacent_duplicate_classification_transition(
    postgres_database_factory,
    tmp_path: Path,
) -> None:
    source = postgres_database_factory.create(
        purpose="duplicate-classification-source", schema="empty"
    )
    target = postgres_database_factory.create(
        purpose="duplicate-classification-target", schema="v2"
    )
    rows = _duplicate_classification_history_rows()
    _seed_source(source, rows)
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "duplicate-classification-extraction",
        dump_sha256="7" * 64,
        source_revision="v1-synthetic",
    )
    load_extracted_rows_to_target(
        target_url=target.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    report = verify_backfill(
        source_url=source.runtime_url,
        target_url=target.runtime_url,
        manifest_path=tmp_path
        / "duplicate-classification-extraction"
        / "manifest.json",
    )

    assert report.status == "PASS", "\n".join(
        f"{issue.code} {issue.scope} {issue.detail}" for issue in report.issues
    )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        pytest.param(
            _classification_chain_break,
            "source_classification_chain_mismatch",
            id="audit-chain-break",
        ),
        pytest.param(
            _classification_final_drift,
            "source_classification_final_state_mismatch",
            id="audit-final-projection-drift",
        ),
    ],
)
def test_source_semantics_reject_classification_audit_drift(
    postgres_database_factory,
    tmp_path: Path,
    mutation: Mutation,
    expected_code: str,
) -> None:
    source = postgres_database_factory.create(
        purpose="classification-drift-source", schema="empty"
    )
    target = postgres_database_factory.create(
        purpose="classification-drift-target", schema="v2"
    )
    target_rows = _classification_history_rows()
    source_rows = copy.deepcopy(target_rows)
    mutation(source_rows)
    _seed_source(source, source_rows)
    manifest = extract_canonical_rows(
        rows_by_table=source_rows,
        output_dir=tmp_path / "classification-drift-extraction",
        dump_sha256="d" * 64,
        source_revision="v1-synthetic",
    )
    load_extracted_rows_to_target(
        target_url=target.runtime_url,
        manifest=manifest,
        rows_by_table=target_rows,
    )

    assert verify_target(target.runtime_url).status == "PASS"
    report = verify_backfill(
        source_url=source.runtime_url,
        target_url=target.runtime_url,
        manifest_path=tmp_path / "classification-drift-extraction" / "manifest.json",
    )

    assert report.status == "FAIL"
    assert expected_code in report.issue_codes


def test_source_semantics_distinguish_pure_and_mixed_fx(
    postgres_database_factory,
    tmp_path: Path,
) -> None:
    source = postgres_database_factory.create(
        purpose="fx-semantic-source", schema="empty"
    )
    target = postgres_database_factory.create(purpose="fx-semantic-target", schema="v2")
    rows = _fx_history_rows()
    _seed_source(source, rows)
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "fx-semantic-extraction",
        dump_sha256="c" * 64,
        source_revision="v1-synthetic",
    )
    load_extracted_rows_to_target(
        target_url=target.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    report = verify_backfill(
        source_url=source.runtime_url,
        target_url=target.runtime_url,
        manifest_path=tmp_path / "fx-semantic-extraction" / "manifest.json",
    )

    assert report.status == "PASS", "\n".join(
        f"{issue.code} {issue.scope} {issue.detail}" for issue in report.issues
    )


def test_source_semantics_preserve_fixed_historical_shape(
    postgres_database_factory,
    tmp_path: Path,
) -> None:
    source = postgres_database_factory.create(
        purpose="fixed-history-shape-source", schema="empty"
    )
    target = postgres_database_factory.create(
        purpose="fixed-history-shape-target", schema="v2"
    )
    rows = _fixed_history_shape_rows()
    assert len(rows["transaction_lines"]) == 43
    assert (
        sum(line["category_id"] is not None for line in rows["transaction_lines"]) == 38
    )
    assert sum(line["category_id"] is None for line in rows["transaction_lines"]) == 5
    assert len(rows["investment_events"]) == 6
    assert rows["investment_valuations"] == []

    _seed_source(source, rows)
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "fixed-history-shape-extraction",
        dump_sha256="9" * 64,
        source_revision="v1-synthetic",
    )
    load_extracted_rows_to_target(
        target_url=target.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(target.runtime_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("select count(*) from reporting_lines")) == 38
            event_counts = {
                str(event_type): int(count)
                for event_type, count in connection.execute(
                    text(
                        "select event_type, count(*) from ledger_events "
                        "group by event_type"
                    )
                ).tuples()
            }
            assert event_counts["HistoricalReportingLineImported"] == 5
            assert event_counts["HistoricalInvestmentActivityImported"] == 6
            assert connection.scalar(text("select count(*) from investment_lots")) == 0
    finally:
        engine.dispose()

    report = verify_backfill(
        source_url=source.runtime_url,
        target_url=target.runtime_url,
        manifest_path=tmp_path / "fixed-history-shape-extraction" / "manifest.json",
    )

    assert report.status == "PASS", "\n".join(
        f"{issue.code} {issue.scope} {issue.detail}" for issue in report.issues
    )
