from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text

from backend.tools.backfill_v1.__main__ import _parser
from backend.tools.backfill_v1.extract import extract_canonical_rows
from backend.tools.backfill_v1.pipeline import (
    BackfillMappingError,
    load_extracted_rows_to_target,
)
from backend.tools.backfill_v1.namespaces import deterministic_uuid
from backend.tools.backfill_v1.verify import verify_target
from backend.tests.v2.backfill.credit_card_review_helpers import (
    approved_mechanical_review,
)


def _synthetic_rows() -> dict[str, list[dict[str, object]]]:
    return {
        "accounts": [
            {
                "account_id": "acc-cash",
                "book_id": "book-home",
                "currency": "CNY",
                "institution": None,
                "institution_type": None,
                "name": "Cash",
                "subtype": None,
                "type": "asset",
                "version": 1,
            },
            {
                "account_id": "acc-expense",
                "book_id": "book-home",
                "currency": "CNY",
                "institution": None,
                "institution_type": None,
                "name": "Expense",
                "subtype": None,
                "type": "expense",
                "version": 1,
            },
            {
                "account_id": "acc-usdt-wallet",
                "book_id": "book-home",
                "currency": "USDT",
                "institution": None,
                "institution_type": None,
                "name": "USDT wallet",
                "subtype": None,
                "type": "asset",
                "version": 1,
            },
            {
                "account_id": "acc-usdt-equity",
                "book_id": "book-home",
                "currency": "USDT",
                "institution": None,
                "institution_type": None,
                "name": "USDT equity",
                "subtype": None,
                "type": "equity",
                "version": 1,
            },
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
                "category_id": "cat-food",
                "color": None,
                "icon": None,
                "kind": "expense",
                "level": 1,
                "name": "Food",
                "normalized_name": "food",
                "parent_id": None,
                "path_cache": "Food",
                "sort_order": 0,
                "status": "active",
                "version": 1,
            }
        ],
        "category_versions": [
            {
                "book_id": "book-home",
                "category_id": "cat-food",
                "category_version_id": "catv-food-1",
                "change_reason": "create",
                "color": None,
                "icon": None,
                "name": "Food",
                "parent_id": None,
                "path": "Food",
                "valid_from": "2026-01-01T00:00:00.000000Z",
                "valid_to": None,
                "version": 1,
            }
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
            {
                "account_id": "acc-cash",
                "amount": "12.34",
                "amount_semantics": "debit_credit",
                "book_id": "book-home",
                "currency": "CNY",
                "id": 1,
                "position": 0,
                "side": "credit",
                "transaction_id": "txn-lunch",
            },
            {
                "account_id": "acc-usdt-wallet",
                "amount": "1.12345678",
                "amount_semantics": "debit_credit",
                "book_id": "book-home",
                "currency": "USDT",
                "id": 3,
                "position": 0,
                "side": "debit",
                "transaction_id": "txn-usdt",
            },
            {
                "account_id": "acc-usdt-equity",
                "amount": "1.12345678",
                "amount_semantics": "debit_credit",
                "book_id": "book-home",
                "currency": "USDT",
                "id": 4,
                "position": 1,
                "side": "credit",
                "transaction_id": "txn-usdt",
            },
            {
                "account_id": "acc-expense",
                "amount": "12.34",
                "amount_semantics": "debit_credit",
                "book_id": "book-home",
                "currency": "CNY",
                "id": 2,
                "position": 1,
                "side": "debit",
                "transaction_id": "txn-lunch",
            },
        ],
        "transaction_lines": [
            {
                "amount": "12.34",
                "book_id": "book-home",
                "category_id": "cat-food",
                "category_path_snapshot": {"primary": "Food"},
                "category_version_id": None,
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
            {
                "book_id": "book-home",
                "memo": "Lunch",
                "occurred_at": "2026-01-02T03:04:05.000000Z",
                "purpose": "expense",
                "reversed_by": None,
                "reverses_transaction_id": None,
                "transaction_id": "txn-lunch",
                "version": 1,
            },
            {
                "book_id": "book-home",
                "memo": "Historical precision",
                "occurred_at": "2026-01-03T03:04:05.000000Z",
                "purpose": "opening",
                "reversed_by": None,
                "reverses_transaction_id": None,
                "transaction_id": "txn-usdt",
                "version": 1,
            },
        ],
    }


def _add_food_to_work_reclassification(
    rows: dict[str, list[dict[str, object]]],
    *,
    created_at: str,
) -> None:
    rows["categories"].append(
        {
            **rows["categories"][0],
            "category_id": "cat-work",
            "name": "Work",
            "normalized_name": "work",
            "path_cache": "Work",
        }
    )
    rows["category_versions"].append(
        {
            **rows["category_versions"][0],
            "category_id": "cat-work",
            "category_version_id": "catv-work-1",
            "name": "Work",
            "path": "Work",
        }
    )
    line = rows["transaction_lines"][0]
    line["category_id"] = "cat-work"
    line["category_version_id"] = "catv-work-1"
    line["category_path_snapshot"] = {"primary": "Work"}
    rows["classification_events"] = [
        {
            "classification_event_id": "aaa-reclass",
            "book_id": "book-home",
            "event_type": "reclassify",
            "source_category_id": "cat-food",
            "target_category_id": "cat-work",
            "affected_line_count": 1,
            "before": {
                "transaction_id": "txn-lunch",
                "line_id": "line-lunch",
                "category_id": "cat-food",
                "category_version_id": "catv-food-1",
                "category_path_snapshot": {"primary": "Food"},
            },
            "after": {
                "transaction_id": "txn-lunch",
                "line_id": "line-lunch",
                "category_id": "cat-work",
                "category_version_id": "catv-work-1",
                "category_path_snapshot": {"primary": "Work"},
            },
            "rollback": {},
            "created_by": "owner-source",
            "created_at": created_at,
            "version": 1,
        }
    ]


