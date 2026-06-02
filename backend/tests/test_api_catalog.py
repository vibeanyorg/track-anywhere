from __future__ import annotations

from decimal import Decimal

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app, service
from track_anywhere.balance_semantics import (
    ACCOUNT_TYPE_BALANCE_SEMANTICS,
    CREDIT_CARD_CURRENT_BALANCE_COMPATIBILITY_ALIAS,
    CREDIT_CARD_DERIVED_AVAILABLE_CREDIT_SEMANTICS,
    LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
    LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
    account_summary_group_semantics_fields,
    account_summary_semantics_metadata,
    liability_balance_view,
)


def test_api_categories_expense_income_and_summary_flow():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}

    cash_resp = client.post(
        "/api/v1/accounts",
        json={"name": "Category API Cash", "type": "asset", "currency": "CNY", "opening_balance": "500"},
        headers={**headers, "X-Idempotency-Key": "api-category-cash"},
    )
    assert cash_resp.status_code == 200
    cash_id = cash_resp.json()["account"]["account_id"]

    expense_parent_resp = client.post(
        "/api/v1/categories",
        json={"kind": "expense", "name": "餐饮"},
        headers={**headers, "X-Idempotency-Key": "api-category-expense-parent"},
    )
    assert expense_parent_resp.status_code == 200
    expense_category_resp = client.post(
        "/api/v1/categories",
        json={"kind": "expense", "name": "外卖", "parent_id": expense_parent_resp.json()["category"]["category_id"]},
        headers={**headers, "X-Idempotency-Key": "api-category-expense"},
    )
    income_parent_resp = client.post(
        "/api/v1/categories",
        json={"kind": "income", "name": "工资"},
        headers={**headers, "X-Idempotency-Key": "api-category-income-parent"},
    )
    assert income_parent_resp.status_code == 200
    income_category_resp = client.post(
        "/api/v1/categories",
        json={"kind": "income", "name": "主业", "parent_id": income_parent_resp.json()["category"]["category_id"]},
        headers={**headers, "X-Idempotency-Key": "api-category-income"},
    )
    assert expense_category_resp.status_code == 200
    assert income_category_resp.status_code == 200
    expense_category_id = expense_category_resp.json()["category"]["category_id"]
    income_category_id = income_category_resp.json()["category"]["category_id"]

    category_list = client.get("/api/v1/categories?kind=expense&name=%E5%A4%96%E5%8D%96", headers=headers)
    assert category_list.status_code == 200
    assert any(item["category_id"] == expense_category_id for item in category_list.json()["categories"])

    expense_resp = client.post(
        "/api/v1/expenses",
        json={
            "amount": "42",
            "currency": "CNY",
            "from_account_id": cash_id,
            "category_id": expense_category_id,
            "purpose": "delivery",
        },
        headers={**headers, "X-Idempotency-Key": "api-expense-delivery"},
    )
    income_resp = client.post(
        "/api/v1/incomes",
        json={
            "amount": "100",
            "currency": "CNY",
            "to_account_id": cash_id,
            "category_id": income_category_id,
            "purpose": "salary",
        },
        headers={**headers, "X-Idempotency-Key": "api-income-salary"},
    )
    assert expense_resp.status_code == 200
    assert income_resp.status_code == 200
    assert expense_resp.json()["transaction"]["lines"][0]["category_id"] == expense_category_id
    assert income_resp.json()["transaction"]["lines"][0]["category_id"] == income_category_id

    expense_summary = client.get("/api/v1/summary/categories?kind=expense&currency=CNY", headers=headers)
    assert expense_summary.status_code == 200
    assert any(
        item["category_id"] == expense_category_id and item["amount"] == "42"
        for item in expense_summary.json()["groups"]
    )

    tx_list = client.get(f"/api/v1/ledger/transactions?category_id={expense_category_id}", headers=headers)
    assert tx_list.status_code == 200
    assert tx_list.json()["transactions"][0]["lines"][0]["category_id"] == expense_category_id


