from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app, service
from track_anywhere.posting_semantics import PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS


def test_posting_semantics_mutations_require_idempotency_key():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}

    rewrite_response = client.post("/api/v1/system/posting-semantics-rewrite", headers=headers, json={})
    resolve_response = client.post(
        "/api/v1/system/posting-semantics-review-resolutions",
        headers=headers,
        json={
            "decisions": [
                {
                    "transaction_id": "txn_missing_key",
                    "position": 0,
                    "account_id": "acc_card",
                    "currency": "USD",
                    "legacy_amount": "-1",
                    "action": "confirm_as_outstanding_liability",
                }
            ]
        },
    )

    assert rewrite_response.status_code == 400
    assert resolve_response.status_code == 400
    assert rewrite_response.json()["detail"] == "missing idempotency key"
    assert resolve_response.json()["detail"] == "missing idempotency key"


@pytest.mark.parametrize(
    "path,payload",
    [
        (
            "/api/v1/ledger/transactions",
            {
                "amount": "10",
                "currency": "USD",
                "from_account_id": "acc_cash",
                "to_account_id": "acc_food",
                "purpose": "raw posting injection",
            },
        ),
        (
            "/api/v1/accounts",
            {
                "name": "Raw Field Cash",
                "type": "asset",
                "currency": "USD",
                "opening_balance": "10",
            },
        ),
        (
            "/api/v1/books/book_1/accounts",
            {
                "name": "Raw Field Book Cash",
                "type": "asset",
                "currency": "USD",
                "opening_balance": "10",
            },
        ),
        (
            "/api/v1/books/book_1/transactions",
            {
                "amount": "10",
                "currency": "USD",
                "from_account_id": "acc_cash",
                "to_account_id": "acc_food",
                "purpose": "raw posting injection",
            },
        ),
        (
            "/api/v1/expenses",
            {
                "amount": "10",
                "currency": "USD",
                "from_account_id": "acc_card",
                "category_id": "cat_food",
                "purpose": "raw posting injection",
            },
        ),
        (
            "/api/v1/incomes",
            {
                "amount": "10",
                "currency": "USD",
                "to_account_id": "acc_cash",
                "category_id": "cat_salary",
                "purpose": "raw posting injection",
            },
        ),
        (
            "/api/v1/ledger/adjustments",
            {
                "account_id": "acc_cash",
                "amount": "10",
                "currency": "USD",
                "purpose": "raw posting injection",
            },
        ),
        (
            "/api/v1/drafts/capture",
            {
                "memo": "raw posting injection",
                "amount": "10",
                "currency": "USD",
                "source_account_id": "acc_cash",
                "expense_account_id": "acc_food",
            },
        ),
        (
            "/api/v1/drafts/confirm",
            {
                "draft_id": "draft_1",
                "expected_version": 1,
            },
        ),
        (
            "/api/v1/drafts/supersede",
            {
                "draft_id": "draft_1",
                "expected_version": 1,
                "replacement": {
                    "memo": "replacement",
                    "amount": "10",
                    "currency": "USD",
                    "source_account_id": "acc_cash",
                    "expense_account_id": "acc_food",
                },
            },
        ),
        (
            "/api/v1/ledger/reverse",
            {
                "transaction_id": "txn_1",
                "memo": "reverse raw posting injection",
            },
        ),
        (
            "/api/v1/books/book_1/transactions/txn_1/reverse",
            {
                "memo": "reverse raw posting injection",
            },
        ),
        (
            "/api/v1/ledger/fx-exchanges",
            {
                "from_account_id": "acc_cny",
                "from_amount": "70",
                "from_currency": "CNY",
                "to_account_id": "acc_usd",
                "to_amount": "10",
                "to_currency": "USD",
            },
        ),
        (
            "/api/v1/investments/events",
            {
                "account_id": "acc_brokerage",
                "event_type": "buy",
                "amount": "10",
                "currency": "USD",
            },
        ),
        (
            "/api/v1/funds/allocate",
            {
                "fund_id": "fund_1",
                "source_account_id": "acc_cash",
                "amount": "10",
                "currency": "USD",
                "expected_version": 1,
            },
        ),
        (
            "/api/v1/funds/spend",
            {
                "fund_id": "fund_1",
                "expense_account_id": "acc_food",
                "amount": "10",
                "currency": "USD",
                "expected_version": 1,
            },
        ),
        (
            "/api/v1/recurring/items",
            {
                "name": "Subscription",
                "kind": "paid",
                "amount": "10",
                "currency": "USD",
                "recurrence": {"type": "monthly_day", "day": 1},
                "reminder_days": [1],
                "anchor_date": "2026-01-01",
                "source_account_id": "acc_cash",
                "category_id": "cat_subscription",
            },
        ),
        (
            "/api/v1/books/book_1/recurring/items",
            {
                "name": "Book Subscription",
                "kind": "paid",
                "amount": "10",
                "currency": "USD",
                "recurrence": {"type": "monthly_day", "day": 1},
                "reminder_days": [1],
                "anchor_date": "2026-01-01",
                "source_account_id": "acc_cash",
                "category_id": "cat_subscription",
            },
        ),
        (
            "/api/v1/recurring/drafts",
            {},
        ),
        (
            "/api/v1/books/book_1/recurring/drafts",
            {},
        ),
        (
            "/api/v1/payment-profiles/virtual_card/expenses",
            {
                "amount": "10",
                "currency": "USD",
                "category_id": "cat_food",
                "purpose": "raw posting injection",
            },
        ),
    ],
)
@pytest.mark.parametrize("forbidden_field", PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS)
def test_public_api_write_commands_reject_raw_posting_semantics(path, payload, forbidden_field):
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {service.owner_token}",
        "X-Idempotency-Key": f"reject-raw-posting-{path.strip('/').replace('/', '-')}-{forbidden_field}",
    }

    response = client.post(path, headers=headers, json={**payload, forbidden_field: "not allowed"})

    assert response.status_code == 422


@pytest.mark.parametrize("forbidden_field", PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS)
def test_public_api_patch_commands_reject_raw_posting_semantics(forbidden_field):
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {service.owner_token}",
        "X-Idempotency-Key": f"reject-raw-posting-recurring-update-{forbidden_field}",
    }

    response = client.patch(
        "/api/v1/recurring/items/recurring_1",
        headers=headers,
        json={"amount": "10", "currency": "USD", forbidden_field: "not allowed"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("forbidden_field", PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS)
def test_public_api_nested_draft_replacement_rejects_raw_posting_semantics(forbidden_field):
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {service.owner_token}",
        "X-Idempotency-Key": f"reject-raw-posting-supersede-nested-{forbidden_field}",
    }

    response = client.post(
        "/api/v1/drafts/supersede",
        headers=headers,
        json={
            "draft_id": "draft_1",
            "expected_version": 1,
            "replacement": {
                "memo": "replacement",
                "amount": "10",
                "currency": "USD",
                "source_account_id": "acc_cash",
                "expense_account_id": "acc_food",
                forbidden_field: "not allowed",
            },
        },
    )

    assert response.status_code == 422
