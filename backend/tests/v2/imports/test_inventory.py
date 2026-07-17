from __future__ import annotations

from copy import deepcopy

import pytest

from backend.tools.frozen_v1_history.inventory import inventory_rows


def _valid_rows() -> dict[str, list[dict[str, object]]]:
    return {
        "assets": [{"asset_code": "CNY", "scale": 2, "display_scale": 2}],
        "ledger_books": [{"book_id": "book-a", "base_currency": "CNY"}],
        "accounts": [
            {"account_id": "cash", "book_id": "book-a", "currency": "CNY"},
            {"account_id": "expense", "book_id": "book-a", "currency": "CNY"},
        ],
        "categories": [{"category_id": "food", "book_id": "book-a", "parent_id": None}],
        "category_versions": [
            {"category_version_id": "food-v1", "category_id": "food", "book_id": "book-a", "parent_id": None, "valid_from": "2026-01-01T00:00:00Z", "valid_to": None}
        ],
        "counterparties": [{"counterparty_id": "shop", "book_id": "book-a"}],
        "transactions": [
            {"transaction_id": "tx", "book_id": "book-a", "occurred_at": "2026-01-01T00:00:00Z", "reversed_by": None, "reverses_transaction_id": None}
        ],
        "postings": [
            {"id": "p1", "transaction_id": "tx", "book_id": "book-a", "position": 0, "account_id": "cash", "currency": "CNY", "amount": "1.00", "amount_semantics": "debit_credit", "side": "credit"},
            {"id": "p2", "transaction_id": "tx", "book_id": "book-a", "position": 1, "account_id": "expense", "currency": "CNY", "amount": "1.00", "amount_semantics": "debit_credit", "side": "debit"},
        ],
        "transaction_lines": [
            {"line_id": "line", "transaction_id": "tx", "book_id": "book-a", "position": 0, "category_id": "food", "category_version_id": "food-v1", "counterparty_id": "shop", "currency": "CNY", "amount": "1.00"}
        ],
        "classification_events": [],
        "investment_events": [],
        "investment_valuations": [],
    }


def test_inventory_accepts_consistent_source_and_never_reprs_rows() -> None:
    rows = _valid_rows()
    report = inventory_rows(rows, attachments_count=0)
    assert report.ok
    assert report.issues == ()
    assert "shop" not in repr(report)
    assert "cash" not in repr(report)


def test_inventory_reports_all_blocking_relation_classes_deterministically() -> None:
    rows = deepcopy(_valid_rows())
    rows["postings"][0]["account_id"] = "missing"
    rows["postings"][0]["currency"] = "UNKNOWN"
    rows["postings"][0]["amount"] = "nan"
    rows["postings"][1]["position"] = 0
    rows["transaction_lines"][0]["category_version_id"] = "missing-version"
    rows["transaction_lines"][0]["counterparty_id"] = "missing-counterparty"
    rows["categories"].append(
        {"category_id": "foreign", "book_id": "book-b", "parent_id": None}
    )
    rows["transaction_lines"][0]["category_id"] = "foreign"

    first = inventory_rows(rows, attachments_count=0)
    second = inventory_rows(deepcopy(rows), attachments_count=0)

    assert first == second
    assert tuple(issue.code for issue in first.issues) == tuple(
        sorted(issue.code for issue in first.issues)
    )
    assert {
        "cross_book_reference",
        "duplicate_position",
        "invalid_amount",
        "invalid_category_version",
        "missing_counterparty",
        "orphan_reference",
        "unknown_asset",
    } <= {issue.code for issue in first.issues}


def test_inventory_blocks_when_attachment_absence_was_not_proved() -> None:
    report = inventory_rows(_valid_rows())
    assert not report.ok
    assert "missing_attachment_proof" in {issue.code for issue in report.issues}


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("posting_book", "cross_book_reference"),
        ("posting_asset", "asset_mismatch"),
        ("line_book", "cross_book_reference"),
        ("missing_parent", "orphan_reference"),
        ("bad_timestamp", "invalid_timestamp"),
        ("bad_position", "invalid_position"),
        ("inexact_amount", "invalid_amount"),
        ("unknown_semantics", "invalid_amount_semantics"),
    ],
)
def test_inventory_blocks_strict_book_parent_time_position_and_money_contracts(
    mutation: str, code: str
) -> None:
    rows = deepcopy(_valid_rows())
    if mutation == "posting_book":
        rows["postings"][0]["book_id"] = "other-book"
    elif mutation == "posting_asset":
        rows["accounts"][0]["currency"] = "USD"
    elif mutation == "line_book":
        rows["transaction_lines"][0]["book_id"] = "other-book"
    elif mutation == "missing_parent":
        rows["categories"][0]["parent_id"] = "missing-parent"
    elif mutation == "bad_timestamp":
        rows["transactions"][0]["occurred_at"] = "2026-01-01T00:00:00"
    elif mutation == "bad_position":
        rows["postings"][0]["position"] = -1
    elif mutation == "inexact_amount":
        rows["postings"][0]["amount"] = "1.001"
        rows["postings"][1]["amount"] = "1.001"
    elif mutation == "unknown_semantics":
        rows["postings"][0]["amount_semantics"] = "guess"

    report = inventory_rows(rows, attachments_count=0)
    assert code in {issue.code for issue in report.issues}
