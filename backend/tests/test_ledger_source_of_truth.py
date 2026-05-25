from __future__ import annotations

from decimal import Decimal

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_confirmed_ledger_reads_use_database_source_of_truth(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    account, _ = service.create_account(
        token,
        {"name": "Truth Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="truth-cash",
    )
    transaction, _ = service.adjust_balance(
        token,
        {
            "account_id": account.account_id,
            "amount": "100",
            "currency": "CNY",
            "purpose": "stored truth",
        },
        idempotency_key="truth-balance",
    )
    transaction.purpose = "dirty in-memory purpose"
    transaction.postings[0].amount = Decimal("999")

    balance = service.account_balance(token, account.account_id)
    shown = service.get_transaction(token, transaction.transaction_id)
    listed = service.list_transactions(token, account_id=account.account_id, limit=1)
    summary = service.account_summary(token, group_by="currency", currency="CNY")

    assert balance["official_balance"]["amount"] == "100"
    assert shown.purpose == "stored truth"
    assert shown.postings[0].amount == Decimal("100")
    assert listed[0].purpose == "stored truth"
    assert summary["groups"][0]["amount"] == "100"
