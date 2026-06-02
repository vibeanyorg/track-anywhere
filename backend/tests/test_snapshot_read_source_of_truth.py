from __future__ import annotations

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService
from track_anywhere.posting_semantics import canonical_posting_semantics_metadata


def test_transaction_snapshot_accounts_use_storage_truth_when_memory_mirror_is_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Snapshot Truth Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="snapshot-truth-cash",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Snapshot Truth Food"},
        idempotency_key="snapshot-truth-category",
    )
    transaction, _ = service.record_expense(
        token,
        {
            "amount": "9",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": category.category_id,
            "purpose": "storage truth snapshot",
        },
        idempotency_key="snapshot-truth-expense",
    )
    service.ledger.accounts.clear()

    snapshot = service.transaction_snapshot(token, transaction.transaction_id)

    assert snapshot["posting_semantics"] == {
        **canonical_posting_semantics_metadata(),
        "row_model": "debit_credit",
        "amount_semantics": ["debit_credit"],
    }
    assert cash.account_id in {account.account_id for account in snapshot["accounts"]}