def test_run_cli_accepts_the_rehearsal_contract(tmp_path: Path) -> None:
    args = _parser().parse_args(
        [
            "run",
            "--source-url",
            "postgresql+psycopg://reader:x@127.0.0.1/source",
            "--target-url",
            "postgresql+psycopg://writer:x@127.0.0.1/target",
            "--dump",
            str(tmp_path / "snapshot.dump"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--batch-size",
            "13",
            "--workers",
            "4",
            "--shuffle-seed",
            "731",
            "--output-dir",
            str(tmp_path / "report"),
        ]
    )

    assert args.command == "run"
    assert (args.batch_size, args.workers, args.shuffle_seed) == (13, 4, 731)


def test_synthetic_pg17_load_is_atomic_resumable_and_sealed(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    source_usdt = next(row for row in rows["assets"] if row["asset_code"] == "USDT")
    assert source_usdt["scale"] == 6
    extraction = tmp_path / "extraction"
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=extraction,
        dump_sha256="a" * 64,
        source_revision="v1-synthetic",
    )

    first = load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )
    replay = load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    assert first.applied_receipts == sum(len(values) for values in rows.values())
    assert replay.applied_receipts == 0
    assert replay.replayed_receipts == first.applied_receipts
    assert first.seal.verification_payload() == replay.seal.verification_payload()
    assert source_usdt["scale"] == 6

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            counts = {
                table: int(connection.scalar(text(f"select count(*) from {table}")))
                for table in (
                    "books",
                    "assets",
                    "accounts",
                    "categories",
                    "journal_transactions",
                    "journal_postings",
                    "reporting_lines",
                    "ledger_events",
                    "backfill_seals",
                )
            }
            usdt = connection.execute(
                text(
                    "select p.units, a.ledger_scale, a.input_scale "
                    "from journal_postings p "
                    "join assets a on a.asset_code=p.asset_code "
                    "where p.asset_code='USDT' and p.side='debit'"
                )
            ).one()
            counterparty_id = connection.scalar(
                text(
                    "select counterparty_id from reporting_lines "
                    "where transaction_id = :transaction_id"
                ),
                {
                    "transaction_id": deterministic_uuid(
                        "transaction",
                        manifest.snapshot_id,
                        "book-home",
                        "txn-lunch",
                    )
                },
            )
    finally:
        engine.dispose()

    assert counts == {
        "accounts": 4,
        "assets": 2,
        "backfill_seals": 1,
        "books": 1,
        "categories": 1,
        "journal_postings": 4,
        "journal_transactions": 2,
        "ledger_events": 3,
        "reporting_lines": 1,
    }
    assert (int(usdt.units), usdt.ledger_scale, usdt.input_scale) == (
        112_345_678,
        8,
        6,
    )
    assert counterparty_id == deterministic_uuid(
        "counterparty", "book-home", "counterparty-market"
    )
    independent = verify_target(migrated_postgres_database.runtime_url)
    assert (independent.status, independent.issues) == ("PASS", ())


def test_trusted_v1_backfill_preserves_generic_credit_card_history(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    card = rows["accounts"][0]
    card["type"] = "liability"
    card["subtype"] = "credit_card"
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "credit-card-history-extraction",
        dump_sha256="9" * 64,
        source_revision="v1-synthetic",
    )

    result = load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
        credit_card_review=approved_mechanical_review(
            tmp_path, manifest=manifest, rows=rows
        ),
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            imported = connection.execute(
                text(
                    "select transaction.transaction_kind, event.event_type, "
                    "account.account_type, account.account_subtype "
                    "from journal_transactions transaction "
                    "join ledger_events event "
                    "  on event.book_id=transaction.book_id "
                    " and event.event_id=transaction.source_event_id "
                    "join journal_postings posting "
                    "  on posting.book_id=transaction.book_id "
                    " and posting.transaction_id=transaction.transaction_id "
                    "join accounts account "
                    "  on account.book_id=posting.book_id "
                    " and account.account_id=posting.account_id "
                    "where account.account_subtype='credit_card'"
                )
            ).one()
            typed_count = int(
                connection.scalar(text("select count(*) from credit_card_transactions"))
            )
    finally:
        engine.dispose()

    assert result.seal.snapshot_id == manifest.snapshot_id
    assert imported == (
        "standard",
        "JournalTransactionPosted",
        "liability",
        "credit_card",
    )
    assert typed_count == 0
    independent = verify_target(migrated_postgres_database.runtime_url)
    assert (independent.status, independent.issues) == ("PASS", ())


