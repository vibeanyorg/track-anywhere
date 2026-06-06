from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app, service
from track_anywhere.balance_semantics import ACCOUNT_TYPE_BALANCE_SEMANTICS, liability_balance_view


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {service.owner_token}"}


def _create_account(client: TestClient, suffix: str, **payload):
    name = payload.setdefault("name", f"Account {suffix}")
    response = client.post(
        "/api/v1/accounts",
        json=payload,
        headers={**_headers(), "X-Idempotency-Key": f"api-account-resource-{suffix}-{uuid4().hex}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["account"]


def test_financial_accounts_default_excludes_internal_accounting_accounts_and_legacy_accounts_stay_compatible():
    client = TestClient(app)
    suffix = uuid4().hex[:8]

    visible = _create_account(
        client,
        suffix,
        name=f"FA Visible Cash {suffix}",
        type="asset",
        currency="CNY",
        opening_balance="123",
        institution_type="cash",
        subtype="cash",
        institution="local",
    )
    expense = _create_account(
        client,
        suffix,
        name=f"FA Hidden Expense {suffix}",
        type="expense",
        currency="CNY",
    )
    system = _create_account(
        client,
        suffix,
        name=f"FA Hidden System {suffix}",
        type="system",
        currency="CNY",
        institution_type="system",
        subtype="system_adjustment",
        institution="track-anywhere",
    )
    system_fund = _create_account(
        client,
        suffix,
        name=f"FA Hidden System Fund {suffix}",
        type="fund",
        currency="CNY",
        institution_type="system",
        subtype="fx_clearing",
        institution="track-anywhere",
    )

    financial_response = client.get("/api/v1/financial-accounts", params={"q": suffix}, headers=_headers())
    assert financial_response.status_code == 200
    financial_accounts = financial_response.json()["financial_accounts"]
    financial_ids = {account["account_id"] for account in financial_accounts}
    assert visible["account_id"] in financial_ids
    assert expense["account_id"] not in financial_ids
    assert system["account_id"] not in financial_ids
    assert system_fund["account_id"] not in financial_ids
    assert all(account["ledger_account_type"] in {"asset", "liability", "fund"} for account in financial_accounts)
    assert all("balance" not in account for account in financial_accounts)

    visible_financial = next(account for account in financial_accounts if account["account_id"] == visible["account_id"])
    assert visible_financial["ledger_account_id"] == visible["account_id"]
    assert visible_financial["type"] == "cash"
    assert visible_financial["ledger_account_type"] == "asset"
    assert visible_financial["status"] == "active"

    hidden_get = client.get(f"/api/v1/financial-accounts/{expense['account_id']}", headers=_headers())
    assert hidden_get.status_code == 404

    legacy_response = client.get("/api/v1/accounts", params={"name": suffix}, headers=_headers())
    assert legacy_response.status_code == 200
    legacy_ids = {account["account_id"] for account in legacy_response.json()["accounts"]}
    assert {visible["account_id"], expense["account_id"], system["account_id"], system_fund["account_id"]} <= legacy_ids

    ledger_response = client.get("/api/v1/ledger-accounts", params={"name": suffix}, headers=_headers())
    assert ledger_response.status_code == 200
    ledger_accounts = ledger_response.json()["ledger_accounts"]
    ledger_ids = {account["account_id"] for account in ledger_accounts}
    assert {visible["account_id"], expense["account_id"], system["account_id"], system_fund["account_id"]} <= ledger_ids
    assert any(account["type"] == "equity" for account in ledger_accounts)

    ledger_get = client.get(f"/api/v1/ledger-accounts/{expense['account_id']}", headers=_headers())
    assert ledger_get.status_code == 200
    assert ledger_get.json()["ledger_account"]["type"] == "expense"


def test_financial_accounts_support_product_filters_and_stable_types():
    client = TestClient(app)
    suffix = uuid4().hex[:8]

    debit = _create_account(
        client,
        suffix,
        name=f"Filter 招商 Debit {suffix}",
        type="asset",
        currency="CNY",
        institution_type="bank",
        subtype="debit_card",
        institution="招商银行",
    )
    card = _create_account(
        client,
        suffix,
        name=f"Filter 招商 Card {suffix}",
        type="liability",
        currency="CNY",
        institution_type="bank",
        subtype="credit_card",
        institution="招商银行",
    )
    crypto = _create_account(
        client,
        suffix,
        name=f"Filter SafePal {suffix}",
        type="asset",
        currency="USDC",
        institution_type="crypto_wallet",
        subtype="crypto_token",
        institution="SafePal",
    )

    bank_response = client.get(
        "/api/v1/financial-accounts",
        params={"q": "招商", "currency": "CNY", "institution_type": "bank", "institution": "招商"},
        headers=_headers(),
    )
    assert bank_response.status_code == 200
    bank_accounts = bank_response.json()["financial_accounts"]
    assert {account["account_id"] for account in bank_accounts} >= {debit["account_id"], card["account_id"]}
    assert {account["type"] for account in bank_accounts if account["account_id"] in {debit["account_id"], card["account_id"]}} == {
        "bank",
        "credit_card",
    }

    card_response = client.get(
        "/api/v1/financial-accounts",
        params={"q": suffix, "type": "credit_card", "subtype": "credit_card"},
        headers=_headers(),
    )
    assert card_response.status_code == 200
    assert [account["account_id"] for account in card_response.json()["financial_accounts"]] == [card["account_id"]]

    crypto_response = client.get(
        "/api/v1/financial-accounts",
        params={"q": suffix, "type": "crypto_wallet", "currency": "USDC"},
        headers=_headers(),
    )
    assert crypto_response.status_code == 200
    assert [account["account_id"] for account in crypto_response.json()["financial_accounts"]] == [crypto["account_id"]]

    ordered_response = client.get("/api/v1/financial-accounts", params={"q": suffix}, headers=_headers())
    assert ordered_response.status_code == 200
    assert [account["account_id"] for account in ordered_response.json()["financial_accounts"]] == [
        debit["account_id"],
        card["account_id"],
        crypto["account_id"],
    ]

    invalid_status_response = client.get(
        "/api/v1/financial-accounts",
        params={"q": suffix, "status": "inactive"},
        headers=_headers(),
    )
    assert invalid_status_response.status_code == 422


def test_financial_accounts_include_balance_uses_batch_read_and_preserves_liability_semantics(monkeypatch):
    client = TestClient(app)
    suffix = uuid4().hex[:8]

    cash = _create_account(
        client,
        suffix,
        name=f"FA Balance Cash {suffix}",
        type="asset",
        currency="CNY",
        opening_balance="100",
        institution_type="cash",
        subtype="cash",
    )
    card = _create_account(
        client,
        suffix,
        name=f"FA Balance Card {suffix}",
        type="liability",
        currency="CNY",
        opening_balance="30",
        institution_type="bank",
        subtype="credit_card",
        institution="Batch Bank",
    )

    original_account_balance = service.storage.account_balance
    original_account_balances = service.storage.account_balances
    batch_calls = []

    def record_account_balances(account_ids):
        ids = list(account_ids)
        batch_calls.append(ids)
        return original_account_balances(ids)

    def fail_account_balance(*_args, **_kwargs):
        raise AssertionError("financial account list must use batched account_balances")

    monkeypatch.setattr(service.storage, "account_balances", record_account_balances)
    monkeypatch.setattr(service.storage, "account_balance", fail_account_balance)

    list_response = client.get(
        "/api/v1/financial-accounts",
        params={"q": suffix, "include": "balance"},
        headers=_headers(),
    )
    assert list_response.status_code == 200
    accounts = {account["account_id"]: account for account in list_response.json()["financial_accounts"]}
    assert accounts[cash["account_id"]]["balance"]["official_balance"]["amount"] == "100"
    assert accounts[cash["account_id"]]["balance"]["official_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["asset"]
    assert accounts[card["account_id"]]["balance"]["official_balance"]["amount"] == "30"
    assert accounts[card["account_id"]]["balance"]["official_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]
    assert accounts[card["account_id"]]["balance"]["liability_balance"] == liability_balance_view(Decimal("30"))
    assert len(batch_calls) == 1
    assert {cash["account_id"], card["account_id"]} <= set(batch_calls[0])

    monkeypatch.setattr(service.storage, "account_balance", original_account_balance)

    show_response = client.get(
        f"/api/v1/financial-accounts/{card['account_id']}",
        params={"include": "balance"},
        headers=_headers(),
    )
    assert show_response.status_code == 200
    shown = show_response.json()["financial_account"]
    assert shown["type"] == "credit_card"
    assert shown["balance"]["liability_balance"] == liability_balance_view(Decimal("30"))

    balance_response = client.get(f"/api/v1/financial-accounts/{card['account_id']}/balance", headers=_headers())
    assert balance_response.status_code == 200
    assert balance_response.json()["liability_balance"] == liability_balance_view(Decimal("30"))
