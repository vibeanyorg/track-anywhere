from __future__ import annotations

import re
from pathlib import Path

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_core_transaction_writes_use_storage_truth_when_memory_maps_are_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Truth Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="truth-write-cash",
    )
    expense_account, _ = service.create_account(
        token,
        {"name": "Truth Expense Account", "type": "expense", "currency": "CNY"},
        idempotency_key="truth-write-expense-account",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Truth Food"},
        idempotency_key="truth-write-category",
    )

    service.ledger.accounts[cash.account_id].currency = "USD"
    service.ledger.accounts[expense_account.account_id].book_id = "stale_book"
    service.categories.categories[category.category_id].kind = "income"

    transfer, _ = service.record_transaction(
        token,
        {
            "amount": "5",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "to_account_id": expense_account.account_id,
            "purpose": "storage truth transfer",
        },
        idempotency_key="truth-write-transfer",
    )
    expense, _ = service.record_expense(
        token,
        {
            "amount": "7",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": category.category_id,
            "purpose": "storage truth expense",
        },
        idempotency_key="truth-write-expense",
    )
    adjustment, _ = service.adjust_balance(
        token,
        {
            "account_id": cash.account_id,
            "amount": "3",
            "currency": "CNY",
            "purpose": "storage truth adjust",
        },
        idempotency_key="truth-write-adjust",
    )

    assert transfer.book_id == cash.book_id
    assert expense.lines[0].category_id == category.category_id
    assert adjustment.postings[0].currency == "CNY"


def test_core_transaction_writes_do_not_use_in_memory_transaction_factory():
    repo_root = Path.cwd()
    files = [
        repo_root / "backend/app/track_anywhere/service_balances.py",
        repo_root / "backend/app/track_anywhere/service_drafts.py",
        repo_root / "backend/app/track_anywhere/service_ledger.py",
    ]
    offenders: list[str] = []
    pattern = re.compile(r"\bself\.ledger\.create_transaction\(")
    for path in files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(repo_root)}:{line_number}: {line.strip()}")

    assert offenders == []
