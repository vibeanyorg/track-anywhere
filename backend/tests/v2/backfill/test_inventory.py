from __future__ import annotations

from backend.tools.backfill_v1.inventory import inventory_rows


def _valid_rows() -> dict[str, list[dict[str, object]]]:
    return {
        "assets": [
            {"asset_code": "CNY"},
            {"asset_code": "USDT"},
        ],
        "ledger_books": [{"book_id": "book-a"}, {"book_id": "book-b"}],
        "accounts": [
            {"account_id": "cash", "book_id": "book-a", "currency": "CNY"},
            {"account_id": "expense", "book_id": "book-a", "currency": "CNY"},
            {"account_id": "other", "book_id": "book-b", "currency": "CNY"},
        ],
        "transactions": [
            {
                "transaction_id": "tx-a",
                "book_id": "book-a",
                "reverses_transaction_id": None,
            }
        ],
        "postings": [
            {
                "id": 1,
                "transaction_id": "tx-a",
                "book_id": "book-a",
                "position": 1,
                "account_id": "cash",
                "amount": "10.00",
                "currency": "CNY",
            },
            {
                "id": 2,
                "transaction_id": "tx-a",
                "book_id": "book-a",
                "position": 2,
                "account_id": "expense",
                "amount": "-10.00",
                "currency": "CNY",
            },
        ],
        "categories": [],
        "category_versions": [],
        "transaction_lines": [],
    }


def test_valid_inventory_is_clean() -> None:
    assert inventory_rows(_valid_rows()).issues == ()


def test_inventory_detects_every_blocking_relationship_class() -> None:
    rows = _valid_rows()
    rows["accounts"].append(
        {"account_id": "mystery", "book_id": "book-a", "currency": "ZZZ"}
    )
    rows["transactions"].extend(
        [
            {
                "transaction_id": "reverse-1",
                "book_id": "book-a",
                "reverses_transaction_id": "reverse-2",
            },
            {
                "transaction_id": "reverse-2",
                "book_id": "book-a",
                "reverses_transaction_id": "reverse-1",
            },
            {
                "transaction_id": "reverse-3",
                "book_id": "book-a",
                "reverses_transaction_id": "reverse-1",
            },
        ]
    )
    rows["postings"].extend(
        [
            {
                "id": 3,
                "transaction_id": "missing-tx",
                "book_id": "book-a",
                "position": 1,
                "account_id": "missing-account",
                "amount": "not-a-decimal",
                "currency": "ZZZ",
            },
            {
                "id": 4,
                "transaction_id": "tx-a",
                "book_id": "book-a",
                "position": 2,
                "account_id": "other",
                "amount": "1",
                "currency": "CNY",
            },
        ]
    )

    codes = {issue.code for issue in inventory_rows(rows).issues}

    assert {
        "orphan_reference",
        "cross_book_reference",
        "invalid_amount",
        "duplicate_position",
        "reversal_cycle",
        "reversal_multiplicity",
        "unknown_asset",
    } <= codes