def test_reviewed_card_redirect_neutralization_and_alias_close_are_atomic(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    old_card = rows["accounts"][0]
    old_card["type"] = "liability"
    old_card["subtype"] = "legacy_credit_card"
    rows["accounts"].append(
        {
            **old_card,
            "account_id": "acc-card-shared",
            "name": "Shared card",
            "subtype": "credit_card",
        }
    )
    rows["transactions"].append(
        {
            **rows["transactions"][0],
            "memo": "legacy-only sign compensation",
            "occurred_at": "2026-01-04T03:04:05.000000Z",
            "purpose": "correction",
            "transaction_id": "txn-card-compensation",
        }
    )
    rows["postings"].extend(
        [
            {
                **rows["postings"][0],
                "account_id": "acc-card-shared",
                "amount": "24.68",
                "id": 5,
                "side": "credit",
                "transaction_id": "txn-card-compensation",
            },
            {
                **rows["postings"][3],
                "account_id": "acc-expense",
                "amount": "24.68",
                "currency": "CNY",
                "id": 6,
                "side": "debit",
                "transaction_id": "txn-card-compensation",
            },
        ]
    )
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "reviewed-card-extraction",
        dump_sha256="7" * 64,
        source_revision="v1-synthetic",
    )
    review = approved_mechanical_review(
        tmp_path,
        manifest=manifest,
        rows=rows,
        posting_overrides={"1": ("acc-card-shared", "credit")},
        neutralized_transaction_ids=frozenset({"txn-card-compensation"}),
        closed_account_ids=frozenset({"acc-cash"}),
    )

    result = load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
        credit_card_review=review,
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            statuses = dict(
                connection.execute(
                    text(
                        "select current_name, status from accounts "
                        "where account_subtype='credit_card'"
                    )
                ).tuples().all()
            )
            shared_raw = int(
                connection.scalar(
                    text(
                        "select balance.balance_units from account_balances balance "
                        "join accounts account on account.book_id=balance.book_id "
                        "and account.account_id=balance.account_id "
                        "where account.current_name='Shared card'"
                    )
                )
            )
            counts = {
                table: int(connection.scalar(text(f"select count(*) from {table}")))
                for table in (
                    "backfill_review_contracts",
                    "journal_transactions",
                    "transaction_reversals",
                )
            }
    finally:
        engine.dispose()

    assert result.seal.snapshot_id == manifest.snapshot_id
    assert statuses == {"Cash": "closed", "Shared card": "active"}
    assert shared_raw == -1234
    assert counts == {
        "backfill_review_contracts": 1,
        "journal_transactions": 4,
        "transaction_reversals": 1,
    }
    assert verify_target(migrated_postgres_database.runtime_url).status == "PASS"


def test_card_snapshot_without_review_fails_before_target_write(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    rows["accounts"][0]["type"] = "liability"
    rows["accounts"][0]["subtype"] = "credit_card"
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "missing-card-review-extraction",
        dump_sha256="8" * 64,
        source_revision="v1-synthetic",
    )

    with pytest.raises(ValueError, match="semantic review is required"):
        load_extracted_rows_to_target(
            target_url=migrated_postgres_database.runtime_url,
            manifest=manifest,
            rows_by_table=rows,
        )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            assert int(connection.scalar(text("select count(*) from books"))) == 0
            assert (
                int(
                    connection.scalar(
                        text("select count(*) from backfill_review_contracts")
                    )
                )
                == 0
            )
    finally:
        engine.dispose()


def test_v1_investment_activity_is_imported_exactly_without_fabricating_a_v2_lot(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    rows["investment_events"] = [
        {
            "account_id": "acc-usdt-wallet",
            "amount": "10.00",
            "book_id": "book-home",
            "currency": "USDT",
            "event_id": "inv-buy-no-quantity",
            "event_type": "buy",
            "memo": "must not enter the immutable payload",
            "nav": None,
            "occurred_at": "2026-01-04T00:00:00.000000Z",
            "transaction_id": None,
            "units": None,
            "version": 1,
        },
        {
            "account_id": "acc-usdt-wallet",
            "amount": "2.50",
            "book_id": "book-home",
            "currency": "USDT",
            "event_id": "inv-sell-with-quantity",
            "event_type": "sell",
            "memo": "also private",
            "nav": "2.0000",
            "occurred_at": "2026-01-05T00:00:00.000000Z",
            "transaction_id": None,
            "units": "1.25",
            "version": 2,
        },
    ]
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "historical-investment-extraction",
        dump_sha256="b" * 64,
        source_revision="v1-synthetic",
    )

    load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            imported = (
                connection.execute(
                    text(
                        "select payload from ledger_events "
                        "where event_type='HistoricalInvestmentActivityImported' "
                        "order by effective_at, event_id"
                    )
                )
                .scalars()
                .all()
            )
            lot_count = connection.scalar(text("select count(*) from investment_lots"))
    finally:
        engine.dispose()

    assert imported == [
        {
            "activity_kind": "buy",
            "cash_amount": {"scale": 2, "unscaled_units": "1000"},
            "nav": None,
            "quantity": None,
            "settlement_asset_code": "USDT",
            "source_account_id": "acc-usdt-wallet",
            "source_event_id": "inv-buy-no-quantity",
            "source_row_hash": imported[0]["source_row_hash"],
            "source_version": 1,
        },
        {
            "activity_kind": "sell",
            "cash_amount": {"scale": 2, "unscaled_units": "250"},
            "nav": {"scale": 4, "unscaled_units": "20000"},
            "quantity": {"scale": 2, "unscaled_units": "125"},
            "settlement_asset_code": "USDT",
            "source_account_id": "acc-usdt-wallet",
            "source_event_id": "inv-sell-with-quantity",
            "source_row_hash": imported[1]["source_row_hash"],
            "source_version": 2,
        },
    ]
    assert all(len(payload["source_row_hash"]) == 64 for payload in imported)
    assert all("memo" not in payload for payload in imported)
    assert lot_count == 0


