from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_fund_flows_use_storage_truth_when_memory_maps_are_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Fund Truth Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="fund-truth-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Fund Truth Expense", "type": "expense", "currency": "CNY"},
        idempotency_key="fund-truth-expense",
    )
    fund, _ = service.create_fund(
        token,
        {"name": "Fund Truth Envelope", "currency": "CNY"},
        idempotency_key="fund-truth-create",
    )

    def fail_create_transaction(*_args, **_kwargs):
        raise AssertionError("fund flow called legacy in-memory transaction factory")

    service.ledger.create_transaction = fail_create_transaction
    service.ledger.accounts[cash.account_id] = replace(cash, currency="USD")
    service.ledger.accounts[expense.account_id] = replace(expense, book_id="stale_book")
    service.ledger.accounts[fund.account_id] = replace(service.storage.get_account(fund.account_id), currency="USD")

    allocation, replay = service.allocate_fund(
        token,
        {
            "fund_id": fund.fund_id,
            "source_account_id": cash.account_id,
            "amount": "40",
            "currency": "CNY",
            "expected_version": fund.version,
            "memo": "storage truth fund allocation",
        },
        idempotency_key="fund-truth-allocate",
    )
    spend, replay = service.spend_fund(
        token,
        {
            "fund_id": fund.fund_id,
            "expense_account_id": expense.account_id,
            "amount": "15",
            "currency": "CNY",
            "expected_version": allocation["fund"].version,
            "memo": "storage truth fund spend",
        },
        idempotency_key="fund-truth-spend",
    )

    assert replay is False
    assert allocation["transaction"].purpose == "fund_allocation"
    assert spend["transaction"].purpose == "fund_spend"
    assert allocation["transaction"].transaction_id not in service.ledger.transactions
    assert spend["transaction"].transaction_id not in service.ledger.transactions
    assert Decimal(service.account_balance(token, cash.account_id)["official_balance"]["amount"]) == Decimal("60")
    assert Decimal(service.account_balance(token, fund.account_id)["official_balance"]["amount"]) == Decimal("25")
    assert Decimal(service.account_balance(token, expense.account_id)["official_balance"]["amount"]) == Decimal("15")
