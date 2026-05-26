from __future__ import annotations

from uuid import uuid4

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from track_anywhere.api import app, service  # noqa: E402


def test_payment_instrument_api_create_list_and_credit_card_overview():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}
    suffix = uuid4().hex[:8]

    card_resp = client.post(
        "/api/v1/accounts",
        json={
            "name": f"交通银行信用卡共享额度 {suffix}",
            "type": "liability",
            "currency": "CNY",
            "institution_type": "bank",
            "subtype": "credit_card",
            "institution": "交通银行",
        },
        headers={**headers, "X-Idempotency-Key": f"api-instrument-card-{suffix}"},
    )
    assert card_resp.status_code == 200
    card_id = card_resp.json()["account"]["account_id"]

    create_resp = client.post(
        "/api/v1/payment-instruments",
        json={
            "slug": f"bocom-2862-{suffix}",
            "display_name": "交通银行实体卡(2862)",
            "kind": "credit_card",
            "account_id": card_id,
            "last4": "2862",
        },
        headers={**headers, "X-Idempotency-Key": f"api-instrument-{suffix}"},
    )
    assert create_resp.status_code == 200
    assert create_resp.json()["payment_instrument"]["account_id"] == card_id

    list_resp = client.get(f"/api/v1/payment-instruments?account_id={card_id}", headers=headers)
    assert list_resp.status_code == 200
    assert [item["slug"] for item in list_resp.json()["payment_instruments"]] == [f"bocom-2862-{suffix}"]

    show_resp = client.get(f"/api/v1/payment-instruments/bocom-2862-{suffix}", headers=headers)
    assert show_resp.status_code == 200
    assert show_resp.json()["payment_instrument"]["last4"] == "2862"

    credit_card_resp = client.get(f"/api/v1/credit-cards/{card_id}", headers=headers)
    assert credit_card_resp.status_code == 200
    instruments = credit_card_resp.json()["credit_card"]["instruments"]
    assert [item["slug"] for item in instruments] == [f"bocom-2862-{suffix}"]
