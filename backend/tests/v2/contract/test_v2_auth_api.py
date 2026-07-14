from __future__ import annotations

from collections.abc import Iterator
import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from track_anywhere.infrastructure.db.models.auth import (
    BrowserSessionRecord,
    CredentialRecord,
    OAuthAuthorizationGrantRecord,
    OAuthClientRecord,
    OAuthDeviceGrantRecord,
    UserRecord,
)


def _unused_session() -> Iterator[Session]:
    raise AssertionError("metadata routes must not open a database session")
    yield


def _seed_api_key(pg_engine, raw_api_key: str):
    issued_at = datetime.now(UTC)
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    with factory.begin() as session:
        user = UserRecord(
            user_id="human:test",
            subject_type="human",
            current_display_name="Test User",
            status="active",
        )
        session.add(user)
        session.flush([user])
        session.add(
            CredentialRecord(
                credential_id=uuid4(),
                token_hash=sha256(raw_api_key.encode()).digest(),
                jti=uuid4(),
                actor_subject_id="human:test",
                actor_type="human",
                auth_kind="api_key",
                book_id=None,
                scopes=["book:read", "ledger:read"],
                issued_at=issued_at,
                expires_at=issued_at + timedelta(hours=1),
                revoked_at=None,
                last_used_at=None,
            )
        )

    def get_session() -> Iterator[Session]:
        with factory() as session, session.begin():
            yield session

    return factory, get_session


def test_oauth_discovery_advertises_only_v2_endpoints() -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    app = FastAPI()
    app.include_router(create_auth_router(_unused_session))
    response = TestClient(app).get("/api/v2/oauth/authorization-server")

    assert response.status_code == 200
    metadata = response.json()
    assert metadata["authorization_endpoint"].endswith("/api/v2/oauth/authorize")
    assert metadata["token_endpoint"].endswith("/api/v2/oauth/token")
    assert metadata["device_authorization_endpoint"].endswith(
        "/api/v2/oauth/device/authorize"
    )
    assert metadata["registration_endpoint"].endswith("/api/v2/oauth/register")
    assert metadata["revocation_endpoint"].endswith("/api/v2/oauth/revoke")
    assert metadata["scopes_supported"] == [
        "book:read",
        "book:write",
        "ledger:read",
        "ledger:write",
    ]
    assert "/api/v1" not in response.text

    protected = TestClient(app).get("/api/v2/oauth/protected-resource")
    assert protected.status_code == 200
    assert protected.json()["resource"].endswith("/api/v2")
    assert protected.json()["scopes_supported"] == metadata["scopes_supported"]
    assert "/api/v1" not in protected.text


def test_api_key_login_persists_binary_hashed_browser_session(pg_engine) -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    raw_api_key = "ta_seed_api_key"
    factory, get_session = _seed_api_key(pg_engine, raw_api_key)

    app = FastAPI()
    app.include_router(create_auth_router(get_session))
    first_client = TestClient(app)
    login = first_client.post(
        "/api/v2/auth/session/api-key",
        json={"api_key": raw_api_key},
    )

    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    csrf_token = login.json()["csrf_token"]
    raw_session = login.cookies["ta_session"]
    assert raw_api_key not in login.text

    second_app = FastAPI()
    second_app.include_router(create_auth_router(get_session))
    second_client = TestClient(second_app)
    second_client.cookies.set("ta_session", raw_session)
    current = second_client.get("/api/v2/auth/session")

    assert current.status_code == 200
    assert current.json() == {
        "authenticated": True,
        "identity": {
            "user_id": "human:test",
            "display_name": "Test User",
            "subject_type": "human",
            "auth_kind": "browser_session",
            "book_id": None,
            "scopes": ["book:read", "ledger:read"],
        },
    }

    with factory() as session:
        stored = session.execute(select(BrowserSessionRecord)).scalar_one()
        assert type(stored.session_hash) is bytes
        assert type(stored.csrf_token_hash) is bytes
        assert type(stored.credential_hash) is bytes
        assert stored.session_hash == sha256(raw_session.encode()).digest()
        assert stored.csrf_token_hash == sha256(csrf_token.encode()).digest()
        assert len(stored.session_hash) == len(stored.csrf_token_hash) == 32

    rejected = second_client.post("/api/v2/auth/logout")
    assert rejected.status_code == 403
    logout = second_client.post(
        "/api/v2/auth/logout",
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
    )
    assert logout.status_code == 200
    assert second_client.get("/api/v2/auth/session").json()["authenticated"] is False


