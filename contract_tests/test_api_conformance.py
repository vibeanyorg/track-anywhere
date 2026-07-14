from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import make_url

from .api_clients import BackendApiClient
from .helpers import auth_headers, bearer_headers, unique


SNAPSHOT = (
    Path(__file__).parents[1] / "backend" / "tests" / "snapshots" / "public-api-v2.json"
)


def test_public_route_contract_matches_snapshot(
    backend_client: BackendApiClient,
) -> None:
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    assert {"paths": backend_client.openapi_paths()} == expected
    assert all(path.startswith("/api/v2/") for path in expected["paths"])


def test_contract_runtime_is_isolated_postgresql_and_ready(
    backend_client: BackendApiClient,
) -> None:
    database_url = make_url(backend_client.database_url)
    health = backend_client.get("/api/v2/health")
    ready = backend_client.get("/api/v2/ready")

    assert database_url.drivername == "postgresql+psycopg"
    assert database_url.host in {"127.0.0.1", "localhost", "::1"}
    assert database_url.database is not None
    assert database_url.database.startswith("ta_v2_")
    assert health.status_code == 200
    assert health.data == {"status": "ok", "api_version": "v2"}
    assert ready.status_code == 200
    assert ready.data == {
        "status": "ok",
        "api_version": "v2",
        "checks": {"database": "ok", "schema": "ok"},
    }


def test_post_query_classify_and_reverse_contract(
    backend_client: BackendApiClient,
) -> None:
    ledger = _create_ledger(backend_client)
    transaction_id = str(uuid4())
    posted = backend_client.post(
        f"/api/v2/books/{ledger['book_id']}/journal/transactions",
        json_body=_transaction_payload(
            ledger,
            transaction_id=transaction_id,
            amount="12.34",
        ),
        headers={**auth_headers(backend_client), "X-Idempotency-Key": unique("post")},
    )

    assert posted.status_code == 201
    assert posted.data == {
        "transaction_id": transaction_id,
        "as_of_book_position": 1,
    }
    assert posted.headers["idempotency-replayed"] == "false"

    journal = backend_client.get(
        f"/api/v2/books/{ledger['book_id']}/journal?limit=10&as_of_book_position=1",
        headers=bearer_headers(backend_client),
    )
    balances = backend_client.get(
        f"/api/v2/books/{ledger['book_id']}/balances?as_of_book_position=1",
        headers=bearer_headers(backend_client),
    )

    assert journal.status_code == 200
    assert journal.data["as_of_book_position"] == 1
    assert journal.data["items"][0]["transaction_id"] == transaction_id
    assert [item["units"] for item in journal.data["items"][0]["postings"]] == [
        "1234",
        "1234",
    ]
    assert balances.status_code == 200
    assert sorted(item["units"] for item in balances.data["items"]) == [
        "-1234",
        "1234",
    ]

    classified = backend_client.post(
        f"/api/v2/books/{ledger['book_id']}/journal/transactions/"
        f"{transaction_id}/reporting-lines/assign",
        json_body={
            "command_id": str(uuid4()),
            "expected_revision": 0,
            "effective_at": "2026-07-14T12:31:00Z",
            "lines": [
                {
                    "line_id": str(uuid4()),
                    "line_version_id": str(uuid4()),
                    "catalog_id": ledger["category_version_id"],
                    "asset_code": "USD",
                    "units": "1234",
                    "line_kind": "expense",
                    "dimension": "category",
                    "dimension_id": ledger["category_id"],
                    "description_ref": None,
                }
            ],
        },
        headers={
            **auth_headers(backend_client),
            "X-Idempotency-Key": unique("classify"),
        },
    )
    assert classified.status_code == 201
    assert classified.data["classification_revision"] == 1
    assert classified.data["as_of_book_position"] == 2

    reporting = backend_client.get(
        f"/api/v2/books/{ledger['book_id']}/reporting-lines?as_of_book_position=2",
        headers=bearer_headers(backend_client),
    )
    assert reporting.status_code == 200
    assert reporting.data["items"][0]["units"] == "1234"

    reversal_id = str(uuid4())
    reversed_transaction = backend_client.post(
        f"/api/v2/books/{ledger['book_id']}/journal/transactions/"
        f"{transaction_id}/reverse",
        json_body={
            "command_id": str(uuid4()),
            "reversal_transaction_id": reversal_id,
            "expected_stream_version": 0,
            "reason_code": "duplicate",
            "effective_at": "2026-07-14T12:32:00Z",
            "description_ref": None,
        },
        headers={
            **auth_headers(backend_client),
            "X-Idempotency-Key": unique("reverse"),
        },
    )
    assert reversed_transaction.status_code == 201
    assert reversed_transaction.data == {
        "reversal_transaction_id": reversal_id,
        "reverses_transaction_id": transaction_id,
        "as_of_book_position": 3,
    }


def test_idempotency_replay_and_conflict_contract(
    backend_client: BackendApiClient,
) -> None:
    ledger = _create_ledger(backend_client)
    key = unique("idem")
    transaction_id = str(uuid4())
    payload = _transaction_payload(
        ledger,
        transaction_id=transaction_id,
        amount="9.99",
    )
    headers = {**auth_headers(backend_client), "X-Idempotency-Key": key}
    path = f"/api/v2/books/{ledger['book_id']}/journal/transactions"

    first = backend_client.post(path, json_body=payload, headers=headers)
    replay = backend_client.post(path, json_body=payload, headers=headers)
    conflict = backend_client.post(
        path,
        json_body={
            **payload,
            "postings": [
                {**posting, "amount": "10.00"} for posting in payload["postings"]
            ],
        },
        headers=headers,
    )

    assert (first.status_code, replay.status_code, conflict.status_code) == (
        201,
        201,
        409,
    )
    assert replay.data == first.data
    assert replay.headers["idempotency-replayed"] == "true"
    assert conflict.data == {"detail": "idempotency key conflict"}
    assert key not in json.dumps(conflict.data)


