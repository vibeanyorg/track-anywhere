from __future__ import annotations

from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app, service
from track_anywhere.posting_semantics import canonical_posting_semantics_metadata


def _headers(idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {service.owner_token}"}
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    return headers


def test_category_path_ensure_and_lookup():
    assert app is not None
    client = TestClient(app)
    suffix = uuid4().hex[:8]
    path = f"稳定餐饮 {suffix} / 外出吃饭"

    ensure = client.post(
        "/api/v1/categories/ensure-path",
        json={"kind": "expense", "path": path},
        headers=_headers(f"stable-category-ensure-{suffix}"),
    )

    assert ensure.status_code == 200
    payload = ensure.json()
    assert payload["created"] is True
    assert len(payload["created_categories"]) == 2
    assert payload["category"]["path_cache"] == path

    found = client.get(
        "/api/v1/categories/by-path",
        params={"kind": "expense", "path": path},
        headers=_headers(),
    )

    assert found.status_code == 200
    assert found.json()["category"]["category_id"] == payload["category"]["category_id"]


def test_transaction_snapshot_includes_related_category_and_accounts():
    assert app is not None
    client = TestClient(app)
    suffix = uuid4().hex[:8]

    cash = client.post(
        "/api/v1/accounts",
        json={"name": f"Snapshot Cash {suffix}", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        headers=_headers(f"snapshot-cash-{suffix}"),
    )
    assert cash.status_code == 200
    category = client.post(
        "/api/v1/categories/ensure-path",
        json={"kind": "expense", "path": f"快照食品 {suffix} / 外卖"},
        headers=_headers(f"snapshot-category-{suffix}"),
    )
    assert category.status_code == 200
    tx = client.post(
        "/api/v1/expenses",
        json={
            "amount": "73",
            "currency": "CNY",
            "from_account_id": cash.json()["account"]["account_id"],
            "category_id": category.json()["category"]["category_id"],
            "purpose": "snapshot lunch",
        },
        headers=_headers(f"snapshot-expense-{suffix}"),
    )
    assert tx.status_code == 200
    transaction_id = tx.json()["transaction"]["transaction_id"]

    snapshot = client.get(f"/api/v1/ledger/transactions/{transaction_id}/snapshot", headers=_headers())

    assert snapshot.status_code == 200
    data = snapshot.json()["snapshot"]
    assert data["schema_version"] == "tx-snapshot.v1"
    assert data["posting_semantics"] == {
        **canonical_posting_semantics_metadata(),
        "row_model": "debit_credit",
        "amount_semantics": ["debit_credit"],
    }
    assert data["transaction"]["transaction_id"] == transaction_id
    assert data["transaction"]["posting_semantics"] == data["posting_semantics"]
    assert {account["account_id"] for account in data["accounts"]} >= {cash.json()["account"]["account_id"]}
    assert data["categories"][0]["category_id"] == category.json()["category"]["category_id"]
    assert data["category_versions"][0]["category_id"] == category.json()["category"]["category_id"]


def test_system_status_reports_database_actor_and_counts():
    assert app is not None
    client = TestClient(app)

    status = client.get("/api/v1/system/status", params={"include_counts": "true"}, headers=_headers())

    assert status.status_code == 200
    payload = status.json()
    assert payload["status"] == "ok"
    assert payload["checks"] == {"database": "ok", "migrations": "ok"}
    assert payload["actor"]["actor_id"] == "owner"
    assert isinstance(payload["counts"]["accounts"], int)
    assert isinstance(payload["counts"]["transaction_lines"], int)
