from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from track_anywhere.api import app


def test_public_api_v1_route_snapshot():
    assert app is not None
    actual = {
        "paths": {
            path: sorted(method for method in details if method in {"get", "post", "put", "patch", "delete"})
            for path, details in sorted(app.openapi()["paths"].items())
        }
    }
    expected = json.loads((Path(__file__).parent / "snapshots" / "public-api-v1.json").read_text())

    assert actual == expected


def test_public_mutation_routes_expose_request_schemas():
    assert app is not None
    openapi = app.openapi()

    schema_refs = {}
    for path, details in openapi["paths"].items():
        if path in {"/api/v1/attachments", "/api/v1/auth/dev-token", "/api/v1/auth/logout", "/api/v1/session/dev-local"}:
            continue
        for method in ("post", "patch"):
            if method in details:
                schema_refs[path] = details[method]["requestBody"]["content"]["application/json"]["schema"].get("$ref")

    assert schema_refs["/api/v1/accounts"].endswith("/CreateAccountCommand")
    assert schema_refs["/api/v1/drafts/capture"].endswith("/CaptureDraftCommand")
    assert schema_refs["/api/v1/ledger/transactions"].endswith("/RecordTransactionCommand")
    assert schema_refs["/api/v1/recurring/items"].endswith("/CreateRecurringItemCommand")
    assert schema_refs["/api/v1/recurring/items/{recurring_id}"].endswith("/UpdateRecurringItemCommand")
    assert schema_refs["/api/v1/recurring/drafts"].endswith("/GenerateRecurringDraftsCommand")
    assert schema_refs["/api/v1/credentials/revoke"].endswith("/RevokeCredentialCommand")
    assert all(ref and not ref.endswith("/dict_str__Any_") for ref in schema_refs.values())