def test_validation_and_authentication_errors_are_fail_closed(
    backend_client: BackendApiClient,
) -> None:
    ledger = _create_ledger(backend_client)
    path = f"/api/v2/books/{ledger['book_id']}/journal/transactions"
    payload = _transaction_payload(
        ledger,
        transaction_id=str(uuid4()),
        amount="12.34",
    )

    unauthenticated = backend_client.post(
        path,
        json_body=payload,
        headers={"X-Idempotency-Key": unique("unauthenticated")},
    )
    missing_key = backend_client.post(
        path,
        json_body=payload,
        headers=auth_headers(backend_client),
    )
    invalid_amount = backend_client.post(
        path,
        json_body={
            **payload,
            "postings": [
                {**posting, "amount": 12.34} for posting in payload["postings"]
            ],
        },
        headers={
            **auth_headers(backend_client),
            "X-Idempotency-Key": unique("invalid"),
        },
    )

    assert unauthenticated.status_code == 401
    assert unauthenticated.data == {"detail": "authentication is required"}
    assert missing_key.status_code == 400
    assert missing_key.data == {"detail": "X-Idempotency-Key is required"}
    assert invalid_amount.status_code == 422
    assert "traceback" not in json.dumps(invalid_amount.data).lower()


def test_api_key_session_and_oauth_metadata_contract(
    backend_client: BackendApiClient,
) -> None:
    authorization_server = backend_client.get("/api/v2/oauth/authorization-server")
    protected_resource = backend_client.get("/api/v2/oauth/protected-resource")
    login = backend_client.post(
        "/api/v2/auth/session/api-key",
        json_body={"api_key": backend_client.api_key},
    )

    assert authorization_server.status_code == 200
    assert authorization_server.data["token_endpoint"].endswith("/api/v2/oauth/token")
    assert protected_resource.status_code == 200
    assert protected_resource.data["resource"].endswith("/api/v2")
    assert login.status_code == 200
    assert login.data["authenticated"] is True
    assert backend_client.api_key not in json.dumps(login.data)

    current = backend_client.get("/api/v2/auth/session")
    status = backend_client.get(
        "/api/v2/auth/token-status",
        headers=bearer_headers(backend_client),
    )
    assert current.data["authenticated"] is True
    assert current.data["identity"]["user_id"] == "human:contract-v2"
    assert status.status_code == 200
    assert status.data["auth_kind"] == "api_key"

    rejected = backend_client.post("/api/v2/auth/logout")
    logout = backend_client.post(
        "/api/v2/auth/logout",
        headers={
            "X-CSRF-Token": login.data["csrf_token"],
            "Origin": "http://testserver",
        },
    )
    assert rejected.status_code == 403
    assert logout.status_code == 200
    assert backend_client.get("/api/v2/auth/session").data == {
        "authenticated": False,
        "identity": None,
    }


def _create_ledger(client: BackendApiClient) -> dict[str, str]:
    values = {
        "book_id": str(uuid4()),
        "debit_account_id": str(uuid4()),
        "credit_account_id": str(uuid4()),
        "category_id": str(uuid4()),
        "category_version_id": str(uuid4()),
    }
    headers = auth_headers(client)
    book = client.post(
        "/api/v2/books",
        json_body={
            "book_id": values["book_id"],
            "current_name": "Contract Book",
            "base_asset_code": None,
        },
        headers=headers,
    )
    asset = client.post(
        f"/api/v2/books/{values['book_id']}/assets",
        json_body={
            "asset_code": "USD",
            "kind": "fiat",
            "ledger_scale": 2,
            "input_scale": 2,
            "display_scale": 2,
            "current_name": "US Dollar",
        },
        headers=headers,
    )
    debit = client.post(
        f"/api/v2/books/{values['book_id']}/accounts",
        json_body={
            "account_id": values["debit_account_id"],
            "asset_code": "USD",
            "account_type": "expense",
            "current_name": "Expense",
            "system_role": None,
        },
        headers=headers,
    )
    credit = client.post(
        f"/api/v2/books/{values['book_id']}/accounts",
        json_body={
            "account_id": values["credit_account_id"],
            "asset_code": "USD",
            "account_type": "asset",
            "current_name": "Cash",
            "system_role": None,
        },
        headers=headers,
    )
    category = client.post(
        f"/api/v2/books/{values['book_id']}/categories",
        json_body={
            "category_id": values["category_id"],
            "category_version_id": values["category_version_id"],
            "name": "Food",
            "parent_category_id": None,
            "change_reason_code": "created",
        },
        headers=headers,
    )
    assert [
        book.status_code,
        asset.status_code,
        debit.status_code,
        credit.status_code,
        category.status_code,
    ] == [201, 201, 201, 201, 201]
    return values


def _transaction_payload(
    ledger: dict[str, str],
    *,
    transaction_id: str,
    amount: Any,
) -> dict[str, Any]:
    return {
        "command_id": str(uuid4()),
        "transaction_id": transaction_id,
        "expected_stream_version": 0,
        "kind": "standard",
        "effective_at": "2026-07-14T12:30:00Z",
        "description_ref": None,
        "external_references": [],
        "postings": [
            {
                "posting_id": str(uuid4()),
                "account_id": ledger["debit_account_id"],
                "asset_code": "USD",
                "side": "debit",
                "amount": amount,
            },
            {
                "posting_id": str(uuid4()),
                "account_id": ledger["credit_account_id"],
                "asset_code": "USD",
                "side": "credit",
                "amount": amount,
            },
        ],
    }
