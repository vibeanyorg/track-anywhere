from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_account_opening_balance_does_not_use_in_memory_transaction_factory(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token

    def fail_create_transaction(*_args, **_kwargs):
        raise AssertionError("account opening balance called legacy in-memory transaction factory")

    service.ledger.create_transaction = fail_create_transaction

    account, replay = service.create_account(
        token,
        {"name": "Opening Truth Cash", "type": "asset", "currency": "CNY", "opening_balance": "42"},
        idempotency_key="opening-truth-cash",
    )

    assert replay is False
    assert Decimal(service.account_balance(token, account.account_id)["official_balance"]["amount"]) == Decimal("42")
    opening = next(
        transaction
        for transaction in service.list_transactions(token, limit=10)
        if transaction.purpose == "opening_balance"
    )
    assert [(posting.account_id, str(posting.amount), posting.currency) for posting in opening.postings][0] == (
        account.account_id,
        "42",
        "CNY",
    )
    assert [(str(posting.amount), posting.currency) for posting in opening.postings][1] == ("-42", "CNY")


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


def test_fx_exchange_uses_storage_truth_when_memory_maps_are_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    cny, _ = service.create_account(
        token,
        {"name": "FX Truth CNY", "type": "asset", "currency": "CNY", "opening_balance": "1000"},
        idempotency_key="fx-truth-cny",
    )
    usd, _ = service.create_account(
        token,
        {"name": "FX Truth USD", "type": "asset", "currency": "USD"},
        idempotency_key="fx-truth-usd",
    )
    fee, _ = service.create_account(
        token,
        {"name": "FX Truth Fee", "type": "expense", "currency": "CNY"},
        idempotency_key="fx-truth-fee",
    )

    def fail_create_transaction(*_args, **_kwargs):
        raise AssertionError("FX exchange called legacy in-memory transaction factory")

    service.ledger.create_transaction = fail_create_transaction
    service.ledger.accounts[cny.account_id].currency = "USD"
    service.ledger.accounts[usd.account_id].book_id = "stale_book"
    service.ledger.accounts[fee.account_id].type = "asset"

    transaction, replay = service.record_fx_exchange(
        token,
        {
            "from_account_id": cny.account_id,
            "from_amount": "100",
            "from_currency": "CNY",
            "to_account_id": usd.account_id,
            "to_amount": "10",
            "to_currency": "USD",
            "fee_account_id": fee.account_id,
            "fee_amount": "2",
            "memo": "storage truth fx",
        },
        idempotency_key="fx-truth-exchange",
    )

    assert replay is False
    assert [line.line_type for line in transaction.lines] == ["fx_exchange", "fx_fee"]
    assert Decimal(service.account_balance(token, cny.account_id)["official_balance"]["amount"]) == Decimal("898")
    assert Decimal(service.account_balance(token, usd.account_id)["official_balance"]["amount"]) == Decimal("10")
    assert Decimal(service.account_balance(token, fee.account_id)["official_balance"]["amount"]) == Decimal("2")


def test_core_transaction_writes_do_not_use_in_memory_transaction_factory():
    repo_root = Path.cwd()
    files = [
        repo_root / "backend/app/track_anywhere/service_accounts.py",
        repo_root / "backend/app/track_anywhere/service_balances.py",
        repo_root / "backend/app/track_anywhere/service_drafts.py",
        repo_root / "backend/app/track_anywhere/service_finance.py",
        repo_root / "backend/app/track_anywhere/service_fx.py",
        repo_root / "backend/app/track_anywhere/service_ledger.py",
        repo_root / "backend/app/track_anywhere/service_payment_profiles.py",
    ]
    offenders: list[str] = []
    patterns = [
        re.compile(r"\bself\.ledger\.create_transaction\("),
        re.compile(r"\bself\.ledger\.reverse_transaction\("),
    ]
    for path in files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if any(pattern.search(line) for pattern in patterns):
                offenders.append(f"{path.relative_to(repo_root)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_reverse_transaction_uses_storage_truth_when_memory_transaction_is_missing(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Reverse Truth Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="reverse-truth-cash",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Reverse Truth Food"},
        idempotency_key="reverse-truth-category",
    )
    transaction, _ = service.record_expense(
        token,
        {
            "amount": "8",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": category.category_id,
            "purpose": "reverse truth spend",
        },
        idempotency_key="reverse-truth-expense",
    )
    service.ledger.transactions.pop(transaction.transaction_id)

    reversal, replay = service.reverse_transaction(
        token,
        {"transaction_id": transaction.transaction_id, "memo": "reverse from storage truth"},
        idempotency_key="reverse-truth-reversal",
    )

    assert replay is False
    assert reversal.reverses_transaction_id == transaction.transaction_id
    assert service.get_transaction(token, transaction.transaction_id).reversed_by == reversal.transaction_id
    assert service.account_balance(token, cash.account_id)["official_balance"]["amount"] == "100"


def test_payment_profile_expense_uses_storage_truth_when_memory_maps_are_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Payment Truth Food"},
        idempotency_key="payment-truth-category",
    )
    card, _ = service.create_account(
        token,
        {"name": "Payment Truth Card", "type": "asset", "currency": "USD"},
        idempotency_key="payment-truth-card",
    )
    usd24, _ = service.create_account(
        token,
        {"name": "Payment Truth USD24", "type": "asset", "currency": "USD24", "opening_balance": "100"},
        idempotency_key="payment-truth-usd24",
    )
    service.create_payment_profile(
        token,
        {
            "slug": "payment-truth-safepal",
            "display_name": "Payment Truth SafePal",
            "kind": "token_backed_card",
            "instrument_account_id": card.account_id,
            "backing_account_id": usd24.account_id,
            "settlement_mode": "immediate",
            "settlement_rate": "1",
        },
        idempotency_key="payment-truth-profile",
    )
    service.ledger.accounts[card.account_id].currency = "EUR"
    service.ledger.accounts[usd24.account_id].book_id = "stale_book"
    service.categories.categories[category.category_id].kind = "income"

    transaction, replay = service.record_payment_profile_expense(
        token,
        {
            "payment": "payment-truth-safepal",
            "amount": "3.40",
            "currency": "USD",
            "category_id": category.category_id,
            "purpose": "storage truth payment profile",
        },
        idempotency_key="payment-truth-expense",
    )

    assert replay is False
    assert [line.line_type for line in transaction.lines] == ["expense", "fx_exchange"]
    assert service.account_balance(token, card.account_id)["official_balance"]["amount"] == "0.00"
    assert service.account_balance(token, usd24.account_id)["official_balance"]["amount"] == "96.60"
