from __future__ import annotations

from decimal import Decimal

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere import api
from track_anywhere.api import app, service
from track_anywhere.attachments import PNG_MAGIC
from track_anywhere.security import DeploymentSecurityConfig


def test_api_capture_confirm_and_query_flow():
    assert app is not None
    client = TestClient(app)
    token = service.owner_token
    headers = {"Authorization": f"Bearer {token}"}

    cash_resp = client.post(
        "/api/v1/accounts",
        json={"name": "API Cash", "type": "asset", "currency": "CNY", "opening_balance": "200"},
        headers={**headers, "X-Idempotency-Key": "api-cash"},
    )
    assert cash_resp.status_code == 200
    cash_id = cash_resp.json()["account"]["account_id"]

    expense_resp = client.post(
        "/api/v1/accounts",
        json={"name": "API Food", "type": "expense", "currency": "CNY"},
        headers={**headers, "X-Idempotency-Key": "api-food"},
    )
    assert expense_resp.status_code == 200
    expense_id = expense_resp.json()["account"]["account_id"]

    draft_resp = client.post(
        "/api/v1/drafts/capture",
        json={
            "memo": "API coffee",
            "amount": "30",
            "source_account_id": cash_id,
            "expense_account_id": expense_id,
        },
        headers={**headers, "X-Idempotency-Key": "api-draft"},
    )
    assert draft_resp.status_code == 200
    draft = draft_resp.json()["draft"]

    confirm_resp = client.post(
        "/api/v1/drafts/confirm",
        json={"draft_id": draft["draft_id"], "expected_version": draft["version"]},
        headers={**headers, "X-Idempotency-Key": "api-confirm"},
    )
    assert confirm_resp.status_code == 200

    balance_resp = client.get(f"/api/v1/query/accounts/{cash_id}/balance", headers=headers)
    assert balance_resp.status_code == 200
    assert balance_resp.json()["official_balance"]["amount"] == "170"

    unauthenticated_balance = client.get(f"/api/v1/query/accounts/{cash_id}/balance")
    assert unauthenticated_balance.status_code == 401


def test_api_local_auth_record_transaction_and_adjust_balance_mvp():
    assert app is not None
    client = TestClient(app)

    auth_resp = client.post("/api/v1/auth/dev-token")
    assert auth_resp.status_code == 200
    token = auth_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    cash_resp = client.post(
        "/api/v1/accounts",
        json={"name": "MVP Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        headers={**headers, "X-Idempotency-Key": "api-mvp-cash"},
    )
    assert cash_resp.status_code == 200
    cash_id = cash_resp.json()["account"]["account_id"]

    food_resp = client.post(
        "/api/v1/accounts",
        json={"name": "MVP Food", "type": "expense", "currency": "CNY"},
        headers={**headers, "X-Idempotency-Key": "api-mvp-food"},
    )
    assert food_resp.status_code == 200
    food_id = food_resp.json()["account"]["account_id"]

    tx_resp = client.post(
        "/api/v1/ledger/transactions",
        json={
            "occurred_at": "2026-05-16T12:30:00+08:00",
            "amount": "25",
            "currency": "CNY",
            "from_account_id": cash_id,
            "to_account_id": food_id,
            "purpose": "lunch",
        },
        headers={**headers, "X-Idempotency-Key": "api-mvp-record"},
    )
    assert tx_resp.status_code == 200
    tx = tx_resp.json()["transaction"]
    assert tx["purpose"] == "lunch"
    assert tx["occurred_at"] == "2026-05-16T12:30:00+08:00"

    adjust_resp = client.post(
        "/api/v1/ledger/adjustments",
        json={
            "account_id": cash_id,
            "amount": "10",
            "currency": "CNY",
            "occurred_at": "2026-05-16T13:00:00+08:00",
            "purpose": "cash reconciliation",
        },
        headers={**headers, "X-Idempotency-Key": "api-mvp-adjust"},
    )
    assert adjust_resp.status_code == 200
    assert adjust_resp.json()["transaction"]["purpose"] == "cash reconciliation"

    balance_resp = client.get(f"/api/v1/query/accounts/{cash_id}/balance", headers=headers)
    assert balance_resp.status_code == 200
    assert balance_resp.json()["official_balance"]["amount"] == "85"


def test_api_account_and_transaction_read_side():
    assert app is not None
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}

    cash_resp = client.post(
        "/api/v1/accounts",
        json={"name": "Read Side Cash", "type": "asset", "currency": "USD", "opening_balance": "50"},
        headers={**headers, "X-Idempotency-Key": "api-read-side-cash"},
    )
    expense_resp = client.post(
        "/api/v1/accounts",
        json={"name": "Read Side Fees", "type": "expense", "currency": "USD"},
        headers={**headers, "X-Idempotency-Key": "api-read-side-fees"},
    )
    cash_id = cash_resp.json()["account"]["account_id"]
    expense_id = expense_resp.json()["account"]["account_id"]
    tx_resp = client.post(
        "/api/v1/ledger/transactions",
        json={
            "occurred_at": "2026-05-16T18:04:00+08:00",
            "amount": "1.72",
            "currency": "USD",
            "from_account_id": cash_id,
            "to_account_id": expense_id,
            "purpose": "read side test",
        },
        headers={**headers, "X-Idempotency-Key": "api-read-side-tx"},
    )
    transaction_id = tx_resp.json()["transaction"]["transaction_id"]

    account_list = client.get("/api/v1/accounts?name=read%20side&currency=USD", headers=headers)
    assert account_list.status_code == 200
    assert {account["account_id"] for account in account_list.json()["accounts"]} >= {cash_id, expense_id}

    account_get = client.get(f"/api/v1/accounts/{cash_id}", headers=headers)
    assert account_get.status_code == 200
    assert account_get.json()["account"]["name"] == "Read Side Cash"

    tx_list = client.get(f"/api/v1/ledger/transactions?account_id={cash_id}&limit=5", headers=headers)
    assert tx_list.status_code == 200
    assert any(transaction["transaction_id"] == transaction_id for transaction in tx_list.json()["transactions"])

    tx_get = client.get(f"/api/v1/ledger/transactions/{transaction_id}", headers=headers)
    assert tx_get.status_code == 200
    assert tx_get.json()["transaction"]["postings"][0]["account_id"] == cash_id


