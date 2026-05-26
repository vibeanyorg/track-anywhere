from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from track_anywhere.api import app, service  # noqa: E402


def test_payment_profile_api_create_list_and_expense_replay():
    assert app is not None
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}
    suffix = uuid4().hex[:8]

    category_resp = client.post(
        "/api/v1/categories",
        json={"kind": "expense", "name": f"SafePal API Food {suffix}"},
        headers={**headers, "X-Idempotency-Key": f"api-payment-category-{suffix}"},
    )
    assert category_resp.status_code == 200
    category_id = category_resp.json()["category"]["category_id"]

    card_resp = client.post(
        "/api/v1/accounts",
        json={"name": f"SafePal Card API {suffix}", "type": "asset", "currency": "USD"},
        headers={**headers, "X-Idempotency-Key": f"api-payment-card-{suffix}"},
    )
    assert card_resp.status_code == 200
    card_id = card_resp.json()["account"]["account_id"]

    usd24_resp = client.post(
        "/api/v1/accounts",
        json={
            "name": f"SafePal USD24 API {suffix}",
            "type": "asset",
            "currency": "USD24",
            "opening_balance": "10.00",
        },
        headers={**headers, "X-Idempotency-Key": f"api-payment-usd24-{suffix}"},
    )
    assert usd24_resp.status_code == 200
    usd24_id = usd24_resp.json()["account"]["account_id"]

    profile_resp = client.post(
        "/api/v1/payment-profiles",
        json={
            "slug": f"safepal-{suffix}",
            "display_name": "SafePal",
            "kind": "token_backed_card",
            "instrument_account_id": card_id,
            "backing_account_id": usd24_id,
            "settlement_mode": "immediate",
            "settlement_rate": "1",
        },
        headers={**headers, "X-Idempotency-Key": f"api-payment-profile-{suffix}"},
    )
    assert profile_resp.status_code == 200
    assert profile_resp.json()["payment_profile"]["slug"] == f"safepal-{suffix}"

    list_resp = client.get("/api/v1/payment-profiles", headers=headers)
    assert list_resp.status_code == 200
    assert any(profile["slug"] == f"safepal-{suffix}" for profile in list_resp.json()["payment_profiles"])

    status_before = client.get(f"/api/v1/payment-profiles/safepal-{suffix}/status", headers=headers)
    assert status_before.status_code == 200
    assert status_before.json()["backing_balance"] == {"account_id": usd24_id, "amount": "10.00", "currency": "USD24"}
    assert status_before.json()["effective_instrument_balance"] == {
        "account_id": card_id,
        "amount": "10.00",
        "currency": "USD",
    }
    assert status_before.json()["instrument_clearing_balance"] == {"account_id": card_id, "amount": "0", "currency": "USD"}

    expense_payload = {
        "amount": "3.40",
        "currency": "USD",
        "category_id": category_id,
        "purpose": "Meituan",
    }
    expense_resp = client.post(
        f"/api/v1/payment-profiles/safepal-{suffix}/expenses",
        json=expense_payload,
        headers={**headers, "X-Idempotency-Key": f"api-payment-expense-{suffix}"},
    )
    assert expense_resp.status_code == 200
    assert len(expense_resp.json()["transaction"]["postings"]) == 6
    assert expense_resp.json()["idempotent_replay"] is False

    replay_resp = client.post(
        f"/api/v1/payment-profiles/safepal-{suffix}/expenses",
        json=expense_payload,
        headers={**headers, "X-Idempotency-Key": f"api-payment-expense-{suffix}"},
    )
    assert replay_resp.status_code == 200
    assert replay_resp.json()["idempotent_replay"] is True

    card_balance = client.get(f"/api/v1/query/accounts/{card_id}/balance", headers=headers).json()
    usd24_balance = client.get(f"/api/v1/query/accounts/{usd24_id}/balance", headers=headers).json()
    assert Decimal(card_balance["official_balance"]["amount"]) == Decimal("0")
    assert Decimal(usd24_balance["official_balance"]["amount"]) == Decimal("6.60")

    status_after = client.get(f"/api/v1/payment-profiles/safepal-{suffix}/status", headers=headers)
    assert status_after.status_code == 200
    assert status_after.json()["backing_balance"]["amount"] == "6.60"
    assert status_after.json()["effective_instrument_balance"]["amount"] == "6.60"
    assert Decimal(status_after.json()["instrument_clearing_balance"]["amount"]) == Decimal("0")