def test_uncategorized_fx_lines_use_typed_history_without_category_fabrication(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    rows["transaction_lines"].extend(
        [
            {
                "amount": "1.0000",
                "book_id": "book-home",
                "category_id": None,
                "category_path_snapshot": None,
                "category_version_id": None,
                "counterparty_id": None,
                "currency": "USDT",
                "line_id": "line-fx-exchange",
                "line_type": "fx_exchange",
                "memo": "private exchange detail",
                "necessity": "unknown",
                "position": 0,
                "project_id": None,
                "reimbursement_status": "none",
                "transaction_id": "txn-usdt",
                "version": 1,
            },
            {
                "amount": "0.12345678",
                "book_id": "book-home",
                "category_id": None,
                "category_path_snapshot": None,
                "category_version_id": None,
                "counterparty_id": None,
                "currency": "USDT",
                "line_id": "line-fx-fee",
                "line_type": "fx_fee",
                "memo": "private fee detail",
                "necessity": "unknown",
                "position": 1,
                "project_id": None,
                "reimbursement_status": "none",
                "transaction_id": "txn-usdt",
                "version": 2,
            },
        ]
    )
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "historical-fx-extraction",
        dump_sha256="c" * 64,
        source_revision="v1-synthetic",
    )

    load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            imported_rows = connection.execute(
                text(
                    "select payload, causation_event_id from ledger_events "
                    "where event_type='HistoricalReportingLineImported' "
                    "order by payload->>'source_line_id'"
                )
            ).all()
            imported = [row.payload for row in imported_rows]
            parent_journal_event_id = connection.scalar(
                text(
                    "select source_event_id from journal_transactions "
                    "where transaction_id = "
                    "cast(:transaction_id as uuid)"
                ),
                {
                    "transaction_id": imported[0]["transaction_id"],
                },
            )
            projected_count = connection.scalar(
                text("select count(*) from reporting_lines")
            )
    finally:
        engine.dispose()

    assert [payload["source_line_id"] for payload in imported] == [
        "line-fx-exchange",
        "line-fx-fee",
    ]
    assert [payload["line_kind"] for payload in imported] == [
        "fx_exchange",
        "fx_fee",
    ]
    assert [payload["amount"] for payload in imported] == [
        {"scale": 4, "unscaled_units": "10000"},
        {"scale": 8, "unscaled_units": "12345678"},
    ]
    assert all(payload["asset_code"] == "USDT" for payload in imported)
    assert all(len(payload["source_row_hash"]) == 64 for payload in imported)
    assert all("memo" not in payload for payload in imported)
    assert parent_journal_event_id is not None
    assert all(
        row.causation_event_id == parent_journal_event_id for row in imported_rows
    )
    assert projected_count == 1


def test_classification_history_replays_every_real_revision_and_audit_event(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    rows["categories"].append(
        {
            "book_id": "book-home",
            "category_id": "cat-drink",
            "color": None,
            "icon": None,
            "kind": "expense",
            "level": 1,
            "name": "Drink",
            "normalized_name": "drink",
            "parent_id": None,
            "path_cache": "Drink",
            "sort_order": 1,
            "status": "active",
            "version": 1,
        }
    )
    rows["category_versions"].append(
        {
            "book_id": "book-home",
            "category_id": "cat-drink",
            "category_version_id": "catv-drink-1",
            "change_reason": "create",
            "color": None,
            "icon": None,
            "name": "Drink",
            "parent_id": None,
            "path": "Drink",
            "valid_from": "2026-01-01T00:01:00.000000Z",
            "valid_to": None,
            "version": 1,
        }
    )
    current_line = rows["transaction_lines"][0]
    current_line["category_id"] = "cat-drink"
    current_line["category_version_id"] = "catv-drink-1"
    current_line["category_path_snapshot"] = {"primary": "Drink"}

    before = {
        "category_id": "cat-food",
        "category_version_id": "catv-food-1",
        "category_path_snapshot": {"primary": "Food"},
        "line_id": "line-lunch",
        "transaction_id": "txn-lunch",
    }
    after = {
        "category_id": "cat-drink",
        "category_version_id": "catv-drink-1",
        "category_path_snapshot": {"primary": "Drink"},
        "line_id": "line-lunch",
        "transaction_id": "txn-lunch",
        "memo": "must stay outside immutable payloads",
    }
    rows["classification_events"] = [
        {
            "classification_event_id": "class-create-drink",
            "book_id": "book-home",
            "event_type": "create",
            "source_category_id": "cat-drink",
            "target_category_id": None,
            "affected_line_count": 0,
            "before": {},
            "after": {"category_id": "cat-drink", "name": "Drink"},
            "rollback": {},
            "created_by": "owner-source",
            "created_at": "2026-01-01T00:02:00.000000Z",
            "version": 1,
        },
        {
            "classification_event_id": "class-reassign-drink",
            "book_id": "book-home",
            "event_type": "reclassify",
            "source_category_id": "cat-food",
            "target_category_id": "cat-drink",
            "affected_line_count": 1,
            "before": before,
            "after": after,
            "rollback": {},
            "created_by": "machine-source",
            "created_at": "2026-01-02T04:00:00.000000Z",
            "version": 1,
        },
        {
            "classification_event_id": "class-reassign-drink-noop",
            "book_id": "book-home",
            "event_type": "reclassify",
            "source_category_id": "cat-drink",
            "target_category_id": "cat-drink",
            "affected_line_count": 1,
            "before": after,
            "after": after,
            "rollback": {},
            "created_by": "machine-source",
            "created_at": "2026-01-02T05:00:00.000000Z",
            "version": 1,
        },
    ]
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "classification-history-extraction",
        dump_sha256="d" * 64,
        source_revision="v1-synthetic",
    )

    load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            reporting = connection.execute(
                text(
                    "select event_id, payload from ledger_events "
                    "where event_type='ReportingLinesAssigned' "
                    "order by book_position"
                )
            ).all()
            historical = connection.execute(
                text(
                    "select payload, causation_event_id from ledger_events "
                    "where event_type='HistoricalCategoryActivityImported' "
                    "order by book_position"
                )
            ).all()
            final_projection = connection.execute(
                text(
                    "select classification_revision, dimension_id, catalog_id "
                    "from reporting_lines order by classification_revision desc "
                    "limit 1"
                )
            ).one()
    finally:
        engine.dispose()

    assert [row.payload["classification_revision"] for row in reporting] == [1, 2, 3]
    dimension_ids = [row.payload["lines"][0]["dimension_id"] for row in reporting]
    catalog_ids = [row.payload["lines"][0]["catalog_id"] for row in reporting]
    line_version_ids = [row.payload["lines"][0]["line_version_id"] for row in reporting]
    assert dimension_ids[0] != dimension_ids[1] == dimension_ids[2]
    assert catalog_ids[0] != catalog_ids[1] == catalog_ids[2]
    assert len(set(line_version_ids)) == 3
    assert final_projection.classification_revision == 3
    assert str(final_projection.dimension_id) == dimension_ids[-1]
    assert str(final_projection.catalog_id) == catalog_ids[-1]

    payloads = [row.payload for row in historical]
    assert [payload["source_event_id"] for payload in payloads] == [
        "class-create-drink",
        "class-reassign-drink",
        "class-reassign-drink-noop",
    ]
    assert [payload["activity_kind"] for payload in payloads] == [
        "create",
        "reclassify",
        "reclassify",
    ]
    machine_actor_hash = sha256(
        b"track-anywhere:v2:backfill:source-actor:v1\x00machine-source"
    ).hexdigest()
    assert payloads[1]["source_actor_hash"] == machine_actor_hash
    assert payloads[2]["source_actor_hash"] == machine_actor_hash
    assert all(
        payload["source_actor_hash"] not in {"owner-source", "machine-source"}
        for payload in payloads
    )
    assert all(len(payload["source_row_hash"]) == 64 for payload in payloads)
    assert all("memo" not in payload for payload in payloads)
    assert historical[0].causation_event_id is None
    assert [row.causation_event_id for row in historical[1:]] == [
        reporting[1].event_id,
        reporting[2].event_id,
    ]