def test_api_credit_card_profile_flow():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}

    card_resp = client.post(
        "/api/v1/accounts",
        json={
            "name": "API Profile Card",
            "type": "liability",
            "currency": "CNY",
            "opening_balance": "300",
            "institution_type": "bank",
            "subtype": "credit_card",
            "institution": "API Bank",
        },
        headers={**headers, "X-Idempotency-Key": "api-profile-card"},
    )
    assert card_resp.status_code == 200
    card_id = card_resp.json()["account"]["account_id"]

    update_resp = client.patch(
        f"/api/v1/credit-cards/{card_id}",
        json={"credit_limit": "10000", "available_credit": "9700", "statement_day": 8, "due_day": 26},
        headers={**headers, "X-Idempotency-Key": "api-profile-card-update"},
    )
    assert update_resp.status_code == 200
    payload = update_resp.json()["credit_card"]
    assert payload["profile"]["credit_limit"] == "10000"
    assert payload["natural_balance"] == "300"
    assert payload["natural_balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]
    assert payload["current_balance"] == "300"
    assert payload["current_balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]
    assert payload["balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]
    assert payload["compatibility_aliases"]["current_balance"] == CREDIT_CARD_CURRENT_BALANCE_COMPATIBILITY_ALIAS
    assert payload["outstanding_balance"] == "300"
    assert payload["outstanding_balance_semantics"] == LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS
    assert payload["overpayment_balance"] == "0"
    assert payload["overpayment_balance_semantics"] == LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS
    assert payload["derived_available_credit"] == "9700"
    assert payload["derived_available_credit_semantics"] == CREDIT_CARD_DERIVED_AVAILABLE_CREDIT_SEMANTICS
    assert payload["utilization_rate"] == "0.03"

    balance_resp = client.get(f"/api/v1/query/accounts/{card_id}/balance", headers=headers)
    assert balance_resp.status_code == 200
    assert balance_resp.json()["liability_balance"] == liability_balance_view(Decimal("300"))
    assert balance_resp.json()["official_balance"]["amount_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]

    list_resp = client.get("/api/v1/credit-cards", headers=headers)
    assert list_resp.status_code == 200
    assert any(item["account"]["account_id"] == card_id for item in list_resp.json()["credit_cards"])


def test_api_account_metadata_create_filter_and_update():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}

    create_resp = client.post(
        "/api/v1/accounts",
        json={
            "name": "Wise USD",
            "type": "asset",
            "currency": "USD",
            "institution_type": "fintech",
            "subtype": "multicurrency_wallet",
            "institution": "Wise",
        },
        headers={**headers, "X-Idempotency-Key": "api-wise-usd"},
    )

    assert create_resp.status_code == 200
    account = create_resp.json()["account"]
    assert account["institution_type"] == "fintech"
    assert account["subtype"] == "multicurrency_wallet"
    assert account["institution"] == "Wise"

    list_resp = client.get("/api/v1/accounts?institution_type=fintech&subtype=multicurrency_wallet", headers=headers)
    assert list_resp.status_code == 200
    assert any(item["account_id"] == account["account_id"] for item in list_resp.json()["accounts"])

    update_resp = client.patch(
        f"/api/v1/accounts/{account['account_id']}",
        json={"institution_type": "bank", "subtype": "savings", "institution": "Example Bank"},
        headers={**headers, "X-Idempotency-Key": "api-wise-retag"},
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()["account"]
    assert updated["institution_type"] == "bank"
    assert updated["subtype"] == "savings"
    assert updated["institution"] == "Example Bank"
    assert updated["version"] == account["version"] + 1


def test_api_account_supports_crypto_wallet_asset_codes():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}

    create_resp = client.post(
        "/api/v1/accounts",
        json={
            "name": "SafePal USDC (Arbitrum)",
            "type": "asset",
            "currency": "USDC",
            "opening_balance": "9.126095",
            "institution_type": "crypto_wallet",
            "subtype": "crypto_token",
            "institution": "SafePal Wallet01-LM3",
        },
        headers={**headers, "X-Idempotency-Key": "api-safepal-usdc"},
    )

    assert create_resp.status_code == 200
    account = create_resp.json()["account"]
    assert account["currency"] == "USDC"
    assert account["institution_type"] == "crypto_wallet"
    assert account["subtype"] == "crypto_token"

    balance_resp = client.get(f"/api/v1/query/accounts/{account['account_id']}/balance", headers=headers)
    assert balance_resp.status_code == 200
    assert balance_resp.json()["currency"] == "USDC"
    assert balance_resp.json()["official_balance"]["amount"] == "9.126095"


def test_api_book_scoped_accounts_include_balance_semantics():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}
    book_resp = client.post(
        "/api/v1/books",
        json={"name": "Semantics Book", "kind": "personal", "base_currency": "USD", "timezone": "UTC"},
        headers={**headers, "X-Idempotency-Key": "api-semantics-book"},
    )
    assert book_resp.status_code == 200
    book_id = book_resp.json()["book"]["book_id"]

    account_resp = client.post(
        f"/api/v1/books/{book_id}/accounts",
        json={"name": "Semantics Card", "type": "liability", "currency": "USD", "subtype": "credit_card"},
        headers={**headers, "X-Idempotency-Key": "api-semantics-book-card"},
    )
    assert account_resp.status_code == 200
    account_id = account_resp.json()["account"]["account_id"]
    assert account_resp.json()["account"]["balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]

    list_resp = client.get(f"/api/v1/books/{book_id}/accounts", headers=headers)
    assert list_resp.status_code == 200
    accounts_by_id = {account["account_id"]: account for account in list_resp.json()["accounts"]}
    assert accounts_by_id[account_id]["balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]


def test_api_account_summary_groups_real_accounts_by_subtype():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}

    cash_resp = client.post(
        "/api/v1/accounts",
        json={
            "name": "Summary WeChat",
            "type": "asset",
            "currency": "CNY",
            "opening_balance": "12.30",
            "institution_type": "e_wallet",
            "subtype": "ewallet_cash",
            "institution": "微信",
        },
        headers={**headers, "X-Idempotency-Key": "api-summary-wechat"},
    )
    assert cash_resp.status_code == 200

    summary_resp = client.get("/api/v1/summary/accounts?group_by=subtype&institution_type=e_wallet&currency=CNY", headers=headers)

    assert summary_resp.status_code == 200
    groups = summary_resp.json()["groups"]
    ewallet_cash = [group for group in groups if group["key"] == "ewallet_cash"]
    assert ewallet_cash
    assert Decimal(ewallet_cash[0]["amount"]) >= Decimal("12.30")
    assert "asset" in ewallet_cash[0]["types"]


def test_api_account_summary_separates_assets_and_liabilities():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}

    asset_resp = client.post(
        "/api/v1/accounts",
        json={
            "name": "Summary Bank Cash",
            "type": "asset",
            "currency": "CNY",
            "opening_balance": "100",
            "institution_type": "bank",
            "subtype": "debit_card",
            "institution": "Summary Bank",
        },
        headers={**headers, "X-Idempotency-Key": "api-summary-bank-asset"},
    )
    liability_resp = client.post(
        "/api/v1/accounts",
        json={
            "name": "Summary Bank Credit",
            "type": "liability",
            "currency": "CNY",
            "opening_balance": "30",
            "institution_type": "bank",
            "subtype": "credit_card",
            "institution": "Summary Bank",
        },
        headers={**headers, "X-Idempotency-Key": "api-summary-bank-liability"},
    )
    overpayment_resp = client.post(
        "/api/v1/accounts",
        json={
            "name": "Summary Bank Overpaid Credit",
            "type": "liability",
            "currency": "CNY",
            "opening_balance": "-10",
            "institution_type": "bank",
            "subtype": "credit_card",
            "institution": "Summary Bank",
        },
        headers={**headers, "X-Idempotency-Key": "api-summary-bank-overpaid-liability"},
    )
    assert asset_resp.status_code == 200
    assert liability_resp.status_code == 200
    assert overpayment_resp.status_code == 200
    assert asset_resp.json()["account"]["balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["asset"]
    assert liability_resp.json()["account"]["balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"]

    summary_resp = client.get("/api/v1/summary/accounts?group_by=institution&institution_type=bank&currency=CNY", headers=headers)

    assert summary_resp.status_code == 200
    assert summary_resp.json()["summary_semantics"] == account_summary_semantics_metadata()
    groups = summary_resp.json()["groups"]
    summary_bank = [group for group in groups if group["key"] == "Summary Bank"][0]
    assert summary_bank["amount"] == "120"
    assert summary_bank["asset_amount"] == "100"
    assert summary_bank["fund_amount"] == "0"
    assert summary_bank["system_amount"] == "0"
    assert summary_bank["liability_amount"] == "20"
    assert summary_bank["liability_outstanding_amount"] == "30"
    assert summary_bank["liability_overpayment_amount"] == "10"
    assert summary_bank["net_amount"] == "80"
    assert {key: summary_bank[key] for key in account_summary_group_semantics_fields()} == account_summary_group_semantics_fields()
    assert summary_bank["types"] == ["asset", "liability"]


def test_api_create_and_list_user():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}

    create_resp = client.post(
        "/api/v1/users",
        json={"username": "api_user", "display_name": "API User"},
        headers={**headers, "X-Idempotency-Key": "api-user-create"},
    )

    assert create_resp.status_code == 200
    user = create_resp.json()["user"]
    assert user["username"] == "api_user"
    assert user["display_name"] == "API User"

    list_resp = client.get("/api/v1/users", headers=headers)
    assert list_resp.status_code == 200
    assert any(item["username"] == "api_user" for item in list_resp.json()["users"])