def test_pkce_grant_and_access_token_are_persistent_single_use_binary_hashes(
    pg_engine,
) -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    raw_api_key = "ta_pkce_seed"
    factory, get_session = _seed_api_key(pg_engine, raw_api_key)
    app = FastAPI()
    app.include_router(create_auth_router(get_session))
    client = TestClient(app)

    registration = client.post(
        "/api/v2/oauth/register",
        json={
            "client_name": "Test CLI",
            "redirect_uris": ["http://localhost:49152/api/v2/auth/callback"],
            "scope": "book:read ledger:read",
            "token_endpoint_auth_method": "none",
        },
    )
    assert registration.status_code == 200
    client_id = registration.json()["client_id"]

    login = client.post(
        "/api/v2/auth/session/api-key",
        json={"api_key": raw_api_key},
    )
    verifier = "v" * 64
    challenge = (
        base64.urlsafe_b64encode(sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    authorization = client.post(
        "/api/v2/oauth/authorize",
        json={
            "client_id": client_id,
            "redirect_uri": "http://localhost:49152/api/v2/auth/callback",
            "scope": "book:read ledger:read",
            "state": "state-1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "action": "approve",
        },
        headers={
            "X-CSRF-Token": login.json()["csrf_token"],
            "Origin": "http://testserver",
        },
    )

    assert authorization.status_code == 200
    redirect_uri = authorization.json()["redirect_uri"]
    parameters = parse_qs(urlparse(redirect_uri).query)
    code = parameters["code"][0]
    assert parameters["state"] == ["state-1"]
    with factory() as session:
        oauth_client = session.get(OAuthClientRecord, client_id)
        grant = session.execute(select(OAuthAuthorizationGrantRecord)).scalar_one()
        assert oauth_client is not None
        assert oauth_client.client_secret_hash is None
        assert type(grant.code_hash) is bytes
        assert grant.code_hash == sha256(code.encode()).digest()
        assert len(grant.code_hash) == 32

    exchange_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": "http://localhost:49152/api/v2/auth/callback",
        "code_verifier": verifier,
    }
    token = client.post("/api/v2/oauth/token", json=exchange_payload)
    replay = client.post("/api/v2/oauth/token", json=exchange_payload)

    assert token.status_code == 200
    assert token.json()["token_type"] == "Bearer"
    access_token = token.json()["access_token"]
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    status = client.get(
        "/api/v2/auth/token-status",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert status.status_code == 200
    assert status.json()["auth_kind"] == "pkce"
    assert status.json()["scopes"] == ["book:read", "ledger:read"]

    with factory() as session:
        stored = session.execute(
            select(CredentialRecord).where(CredentialRecord.auth_kind == "pkce")
        ).scalar_one()
        assert type(stored.token_hash) is bytes
        assert stored.token_hash == sha256(access_token.encode()).digest()
        assert len(stored.token_hash) == 32


def test_device_grant_is_persistent_approvable_and_single_use(pg_engine) -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    raw_api_key = "ta_device_seed"
    factory, get_session = _seed_api_key(pg_engine, raw_api_key)
    app = FastAPI()
    app.include_router(create_auth_router(get_session))
    client = TestClient(app)

    registration = client.post(
        "/api/v2/oauth/register",
        json={
            "client_name": "Headless CLI",
            "redirect_uris": ["http://127.0.0.1:49152/api/v2/auth/callback"],
            "scope": "book:read ledger:read",
            "token_endpoint_auth_method": "none",
        },
    )
    client_id = registration.json()["client_id"]
    authorization = client.post(
        "/api/v2/oauth/device/authorize",
        json={
            "client_id": client_id,
            "scope": "book:read ledger:read",
        },
    )

    assert authorization.status_code == 200
    device = authorization.json()
    assert device["verification_uri"].endswith("/api/v2/auth/device")
    assert "/api/v1" not in authorization.text
    assert device["verification_uri_complete"].endswith(
        f"/api/v2/auth/device?user_code={device['user_code']}"
    )
    with factory() as session:
        grant = session.execute(select(OAuthDeviceGrantRecord)).scalar_one()
        assert type(grant.device_code_hash) is bytes
        assert type(grant.user_code_hash) is bytes
        assert grant.device_code_hash == sha256(device["device_code"].encode()).digest()
        normalized_user_code = device["user_code"].replace("-", "")
        assert grant.user_code_hash == sha256(normalized_user_code.encode()).digest()

    login = client.post(
        "/api/v2/auth/session/api-key",
        json={"api_key": raw_api_key},
    )
    approval = client.post(
        "/api/v2/auth/device",
        json={"user_code": device["user_code"], "action": "approve"},
        headers={
            "X-CSRF-Token": login.json()["csrf_token"],
            "Origin": "http://testserver",
        },
    )
    assert approval.status_code == 200
    assert approval.json()["status"] == "approved"

    exchange_payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device["device_code"],
        "client_id": client_id,
    }
    token = client.post("/api/v2/oauth/token", json=exchange_payload)
    replay = client.post("/api/v2/oauth/token", json=exchange_payload)

    assert token.status_code == 200
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    access_token = token.json()["access_token"]
    status = client.get(
        "/api/v2/auth/token-status",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert status.status_code == 200
    assert status.json()["auth_kind"] == "device"

    revocation = client.post(
        "/api/v2/oauth/revoke",
        json={"token": access_token},
    )
    assert revocation.status_code == 200
    assert revocation.json() == {"revoked": True}
    assert (
        client.get(
            "/api/v2/auth/token-status",
            headers={"Authorization": f"Bearer {access_token}"},
        ).status_code
        == 401
    )


def test_pending_device_poll_returns_oauth_error_and_records_poll(pg_engine) -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    _, get_session = _seed_api_key(pg_engine, "ta_pending_seed")
    app = FastAPI()
    app.include_router(create_auth_router(get_session))
    client = TestClient(app)
    registration = client.post(
        "/api/v2/oauth/register",
        json={
            "client_name": "Polling CLI",
            "redirect_uris": ["http://localhost:49152/api/v2/auth/callback"],
            "scope": "book:read",
        },
    )
    authorization = client.post(
        "/api/v2/oauth/device/authorize",
        json={"client_id": registration.json()["client_id"], "scope": "book:read"},
    ).json()
    payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": authorization["device_code"],
        "client_id": registration.json()["client_id"],
    }

    pending = client.post("/api/v2/oauth/token", json=payload)
    too_fast = client.post("/api/v2/oauth/token", json=payload)

    assert pending.status_code == 400
    assert pending.json()["error"] == "authorization_pending"
    assert too_fast.status_code == 400
    assert too_fast.json()["error"] == "slow_down"
    assert too_fast.json()["interval"] == authorization["interval"] + 5