def test_pure_four_leg_fx_uses_fx_kernel_while_mixed_six_leg_stays_generic(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    rows["accounts"].extend(
        [
            {
                "account_id": "acc-cny-fx",
                "book_id": "book-home",
                "currency": "CNY",
                "institution": None,
                "institution_type": None,
                "name": "CNY FX clearing",
                "subtype": None,
                "type": "system",
                "version": 1,
            },
            {
                "account_id": "acc-usdt-fx",
                "book_id": "book-home",
                "currency": "USDT",
                "institution": None,
                "institution_type": None,
                "name": "USDT FX clearing",
                "subtype": None,
                "type": "system",
                "version": 1,
            },
            {
                "account_id": "acc-cash-alt",
                "book_id": "book-home",
                "currency": "CNY",
                "institution": None,
                "institution_type": None,
                "name": "Other cash",
                "subtype": None,
                "type": "asset",
                "version": 1,
            },
        ]
    )
    rows["transactions"].extend(
        [
            {
                "book_id": "book-home",
                "memo": "pure fx",
                "occurred_at": "2026-01-04T00:00:00.000000Z",
                "purpose": "fx",
                "reversed_by": None,
                "reverses_transaction_id": None,
                "transaction_id": "txn-pure-fx",
                "version": 1,
            },
            {
                "book_id": "book-home",
                "memo": "mixed fx and fee",
                "occurred_at": "2026-01-05T00:00:00.000000Z",
                "purpose": "fx",
                "reversed_by": None,
                "reverses_transaction_id": None,
                "transaction_id": "txn-mixed-fx",
                "version": 1,
            },
        ]
    )

    def posting(
        source_id: int,
        transaction_id: str,
        position: int,
        account_id: str,
        side: str,
        amount: str,
        currency: str,
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

    rows["postings"].extend(
        [
            posting(10, "txn-pure-fx", 0, "acc-cash", "credit", "700.00", "CNY"),
            posting(11, "txn-pure-fx", 1, "acc-cny-fx", "debit", "700.00", "CNY"),
            posting(
                12, "txn-pure-fx", 2, "acc-usdt-fx", "credit", "1.00000000", "USDT"
            ),
            posting(
                13, "txn-pure-fx", 3, "acc-usdt-wallet", "debit", "1.00000000", "USDT"
            ),
            posting(20, "txn-mixed-fx", 0, "acc-cash", "credit", "700.00", "CNY"),
            posting(21, "txn-mixed-fx", 1, "acc-cash-alt", "credit", "10.00", "CNY"),
            posting(22, "txn-mixed-fx", 2, "acc-cny-fx", "debit", "700.00", "CNY"),
            posting(23, "txn-mixed-fx", 3, "acc-expense", "debit", "10.00", "CNY"),
            posting(
                24, "txn-mixed-fx", 4, "acc-usdt-fx", "credit", "1.00000000", "USDT"
            ),
            posting(
                25, "txn-mixed-fx", 5, "acc-usdt-wallet", "debit", "1.00000000", "USDT"
            ),
        ]
    )

    def historical_line(
        line_id: str,
        transaction_id: str,
        position: int,
        line_type: str,
        amount: str,
        currency: str,
    ) -> dict[str, object]:
        return {
            "amount": amount,
            "book_id": "book-home",
            "category_id": None,
            "category_path_snapshot": None,
            "category_version_id": None,
            "counterparty_id": None,
            "currency": currency,
            "line_id": line_id,
            "line_type": line_type,
            "memo": "private",
            "necessity": "unknown",
            "position": position,
            "project_id": None,
            "reimbursement_status": "none",
            "transaction_id": transaction_id,
            "version": 1,
        }

    rows["transaction_lines"].extend(
        [
            historical_line(
                "line-pure-fx", "txn-pure-fx", 0, "fx_exchange", "1.0000", "USDT"
            ),
            historical_line(
                "line-mixed-fx", "txn-mixed-fx", 0, "fx_exchange", "1.0000", "USDT"
            ),
            historical_line(
                "line-mixed-fee", "txn-mixed-fx", 1, "fx_fee", "10.00", "CNY"
            ),
        ]
    )
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "fx-kernel-extraction",
        dump_sha256="e" * 64,
        source_revision="v1-synthetic",
    )

    load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            transaction_kinds = connection.execute(
                text(
                    "select effective_at, transaction_kind from journal_transactions "
                    "where effective_at in "
                    "('2026-01-04 00:00:00+00', '2026-01-05 00:00:00+00') "
                    "order by effective_at"
                )
            ).all()
            system_roles = connection.execute(
                text(
                    "select current_name, system_role from accounts "
                    "where current_name in ('CNY FX clearing', 'USDT FX clearing') "
                    "order by current_name"
                )
            ).all()
    finally:
        engine.dispose()

    assert [row.transaction_kind for row in transaction_kinds] == ["fx", "standard"]
    assert [row.system_role for row in system_roles] == ["fx_trading", "fx_trading"]


def test_financial_and_historical_events_share_one_canonical_per_book_schedule(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    tie_time = "2026-01-02T03:04:05.000000Z"
    rows["classification_events"] = [
        {
            "classification_event_id": "txn-lunch",
            "book_id": "book-home",
            "event_type": "create",
            "source_category_id": "cat-food",
            "target_category_id": None,
            "affected_line_count": 0,
            "before": {},
            "after": {"category_id": "cat-food", "name": "Food"},
            "rollback": {},
            "created_by": "owner-source",
            "created_at": tie_time,
            "version": 1,
        }
    ]
    rows["investment_events"] = [
        {
            "account_id": "acc-usdt-wallet",
            "amount": "10.00",
            "book_id": "book-home",
            "currency": "USDT",
            "event_id": "txn-lunch",
            "event_type": "buy",
            "memo": "private",
            "nav": None,
            "occurred_at": tie_time,
            "transaction_id": None,
            "units": None,
            "version": 1,
        }
    ]
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "global-schedule-extraction",
        dump_sha256="f" * 64,
        source_revision="v1-synthetic",
    )

    load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            ordered = connection.execute(
                text(
                    "select event_type, effective_at from ledger_events "
                    "order by book_position"
                )
            ).all()
    finally:
        engine.dispose()

    assert [row.event_type for row in ordered] == [
        "JournalTransactionPosted",
        "ReportingLinesAssigned",
        "HistoricalCategoryActivityImported",
        "HistoricalInvestmentActivityImported",
        "JournalTransactionPosted",
    ]
    assert [row.effective_at.isoformat() for row in ordered[:4]] == [
        "2026-01-02T03:04:05+00:00",
        "2026-01-02T03:04:05+00:00",
        "2026-01-02T03:04:05+00:00",
        "2026-01-02T03:04:05+00:00",
    ]


def test_inferred_same_time_reversal_runs_after_original_when_raw_id_sorts_first(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    original = rows["transactions"][0]
    original["reversed_by"] = "aaa-reversal"
    rows["transactions"].append(
        {
            "book_id": "book-home",
            "memo": "same-time correction",
            "occurred_at": original["occurred_at"],
            "purpose": "correction",
            "reversed_by": None,
            "reverses_transaction_id": None,
            "transaction_id": "aaa-reversal",
            "version": 1,
        }
    )
    rows["postings"].extend(
        [
            {
                **rows["postings"][0],
                "id": 10,
                "side": "debit",
                "transaction_id": "aaa-reversal",
            },
            {
                **rows["postings"][3],
                "id": 11,
                "side": "credit",
                "transaction_id": "aaa-reversal",
            },
        ]
    )
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "same-time-reversal-extraction",
        dump_sha256="1" * 64,
        source_revision="v1-synthetic",
    )

    load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            event_types = (
                connection.execute(
                    text(
                        "select event_type from ledger_events "
                        "where effective_at='2026-01-02 03:04:05+00' "
                        "order by book_position"
                    )
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()

    assert event_types == [
        "JournalTransactionPosted",
        "ReportingLinesAssigned",
        "JournalTransactionReversed",
    ]


def test_credit_card_reversal_before_its_original_fails_before_any_target_write(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    rows["accounts"][0]["type"] = "liability"
    rows["accounts"][0]["subtype"] = "credit_card"
    original = rows["transactions"][0]
    original["reversed_by"] = "early-reversal"
    rows["transactions"].append(
        {
            "book_id": "book-home",
            "memo": "invalid early correction",
            "occurred_at": "2026-01-01T03:04:05.000000Z",
            "purpose": "correction",
            "reversed_by": None,
            "reverses_transaction_id": "txn-lunch",
            "transaction_id": "early-reversal",
            "version": 1,
        }
    )
    rows["postings"].extend(
        [
            {
                **rows["postings"][0],
                "id": 10,
                "side": "debit",
                "transaction_id": "early-reversal",
            },
            {
                **rows["postings"][3],
                "id": 11,
                "side": "credit",
                "transaction_id": "early-reversal",
            },
        ]
    )
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "early-reversal-extraction",
        dump_sha256="3" * 64,
        source_revision="v1-synthetic",
    )

    with pytest.raises(BackfillMappingError, match="reversal_precedes_original"):
        load_extracted_rows_to_target(
            target_url=migrated_postgres_database.runtime_url,
            manifest=manifest,
            rows_by_table=rows,
            credit_card_review=approved_mechanical_review(
                tmp_path, manifest=manifest, rows=rows
            ),
        )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            assert {
                table: int(connection.scalar(text(f"select count(*) from {table}")))
                for table in (
                    "users",
                    "books",
                    "ledger_events",
                    "backfill_source_receipts",
                    "backfill_quarantine",
                )
            } == {
                "backfill_quarantine": 0,
                "backfill_source_receipts": 0,
                "books": 0,
                "ledger_events": 0,
                "users": 0,
            }
    finally:
        engine.dispose()


def test_generic_reversal_preserves_an_earlier_historical_effective_time(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    original = rows["transactions"][0]
    original["reversed_by"] = "early-generic-reversal"
    rows["transactions"].append(
        {
            "book_id": "book-home",
            "memo": "historical early correction",
            "occurred_at": "2026-01-01T03:04:05.000000Z",
            "purpose": "correction",
            "reversed_by": None,
            "reverses_transaction_id": "txn-lunch",
            "transaction_id": "early-generic-reversal",
            "version": 1,
        }
    )
    rows["postings"].extend(
        [
            {
                **rows["postings"][0],
                "id": 10,
                "side": "debit",
                "transaction_id": "early-generic-reversal",
            },
            {
                **rows["postings"][3],
                "id": 11,
                "side": "credit",
                "transaction_id": "early-generic-reversal",
            },
        ]
    )
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "early-generic-reversal-extraction",
        dump_sha256="4" * 64,
        source_revision="v1-synthetic",
    )

    load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            reversal_time, original_time = connection.execute(
                text(
                    "select reversal.effective_at, original.effective_at "
                    "from transaction_reversals relation "
                    "join journal_transactions reversal "
                    "on reversal.book_id=relation.book_id and "
                    "reversal.transaction_id=relation.reversal_transaction_id "
                    "join journal_transactions original "
                    "on original.book_id=relation.book_id and "
                    "original.transaction_id=relation.original_transaction_id"
                )
            ).one()
        assert reversal_time < original_time
    finally:
        engine.dispose()


def test_reclassification_before_target_transaction_fails_before_target_write(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    _add_food_to_work_reclassification(
        rows,
        created_at="2026-01-02T03:04:04.999999Z",
    )
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "early-reclass-extraction",
        dump_sha256="4" * 64,
        source_revision="v1-synthetic",
    )

    with pytest.raises(
        BackfillMappingError, match="classification_precedes_transaction"
    ):
        load_extracted_rows_to_target(
            target_url=migrated_postgres_database.runtime_url,
            manifest=manifest,
            rows_by_table=rows,
        )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            assert int(connection.scalar(text("select count(*) from users"))) == 0
            assert (
                int(connection.scalar(text("select count(*) from ledger_events"))) == 0
            )
            assert (
                int(
                    connection.scalar(
                        text("select count(*) from backfill_source_receipts")
                    )
                )
                == 0
            )
    finally:
        engine.dispose()


def test_same_time_reclassification_runs_after_its_target_transaction(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    _add_food_to_work_reclassification(
        rows,
        created_at="2026-01-02T03:04:05.000000Z",
    )
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "same-time-reclass-extraction",
        dump_sha256="5" * 64,
        source_revision="v1-synthetic",
    )

    load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            event_types = (
                connection.execute(
                    text(
                        "select event_type from ledger_events "
                        "where effective_at='2026-01-02 03:04:05+00' "
                        "order by book_position"
                    )
                )
                .scalars()
                .all()
            )
            final_line = connection.execute(
                text(
                    "select classification_revision, dimension_id, catalog_id "
                    "from reporting_lines order by classification_revision desc limit 1"
                )
            ).one()
    finally:
        engine.dispose()

    assert event_types == [
        "JournalTransactionPosted",
        "ReportingLinesAssigned",
        "ReportingLinesAssigned",
        "HistoricalCategoryActivityImported",
    ]
    assert final_line.classification_revision == 2


def test_raw_source_tie_order_is_also_the_checkpoint_order(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    rename = {"txn-lunch": "tx-0", "txn-usdt": "tx-1"}
    tie_time = "2026-01-02T03:04:05.000000Z"
    for transaction in rows["transactions"]:
        transaction["transaction_id"] = rename[str(transaction["transaction_id"])]
        transaction["occurred_at"] = tie_time
    for posting in rows["postings"]:
        posting["transaction_id"] = rename[str(posting["transaction_id"])]
    for line in rows["transaction_lines"]:
        line["transaction_id"] = rename[str(line["transaction_id"])]
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "raw-tie-checkpoint-extraction",
        dump_sha256="a" * 64,
        source_revision="v1-synthetic",
    )

    load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            receipts = connection.execute(
                text(
                    "select source_primary_key, canonical_source_key "
                    "from backfill_source_receipts "
                    "where source_table='transactions' "
                    "order by canonical_source_key"
                )
            ).all()
    finally:
        engine.dispose()

    assert [row.source_primary_key for row in receipts] == [
        '["tx-0"]',
        '["tx-1"]',
    ]


def test_multi_book_same_time_aggregates_share_execution_and_checkpoint_rank(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    tie_time = "2026-01-02T03:04:05.000000Z"
    home_transaction = rows["transactions"][0]
    home_transaction["transaction_id"] = "zzz-home"
    home_transaction["occurred_at"] = tie_time
    rows["transactions"] = [home_transaction]
    rows["postings"] = [
        posting
        for posting in rows["postings"]
        if posting["transaction_id"] == "txn-lunch"
    ]
    for posting in rows["postings"]:
        posting["transaction_id"] = "zzz-home"
    rows["transaction_lines"][0]["transaction_id"] = "zzz-home"

    rows["ledger_books"].append(
        {
            **rows["ledger_books"][0],
            "book_id": "book-other",
            "name": "Other",
        }
    )
    rows["accounts"].extend(
        [
            {
                **rows["accounts"][0],
                "account_id": "acc-other-cash",
                "book_id": "book-other",
                "name": "Other cash",
            },
            {
                **rows["accounts"][1],
                "account_id": "acc-other-expense",
                "book_id": "book-other",
                "name": "Other expense",
            },
        ]
    )
    rows["categories"].append(
        {
            **rows["categories"][0],
            "book_id": "book-other",
            "category_id": "cat-other",
            "name": "Other category",
            "normalized_name": "other category",
            "path_cache": "Other category",
        }
    )
    rows["category_versions"].append(
        {
            **rows["category_versions"][0],
            "book_id": "book-other",
            "category_id": "cat-other",
            "category_version_id": "catv-other-1",
            "name": "Other category",
            "path": "Other category",
        }
    )
    rows["transactions"].append(
        {
            **home_transaction,
            "book_id": "book-other",
            "transaction_id": "aaa-other",
        }
    )
    rows["postings"].extend(
        [
            {
                **rows["postings"][0],
                "account_id": "acc-other-cash",
                "book_id": "book-other",
                "id": 20,
                "transaction_id": "aaa-other",
            },
            {
                **rows["postings"][1],
                "account_id": "acc-other-expense",
                "book_id": "book-other",
                "id": 21,
                "transaction_id": "aaa-other",
            },
        ]
    )
    rows["transaction_lines"].append(
        {
            **rows["transaction_lines"][0],
            "book_id": "book-other",
            "category_id": "cat-other",
            "category_path_snapshot": {"primary": "Other category"},
            "category_version_id": "catv-other-1",
            "counterparty_id": None,
            "line_id": "line-other",
            "transaction_id": "aaa-other",
        }
    )
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "multi-book-aggregate-extraction",
        dump_sha256="6" * 64,
        source_revision="v1-synthetic",
    )

    load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            posted_books = (
                connection.execute(
                    text(
                        "select book_id from ledger_events "
                        "where event_type='JournalTransactionPosted' "
                        "order by global_sequence"
                    )
                )
                .scalars()
                .all()
            )
            receipts = connection.execute(
                text(
                    "select source_table, canonical_source_key, book_id "
                    "from backfill_source_receipts "
                    "where source_table in "
                    "('transactions', 'postings', 'transaction_lines') "
                    "order by source_table, canonical_source_key"
                )
            ).all()
            checkpoints = {
                row.source_table: row.last_canonical_source_key
                for row in connection.execute(
                    text(
                        "select source_table, last_canonical_source_key "
                        "from backfill_checkpoints where source_table in "
                        "('transactions', 'postings', 'transaction_lines')"
                    )
                )
            }
    finally:
        engine.dispose()

    expected_books = sorted(
        {UUID(str(row.book_id)) for row in receipts},
        key=lambda book_id: book_id.bytes,
    )
    assert [UUID(str(book_id)) for book_id in posted_books] == expected_books
    by_table = {
        table: [row for row in receipts if row.source_table == table]
        for table in ("transactions", "postings", "transaction_lines")
    }
    assert [row.canonical_source_key[:12] for row in by_table["transactions"]] == [
        "000000000000",
        "000000000001",
    ]
    assert [
        UUID(str(row.book_id)) for row in by_table["transactions"]
    ] == expected_books
    for table, table_receipts in by_table.items():
        assert checkpoints[table] == table_receipts[-1].canonical_source_key
        assert [row.canonical_source_key for row in table_receipts] == sorted(
            row.canonical_source_key for row in table_receipts
        )


def test_catalogs_across_books_follow_loader_canonical_checkpoint_order(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    rows = _synthetic_rows()
    rows["ledger_books"].append(
        {
            **rows["ledger_books"][0],
            "book_id": "book-z",
            "name": "Second book",
        }
    )
    rows["accounts"].append(
        {
            **rows["accounts"][0],
            "account_id": "000-second-account",
            "book_id": "book-z",
            "name": "Second cash",
        }
    )
    rows["categories"].append(
        {
            **rows["categories"][0],
            "book_id": "book-z",
            "category_id": "000-second-category",
            "name": "Second category",
            "normalized_name": "second category",
            "path_cache": "Second category",
        }
    )
    rows["category_versions"].append(
        {
            **rows["category_versions"][0],
            "book_id": "book-z",
            "category_id": "000-second-category",
            "category_version_id": "000-second-category-version",
            "name": "Second category",
            "path": "Second category",
        }
    )
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "two-book-catalog-extraction",
        dump_sha256="2" * 64,
        source_revision="v1-synthetic",
    )

    load_extracted_rows_to_target(
        target_url=migrated_postgres_database.runtime_url,
        manifest=manifest,
        rows_by_table=rows,
    )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            counts = connection.execute(
                text(
                    "select source_table, count(*) from backfill_source_receipts "
                    "where source_table in ('accounts', 'categories', 'category_versions') "
                    "group by source_table order by source_table"
                )
            ).all()
    finally:
        engine.dispose()

    assert {row.source_table: int(row.count) for row in counts} == {
        "accounts": 5,
        "categories": 2,
        "category_versions": 2,
    }
