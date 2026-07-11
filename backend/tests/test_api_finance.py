from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app, service
from track_anywhere.attachments import MAX_ATTACHMENT_BYTES, PNG_MAGIC


def test_api_attachment_endpoint_creates_ocr_draft(monkeypatch):
    assert app is not None
    monkeypatch.setattr(service.attachments, "scanner", type("Scanner", (), {"scan": lambda self, content: None})())
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


def test_api_attachment_endpoint_rejects_oversized_upload_before_intake():
    assert app is not None
    client = TestClient(app)
    oversized_png = PNG_MAGIC + b"x" * (MAX_ATTACHMENT_BYTES + 1 - len(PNG_MAGIC))

    response = client.post(
        "/api/v1/attachments",
        files={"file": ("too-large.png", oversized_png, "image/png")},
        headers={"Authorization": f"Bearer {service.owner_token}", "X-Idempotency-Key": "api-attachment-too-large"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "attachment exceeds size limit"


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


def test_api_machine_credential_allows_local_durable_ttl():
    assert app is not None
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}", "X-Idempotency-Key": "api-machine-durable-ttl"}
    common_local_scopes = [
        "account:read",
        "account:write",
        "book:read",
        "budget:read",
        "budget:write",
        "capture:draft",
        "category:read",
        "category:write",
        "credit-card:read",
        "credit-card:write",
        "investment:read",
        "investment:write",
        "ledger:confirm",
        "ledger:read",
        "ledger:reverse",
        "recurring:read",
        "recurring:write",
        "user:read",
        "user:write",
    ]

    response = client.post(
        "/api/v1/credentials/machine",
        json={
            "name": "Stable local smoke token",
            "description": "pytest durable token",
            "scopes": common_local_scopes,
            "ttl_minutes": 3650 * 24 * 60,
        },
        headers=headers,
    )

    assert response.status_code == 200
    credential = response.json()["credential"]
    assert credential["token"].startswith("ta_m2m_")
    assert credential["scopes"] == common_local_scopes
    assert credential["ttl_minutes"] == 3650 * 24 * 60


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
