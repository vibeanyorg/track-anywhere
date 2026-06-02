from __future__ import annotations

from uuid import uuid4

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app, service
from track_anywhere.balance_semantics import ACCOUNT_TYPE_BALANCE_SEMANTICS
from track_anywhere.posting_semantics import canonical_posting_semantics_metadata


def test_api_ready_checks_database_and_migrations():
    assert app is not None
    response = TestClient(app).get("/api/v1/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"] == {"database": "ok", "migrations": "ok"}
    assert payload["alembic_revision"] == payload["expected_revision"]


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
            "memo": "Lunch with Alice, card ending 1234",
        },
        headers={**headers, "X-Idempotency-Key": "api-mvp-record"},
    )
    assert tx_resp.status_code == 200
    tx = tx_resp.json()["transaction"]
    assert tx["purpose"] == "lunch"
    assert tx["memo"] == "Lunch with Alice, card ending 1234"
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
    accounts_by_id = {account["account_id"]: account for account in account_list.json()["accounts"]}
    assert accounts_by_id[cash_id]["balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["asset"]
    assert accounts_by_id[expense_id]["balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["expense"]

    account_get = client.get(f"/api/v1/accounts/{cash_id}", headers=headers)
    assert account_get.status_code == 200
    assert account_get.json()["account"]["name"] == "Read Side Cash"
    assert account_get.json()["account"]["balance_semantics"] == ACCOUNT_TYPE_BALANCE_SEMANTICS["asset"]

    tx_list = client.get(f"/api/v1/ledger/transactions?account_id={cash_id}&limit=5", headers=headers)
    assert tx_list.status_code == 200
    assert any(transaction["transaction_id"] == transaction_id for transaction in tx_list.json()["transactions"])
    listed_transaction = next(
        transaction for transaction in tx_list.json()["transactions"] if transaction["transaction_id"] == transaction_id
    )
    assert listed_transaction["posting_semantics"]["canonical_model"] == canonical_posting_semantics_metadata()["canonical_model"]
    assert listed_transaction["posting_semantics"]["row_model"] == "debit_credit"
    assert listed_transaction["posting_semantics"]["debit_credit_amount_rule"] == canonical_posting_semantics_metadata()["debit_credit_amount_rule"]

    tx_get = client.get(f"/api/v1/ledger/transactions/{transaction_id}", headers=headers)
    assert tx_get.status_code == 200
    transaction_payload = tx_get.json()["transaction"]
    assert transaction_payload["posting_semantics"] == {
        **canonical_posting_semantics_metadata(),
        "row_model": "debit_credit",
        "amount_semantics": ["debit_credit"],
    }
    postings = transaction_payload["postings"]
    postings_by_account = {posting["account_id"]: posting for posting in postings}
    assert postings_by_account[cash_id]["side"] == "credit"
    assert postings_by_account[cash_id]["amount"] == "1.72"
    assert postings_by_account[cash_id]["amount_semantics"] == "debit_credit"
    assert postings_by_account[expense_id]["side"] == "debit"
    assert postings_by_account[expense_id]["amount"] == "1.72"
    assert postings_by_account[expense_id]["amount_semantics"] == "debit_credit"


def test_api_root_category_update_flow():
    assert app is not None
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}
    suffix = uuid4().hex[:8]
    create_resp = client.post(
        "/api/v1/categories",
        json={"kind": "expense", "name": f"Smoke Parent {suffix}"},
        headers={**headers, "X-Idempotency-Key": f"api-category-create-{suffix}"},
    )
    assert create_resp.status_code == 200
    category_id = create_resp.json()["category"]["category_id"]

    update_resp = client.patch(
        f"/api/v1/categories/{category_id}",
        json={"name": f"Smoke Parent Renamed {suffix}"},
        headers={**headers, "X-Idempotency-Key": f"api-category-update-{suffix}"},
    )

    assert update_resp.status_code == 200
    assert update_resp.json()["category"]["name"] == f"Smoke Parent Renamed {suffix}"
