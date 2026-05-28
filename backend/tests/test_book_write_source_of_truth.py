from __future__ import annotations

from dataclasses import replace

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_book_scoped_transaction_uses_storage_truth_when_memory_maps_are_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    travel_book, _ = service.create_book(
        token,
        {"name": "Travel Truth", "kind": "travel", "base_currency": "CNY"},
        idempotency_key="book-truth-travel",
    )
    cash, _ = service.create_book_account(
        token,
        travel_book.book_id,
        {"name": "Travel Truth Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="book-truth-cash",
    )
    food, _ = service.create_book_account(
        token,
        travel_book.book_id,
        {"name": "Travel Truth Food", "type": "expense", "currency": "CNY"},
        idempotency_key="book-truth-food",
    )
    service.ledger.accounts[cash.account_id] = replace(cash, book_id="stale_book")
    service.ledger.accounts[food.account_id] = replace(food, book_id="stale_book")

    transaction, replay = service.record_book_transaction(
        token,
        travel_book.book_id,
        {
            "amount": "12",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "to_account_id": food.account_id,
            "purpose": "storage truth book transaction",
        },
        idempotency_key="book-truth-transaction",
    )

    assert replay is False
    assert transaction.book_id == travel_book.book_id


def test_book_category_merge_counts_storage_truth_when_memory_transaction_is_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Merge Truth Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="merge-truth-cash",
    )
    source, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Merge Truth Source"},
        idempotency_key="merge-truth-source",
    )
    target, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Merge Truth Target"},
        idempotency_key="merge-truth-target",
    )
    transaction, _ = service.record_expense(
        token,
        {
            "amount": "12",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": source.category_id,
            "purpose": "storage truth category merge",
        },
        idempotency_key="merge-truth-expense",
    )
    transaction.lines[0].category_id = target.category_id

    merged, replay = service.merge_book_category(
        token,
        source.book_id,
        source.category_id,
        {"target_category_id": target.category_id},
        idempotency_key="merge-truth-category",
    )
    merge_events = [
        event
        for event in service.list_book_classification_events(token, source.book_id)
        if event.event_type == "merge" and event.source_category_id == source.category_id
    ]

    assert replay is False
    assert merged.status == "archived"
    assert merge_events[-1].affected_line_count == 1
