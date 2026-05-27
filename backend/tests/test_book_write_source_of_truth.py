from __future__ import annotations

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
    service.ledger.accounts[cash.account_id].book_id = "stale_book"
    service.ledger.accounts[food.account_id].book_id = "stale_book"

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