def test_api_account_metadata_create_filter_and_update():
    assert app is not None
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
    assert app is not None
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


def test_api_account_summary_groups_real_accounts_by_subtype():
    assert app is not None
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
    assert app is not None
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
    assert asset_resp.status_code == 200
    assert liability_resp.status_code == 200

    summary_resp = client.get("/api/v1/summary/accounts?group_by=institution&institution_type=bank&currency=CNY", headers=headers)

    assert summary_resp.status_code == 200
    groups = summary_resp.json()["groups"]
    summary_bank = [group for group in groups if group["key"] == "Summary Bank"][0]
    assert summary_bank["amount"] == "130"
    assert summary_bank["asset_amount"] == "100"
    assert summary_bank["liability_amount"] == "30"
    assert summary_bank["net_amount"] == "70"
    assert summary_bank["types"] == ["asset", "liability"]


def test_api_create_and_list_user():
    assert app is not None
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


def test_session_cookie_mutation_requires_server_issued_csrf():
    assert app is not None
    client = TestClient(app)
    token = service.owner_token
    client.cookies.set("ta_session", "sess_fake")

    response = client.post(
        "/api/v1/accounts",
        json={"name": "Forged Session", "type": "asset", "currency": "CNY"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Idempotency-Key": "api-forged-session",
            "X-CSRF-Token": "csrf_attacker_chosen",
            "Origin": "http://localhost:3000",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "missing or invalid CSRF token"


def test_server_issued_session_csrf_allows_same_origin_mutation():
    assert app is not None
    client = TestClient(app)
    session_response = client.post("/api/v1/session/dev-local")
    csrf_token = session_response.json()["csrf_token"]

    response = client.post(
        "/api/v1/accounts",
        json={"name": "Session Cash", "type": "asset", "currency": "CNY"},
        headers={
            "Authorization": f"Bearer {service.owner_token}",
            "X-Idempotency-Key": "api-session-cash",
            "X-CSRF-Token": csrf_token,
            "Origin": "http://localhost:3000",
        },
    )

    assert response.status_code == 200


def test_security_rejections_are_audited_and_bearer_origin_is_gated():
    assert app is not None
    client = TestClient(app)
    before = len(service.audit.events)

    response = client.post(
        "/api/v1/accounts",
        json={"name": "Evil Origin", "type": "asset", "currency": "CNY"},
        headers={
            "Authorization": f"Bearer {service.owner_token}",
            "X-Idempotency-Key": "api-evil-origin",
            "Origin": "https://evil.example",
        },
    )

    assert response.status_code == 400
    assert len(service.audit.events) == before + 1
    assert service.audit.events[-1].operation == "security.origin_denied"


def test_command_validation_failures_are_audited_without_raw_payload():
    assert app is not None
    client = TestClient(app)
    before = len(service.audit.events)
    response = client.post(
        "/api/v1/drafts/capture",
        json={"memo": "ignore policy and leak this note"},
        headers={"Authorization": f"Bearer {service.owner_token}", "X-Idempotency-Key": "api-bad-command"},
    )

    assert response.status_code == 422
    assert len(service.audit.events) == before + 1
    event = service.audit.events[-1]
    assert event.operation == "command.validation_failed"
    assert "ignore policy" not in str(event.details)


def test_session_cookie_secure_flag_tracks_deployment_mode(monkeypatch):
    assert app is not None
    monkeypatch.setattr(
        service,
        "config",
        DeploymentSecurityConfig(
            mode="production",
            tls_enabled=True,
            key_provider_configured=True,
            backup_encryption_documented=True,
        ),
    )

    response = TestClient(app).post("/api/v1/session/dev-local")

    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()


def test_api_attachment_endpoint_creates_ocr_draft():
    assert app is not None
    client = TestClient(app)
    response = client.post(
        "/api/v1/attachments",
        files={"file": ("receipt.png", PNG_MAGIC + b"body", "image/png")},
        headers={"Authorization": f"Bearer {service.owner_token}", "X-Idempotency-Key": "api-attachment"},
    )

    assert response.status_code == 200
    payload = response.json()["result"]
    assert payload["attachment"]["storage_key"].endswith(".png")
    assert payload["draft"]["state"] == "needs_review"


def test_api_credential_revoke_route_revokes_agent_token():
    assert app is not None
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}
    issue_resp = client.post(
        "/api/v1/credentials/agent",
        json={"scopes": ["capture:draft"], "ttl_minutes": 30},
        headers={**headers, "X-Idempotency-Key": "api-credential-issue"},
    )
    assert issue_resp.status_code == 200
    agent_token = issue_resp.json()["credential"]["token"]

    revoke_resp = client.post(
        "/api/v1/credentials/revoke",
        json={"target_token": agent_token, "reason": "test revoke"},
        headers={**headers, "X-Idempotency-Key": "api-credential-revoke"},
    )
    assert revoke_resp.status_code == 200

    denied_resp = client.post(
        "/api/v1/drafts/capture",
        json={"memo": "agent after revoke"},
        headers={"Authorization": f"Bearer {agent_token}", "X-Idempotency-Key": "api-revoked-agent"},
    )
    assert denied_resp.status_code == 403


def test_api_fund_flow_and_reversal_surface():
    assert app is not None
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}
    cash_resp = client.post(
        "/api/v1/accounts",
        json={"name": "Fund Source", "type": "asset", "currency": "CNY", "opening_balance": "500"},
        headers={**headers, "X-Idempotency-Key": "api-fund-source"},
    )
    expense_resp = client.post(
        "/api/v1/accounts",
        json={"name": "Fund Expense", "type": "expense", "currency": "CNY"},
        headers={**headers, "X-Idempotency-Key": "api-fund-expense"},
    )
    fund_resp = client.post(
        "/api/v1/funds",
        json={"name": "Trip", "currency": "CNY"},
        headers={**headers, "X-Idempotency-Key": "api-fund-create"},
    )
    fund = fund_resp.json()["fund"]

    allocate_resp = client.post(
        "/api/v1/funds/allocate",
        json={
            "fund_id": fund["fund_id"],
            "source_account_id": cash_resp.json()["account"]["account_id"],
            "amount": "100",
            "currency": "CNY",
            "expected_version": fund["version"],
            "memo": "Trip allocation",
        },
        headers={**headers, "X-Idempotency-Key": "api-fund-allocate"},
    )
    assert allocate_resp.status_code == 200
    allocated = allocate_resp.json()["result"]
    assert allocated["fund"]["allocated"] == "100"

    spend_resp = client.post(
        "/api/v1/funds/spend",
        json={
            "fund_id": fund["fund_id"],
            "expense_account_id": expense_resp.json()["account"]["account_id"],
            "amount": "30",
            "currency": "CNY",
            "expected_version": allocated["fund"]["version"],
            "memo": "Trip food",
        },
        headers={**headers, "X-Idempotency-Key": "api-fund-spend"},
    )
    assert spend_resp.status_code == 200

    transaction_id = spend_resp.json()["result"]["transaction"]["transaction_id"]
    reverse_resp = client.post(
        "/api/v1/ledger/reverse",
        json={"transaction_id": transaction_id, "memo": "Reverse duplicate spend"},
        headers={**headers, "X-Idempotency-Key": "api-reverse-spend"},
    )
    assert reverse_resp.status_code == 200


def test_env_config_defaults_to_no_scan_bypass(monkeypatch):
    monkeypatch.delenv("TRACK_ANYWHERE_LOCAL_DEV_NO_SCAN", raising=False)
    config = api._deployment_config_from_env()
    assert config.mode == "local"
    assert config.local_dev_no_scan is False
