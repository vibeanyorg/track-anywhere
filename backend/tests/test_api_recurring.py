from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app, service


def test_api_recurring_create_remind_and_generate_draft_flow():
    assert app is not None
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}

    account_resp = client.post(
        "/api/v1/accounts",
        json={"name": "Recurring API USD", "type": "asset", "currency": "USD", "opening_balance": "100"},
        headers={**headers, "X-Idempotency-Key": "api-recurring-usd"},
    )
    category_resp = client.post(
        "/api/v1/categories",
        json={"kind": "expense", "primary": "Subscriptions", "secondary": "API"},
        headers={**headers, "X-Idempotency-Key": "api-recurring-category"},
    )
    assert account_resp.status_code == 200
    assert category_resp.status_code == 200

    recurring_resp = client.post(
        "/api/v1/recurring/items",
        json={
            "name": "ChatGPT API",
            "kind": "paid",
            "provider": "OpenAI",
            "amount": "20",
            "currency": "USD",
            "recurrence": {"type": "monthly_day", "day": 15},
            "anchor_date": "2026-06-15",
            "reminder_days": [3, 2, 1],
            "source_account_id": account_resp.json()["account"]["account_id"],
            "category_id": category_resp.json()["category"]["category_id"],
        },
        headers={**headers, "X-Idempotency-Key": "api-recurring-chatgpt"},
    )
    assert recurring_resp.status_code == 200
    recurring_id = recurring_resp.json()["recurring_item"]["recurring_id"]

    reminders_resp = client.get("/api/v1/recurring/reminders?as_of=2026-06-12&window_days=0", headers=headers)
    assert reminders_resp.status_code == 200
    reminders = reminders_resp.json()["reminders"]
    assert any(item["recurring_id"] == recurring_id and item["lead_days"] == 3 for item in reminders)

    draft_resp = client.post(
        "/api/v1/recurring/drafts",
        json={"as_of": "2026-06-16"},
        headers={**headers, "X-Idempotency-Key": "api-recurring-generate-june"},
    )
    assert draft_resp.status_code == 200
    created = draft_resp.json()["result"]["created"][0]
    assert created["recurring_id"] == recurring_id
    assert service.drafts.get(created["draft_id"]).source == "recurring"

    cancel_resp = client.patch(
        f"/api/v1/recurring/items/{recurring_id}",
        json={"status": "cancelled"},
        headers={**headers, "X-Idempotency-Key": "api-recurring-cancel"},
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["recurring_item"]["status"] == "cancelled"
