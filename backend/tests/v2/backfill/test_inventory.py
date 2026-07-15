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


def test_inventory_validates_counterparty_reference_and_book_scope() -> None:
    rows = _valid_rows()
    rows["counterparties"] = [{"counterparty_id": "merchant", "book_id": "book-b"}]
    rows["transaction_lines"] = [
        {
            "line_id": "line-a",
            "transaction_id": "tx-a",
            "book_id": "book-a",
            "category_id": None,
            "category_version_id": None,
            "counterparty_id": "merchant",
            "currency": "CNY",
            "amount": "10.00",
        }
    ]

    cross_book = inventory_rows(rows)
    rows["transaction_lines"][0]["counterparty_id"] = "missing"
    missing = inventory_rows(rows)

    assert "cross_book_reference" in {issue.code for issue in cross_book.issues}
    assert "counterparty_reference_missing" in {issue.code for issue in missing.issues}


def test_inventory_deterministically_infers_one_sided_exact_reversal() -> None:
    rows = _valid_rows()
    original = rows["transactions"][0]
    original["reversed_by"] = "tx-a-reversal"
    rows["transactions"].append(
        {
            "transaction_id": "tx-a-reversal",
            "book_id": "book-a",
            "reverses_transaction_id": None,
        }
    )
    rows["postings"].extend(
        [
            {
                **rows["postings"][0],
                "id": 3,
                "amount": "-10.00",
                "transaction_id": "tx-a-reversal",
            },
            {
                **rows["postings"][1],
                "id": 4,
                "amount": "10.00",
                "transaction_id": "tx-a-reversal",
            },
        ]
    )

    report = inventory_rows(rows)

    assert report.issues == ()
    assert [resolution.to_dict() for resolution in report.resolutions] == [
        {
            "code": "inferred_reversal_link",
            "relation": "reverses_transaction_id:tx-a",
            "source_primary_key": "tx-a-reversal",
            "source_table": "transactions",
        }
    ]


def test_inventory_refuses_to_infer_one_sided_non_inverse_reversal() -> None:
    rows = _valid_rows()
    original = rows["transactions"][0]
    original["reversed_by"] = "tx-a-reversal"
    rows["transactions"].append(
        {
            "transaction_id": "tx-a-reversal",
            "book_id": "book-a",
            "reverses_transaction_id": None,
        }
    )
    rows["postings"].extend(
        [
            {
                **rows["postings"][0],
                "id": 3,
                "amount": "-9.99",
                "transaction_id": "tx-a-reversal",
            },
            {
                **rows["postings"][1],
                "id": 4,
                "amount": "9.99",
                "transaction_id": "tx-a-reversal",
            },
        ]
    )

    report = inventory_rows(rows)

    assert report.resolutions == ()
    assert {issue.code for issue in report.issues} == {"reversal_inconsistent"}


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
