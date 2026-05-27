from __future__ import annotations

from uuid import uuid4

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from track_anywhere.api import app, service  # noqa: E402


def test_counterparty_can_be_ensured_used_and_filtered_on_expenses():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}
    suffix = uuid4().hex[:8]

    account_resp = client.post(
        "/api/v1/accounts",
        json={"name": f"Counterparty Cash {suffix}", "type": "asset", "currency": "CNY"},
        headers={**headers, "X-Idempotency-Key": f"counterparty-account-{suffix}"},
    )
    assert account_resp.status_code == 200
    account_id = account_resp.json()["account"]["account_id"]

    category_resp = client.post(
        "/api/v1/categories",
        json={"kind": "expense", "name": f"Counterparty Food {suffix}"},
        headers={**headers, "X-Idempotency-Key": f"counterparty-category-{suffix}"},
    )
    assert category_resp.status_code == 200
    category_id = category_resp.json()["category"]["category_id"]

    ensure_resp = client.post(
        "/api/v1/counterparties/ensure",
        json={"name": f"美团 {suffix}", "kind": "merchant"},
        headers={**headers, "X-Idempotency-Key": f"counterparty-ensure-{suffix}"},
    )
    assert ensure_resp.status_code == 200
    counterparty = ensure_resp.json()["counterparty"]
    counterparty_id = counterparty["counterparty_id"]

    list_resp = client.get("/api/v1/counterparties?kind=merchant", headers=headers)
    assert list_resp.status_code == 200
    assert any(item["counterparty_id"] == counterparty_id for item in list_resp.json()["counterparties"])

    expense_resp = client.post(
        "/api/v1/expenses",
        json={
            "amount": "12.30",
            "currency": "CNY",
            "from_account_id": account_id,
            "category_id": category_id,
            "purpose": "午餐",
            "counterparty": counterparty_id,
        },
        headers={**headers, "X-Idempotency-Key": f"counterparty-expense-{suffix}"},
    )
    assert expense_resp.status_code == 200
    transaction = expense_resp.json()["transaction"]
    assert transaction["lines"][0]["counterparty_id"] == counterparty_id
    assert "merchant_id" not in transaction["lines"][0]

    filtered_resp = client.get(f"/api/v1/ledger/transactions?counterparty={counterparty_id}", headers=headers)
    assert filtered_resp.status_code == 200
    assert [item["transaction_id"] for item in filtered_resp.json()["transactions"]] == [transaction["transaction_id"]]

    missing_resp = client.get("/api/v1/ledger/transactions?counterparty=missing-counterparty", headers=headers)
    assert missing_resp.status_code == 200
    assert all(item["transaction_id"] != transaction["transaction_id"] for item in missing_resp.json()["transactions"])


def test_record_expense_requires_existing_counterparty():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}
    suffix = uuid4().hex[:8]

    account_resp = client.post(
        "/api/v1/accounts",
        json={"name": f"Strict Counterparty Cash {suffix}", "type": "asset", "currency": "CNY"},
        headers={**headers, "X-Idempotency-Key": f"strict-counterparty-account-{suffix}"},
    )
    assert account_resp.status_code == 200
    account_id = account_resp.json()["account"]["account_id"]

    category_resp = client.post(
        "/api/v1/categories",
        json={"kind": "expense", "name": f"Strict Counterparty Food {suffix}"},
        headers={**headers, "X-Idempotency-Key": f"strict-counterparty-category-{suffix}"},
    )
    assert category_resp.status_code == 200
    category_id = category_resp.json()["category"]["category_id"]

    expense_resp = client.post(
        "/api/v1/expenses",
        json={
            "amount": "9.90",
            "currency": "CNY",
            "from_account_id": account_id,
            "category_id": category_id,
            "purpose": "coffee",
            "counterparty": f"New Shop {suffix}",
        },
        headers={**headers, "X-Idempotency-Key": f"strict-counterparty-expense-{suffix}"},
    )

    assert expense_resp.status_code == 404
    assert "counterparty" in expense_resp.json()["detail"]
