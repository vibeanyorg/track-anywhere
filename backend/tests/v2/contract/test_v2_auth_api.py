from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Barrier, Lock
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
    PasswordAccountRecord,
    UserRecord,
)


API_RESOURCE = "http://testserver/api/v2"


def _unused_session() -> Iterator[Session]:
    raise AssertionError("metadata routes must not open a database session")
    yield


def _seed_api_key(
    pg_engine,
    raw_api_key: str,
    *,
    scopes: list[str] | None = None,
):
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
                scopes=scopes or ["book:read", "ledger:read"],
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
    assert login.headers["cache-control"] == "no-store"
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


def test_first_password_signup_claims_existing_owner_and_issues_session(
    pg_engine,
) -> None:
    from track_anywhere.api.v2.auth import create_auth_router
    from track_anywhere.auth.security import verify_password_hash

    factory, get_session = _seed_api_key(
        pg_engine,
        "ta_existing_owner",
        scopes=["book:read", "book:write", "ledger:read", "ledger:write"],
    )
    app = FastAPI()
    app.include_router(create_auth_router(get_session))
    client = TestClient(app)

    signup = client.post(
        "/api/v2/auth/signup",
        headers={"Origin": "http://testserver"},
        json={
            "display_name": "Alice Owner",
            "email": "  Alice@Example.Test ",
            "password": "correct horse battery staple",
            "setup_key": "ta_existing_owner",
        },
    )

    assert signup.status_code == 201
    assert signup.headers["cache-control"] == "no-store"
    assert signup.json()["authenticated"] is True
    assert signup.json()["identity"] == {
        "user_id": "human:test",
        "display_name": "Alice Owner",
        "subject_type": "human",
        "auth_kind": "browser_session",
        "book_id": None,
        "scopes": ["book:read", "book:write", "ledger:read", "ledger:write"],
    }
    assert signup.cookies["ta_session"]
    assert signup.cookies["ta_csrf"]
    assert "correct horse battery staple" not in signup.text
    assert "ta_existing_owner" not in signup.text

    with factory() as session:
        users = list(session.scalars(select(UserRecord)))
        password_account = session.scalar(select(PasswordAccountRecord))
        assert [user.user_id for user in users] == ["human:test"]
        assert users[0].current_display_name == "Alice Owner"
        assert password_account is not None
        assert password_account.user_id == "human:test"
        assert password_account.normalized_email == "alice@example.test"
        assert password_account.password_hash != "correct horse battery staple"
        assert verify_password_hash(
            "correct horse battery staple",
            password_account.password_hash,
        )


def test_password_signup_is_same_origin_and_first_account_only(pg_engine) -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    factory, get_session = _seed_api_key(
        pg_engine,
        "ta_private_setup",
        scopes=["book:read", "book:write", "ledger:read", "ledger:write"],
    )

    app = FastAPI()
    app.include_router(create_auth_router(get_session))
    client = TestClient(app)
    payload = {
        "display_name": "First Owner",
        "email": "owner@example.test",
        "password": "a long private password",
        "setup_key": "ta_private_setup",
    }

    cross_origin = client.post(
        "/api/v2/auth/signup",
        headers={"Origin": "https://attacker.example"},
        json=payload,
    )
    assert cross_origin.status_code == 403

    invalid_key = client.post(
        "/api/v2/auth/signup",
        headers={"Origin": "http://testserver"},
        json={**payload, "setup_key": "ta_wrong_setup_key"},
    )
    assert invalid_key.status_code == 401
    assert invalid_key.json() == {"detail": "Setup key is invalid or expired"}

    with factory() as session:
        assert session.scalar(select(PasswordAccountRecord)) is None

    first = client.post(
        "/api/v2/auth/signup",
        headers={"Origin": "http://testserver"},
        json=payload,
    )
    assert first.status_code == 201

    second = TestClient(app).post(
        "/api/v2/auth/signup",
        headers={"Origin": "http://testserver"},
        json={**payload, "email": "other@example.test"},
    )
    assert second.status_code == 409
    assert second.json() == {"detail": "Account setup is already complete"}

    with factory() as session:
        assert len(list(session.scalars(select(PasswordAccountRecord)))) == 1
        assert len(list(session.scalars(select(UserRecord)))) == 1


def test_concurrent_first_password_signups_create_exactly_one_account(
    pg_engine,
) -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    factory, _ = _seed_api_key(
        pg_engine,
        "ta_concurrent_setup",
        scopes=["book:read", "book:write", "ledger:read", "ledger:write"],
    )
    sessions_ready = Barrier(2)
    session_ids: set[int] = set()
    session_ids_lock = Lock()

    def get_concurrent_session() -> Iterator[Session]:
        with factory() as session, session.begin():
            with session_ids_lock:
                session_ids.add(id(session))
            sessions_ready.wait(timeout=10)
            yield session

    first_app = FastAPI()
    first_app.include_router(create_auth_router(get_concurrent_session))
    second_app = FastAPI()
    second_app.include_router(create_auth_router(get_concurrent_session))
    payload = {
        "display_name": "Concurrent Owner",
        "password": "a sufficiently long password",
        "setup_key": "ta_concurrent_setup",
    }

    def submit(client: TestClient, email: str):
        return client.post(
            "/api/v2/auth/signup",
            headers={"Origin": "http://testserver"},
            json={**payload, "email": email},
        )

    with TestClient(first_app) as first_client, TestClient(second_app) as second_client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [
                future.result(timeout=20)
                for future in (
                    pool.submit(submit, first_client, "first@example.test"),
                    pool.submit(submit, second_client, "second@example.test"),
                )
            ]

    assert len(session_ids) == 2
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert [
        response.json()
        for response in responses
        if response.status_code == 409
    ] == [{"detail": "Account setup is already complete"}]
    with factory() as session:
        password_accounts = list(session.scalars(select(PasswordAccountRecord)))
        assert len(password_accounts) == 1
        assert password_accounts[0].normalized_email in {
            "first@example.test",
            "second@example.test",
        }


def test_password_signup_requires_an_unscoped_owner_setup_key(pg_engine) -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    factory, get_session = _seed_api_key(
        pg_engine,
        "ta_read_only_setup",
        scopes=["book:read", "ledger:read"],
    )
    app = FastAPI()
    app.include_router(create_auth_router(get_session))

    response = TestClient(app).post(
        "/api/v2/auth/signup",
        headers={"Origin": "http://testserver"},
        json={
            "display_name": "Owner",
            "email": "owner@example.test",
            "password": "a sufficiently long password",
            "setup_key": "ta_read_only_setup",
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Setup key is invalid or expired"}
    with factory() as session:
        assert session.scalar(select(PasswordAccountRecord)) is None


def test_password_auth_rejects_malformed_email_and_weak_password(pg_engine) -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    factory, get_session = _seed_api_key(
        pg_engine,
        "ta_validation_setup",
        scopes=["book:read", "book:write", "ledger:read", "ledger:write"],
    )

    app = FastAPI()
    app.include_router(create_auth_router(get_session))
    client = TestClient(app)

    invalid_email = client.post(
        "/api/v2/auth/signup",
        headers={"Origin": "http://testserver"},
        json={
            "display_name": "Owner",
            "email": "not-an-email",
            "password": "a sufficiently long password",
            "setup_key": "ta_validation_setup",
        },
    )
    weak_password = client.post(
        "/api/v2/auth/signup",
        headers={"Origin": "http://testserver"},
        json={
            "display_name": "Owner",
            "email": "owner@example.test",
            "password": "too-short",
            "setup_key": "ta_validation_setup",
        },
    )

    assert invalid_email.status_code == 422
    assert weak_password.status_code == 422

    with factory() as session:
        assert session.scalar(select(PasswordAccountRecord)) is None


def test_password_login_is_generic_and_persists_before_follow_up(pg_engine) -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    factory, get_session = _seed_api_key(
        pg_engine,
        "ta_password_setup",
        scopes=["book:read", "book:write", "ledger:read", "ledger:write"],
    )

    app = FastAPI()
    app.include_router(create_auth_router(get_session))
    client = TestClient(app)
    signup = client.post(
        "/api/v2/auth/signup",
        headers={"Origin": "http://testserver"},
        json={
            "display_name": "Password Owner",
            "email": "owner@example.test",
            "password": "correct horse battery staple",
            "setup_key": "ta_password_setup",
        },
    )
    assert signup.status_code == 201
    logout = client.post(
        "/api/v2/auth/logout",
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": signup.json()["csrf_token"],
        },
    )
    assert logout.status_code == 200

    wrong_password = client.post(
        "/api/v2/auth/session/password",
        headers={"Origin": "http://testserver"},
        json={
            "email": "owner@example.test",
            "password": "this password is incorrect",
        },
    )
    unknown_email = client.post(
        "/api/v2/auth/session/password",
        headers={"Origin": "http://testserver"},
        json={
            "email": "unknown@example.test",
            "password": "this password is incorrect",
        },
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json() == {
        "detail": "Email or password is invalid"
    }

    login = client.post(
        "/api/v2/auth/session/password",
        headers={"Origin": "http://testserver"},
        json={
            "email": " OWNER@example.test ",
            "password": "correct horse battery staple",
        },
    )
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    assert client.get("/api/v2/auth/session").json()["authenticated"] is True


def test_auth_dependencies_finalize_before_the_response_body() -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    events: list[str] = []

    def get_session() -> Iterator[object]:
        try:
            yield object()
        finally:
            events.append("dependency-finalized")

    app = FastAPI()
    app.include_router(create_auth_router(get_session))

    class ResponseProbe:
        def __init__(self, application: object) -> None:
            self.application = application

        async def __call__(self, scope, receive, send) -> None:
            async def tracked_send(message) -> None:
                if (
                    message["type"] == "http.response.body"
                    and not message.get("more_body", False)
                ):
                    events.append("response-body")
                await send(message)

            await self.application(scope, receive, tracked_send)

    response = TestClient(ResponseProbe(app)).get("/api/v2/auth/session")

    assert response.status_code == 200
    assert events == ["dependency-finalized", "response-body"]


def test_password_login_returns_retry_after_before_password_work() -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    class RejectingThrottle:
        def check(self, client: str, subject: str) -> int | None:
            assert client == "testclient"
            assert subject == "login:owner@example.test"
            return 17

        def reset(self, subject: str) -> None:
            raise AssertionError(f"blocked login unexpectedly reset {subject}")

    def get_session() -> Iterator[object]:
        yield object()

    app = FastAPI()
    app.include_router(
        create_auth_router(
            get_session,
            password_throttle=RejectingThrottle(),
        )
    )
    response = TestClient(app).post(
        "/api/v2/auth/session/password",
        headers={"Origin": "http://testserver"},
        json={
            "email": " OWNER@example.test ",
            "password": "a sufficiently long password",
        },
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "17"
    assert response.json() == {"detail": "Too many authentication attempts"}


def test_default_password_login_throttle_is_wired_to_the_route(
    pg_engine,
    monkeypatch,
) -> None:
    import track_anywhere.api.v2.auth as auth_api
    from track_anywhere.auth.throttle import InMemoryAuthThrottle

    monkeypatch.setattr(
        auth_api,
        "_PASSWORD_AUTH_THROTTLE",
        InMemoryAuthThrottle(client_capacity=100),
    )

    _, get_session = _seed_api_key(pg_engine, "ta_throttle_wiring_seed")
    app = FastAPI()
    app.include_router(auth_api.create_auth_router(get_session))
    client = TestClient(app)
    payload = {
        "email": "route-throttle@example.test",
        "password": "a deliberately incorrect password",
    }

    responses = [
        client.post(
            "/api/v2/auth/session/password",
            headers={"Origin": "http://testserver"},
            json=payload,
        )
        for _ in range(9)
    ]

    assert [response.status_code for response in responses[:8]] == [401] * 8
    blocked = responses[8]
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
    assert blocked.json() == {"detail": "Too many authentication attempts"}


def test_rotating_login_subjects_do_not_block_an_unrelated_client(pg_engine) -> None:
    from track_anywhere.api.v2.auth import create_auth_router
    from track_anywhere.auth.throttle import InMemoryAuthThrottle

    _, get_session = _seed_api_key(pg_engine, "ta_client_throttle_seed")
    app = FastAPI()
    app.include_router(
        create_auth_router(
            get_session,
            password_throttle=InMemoryAuthThrottle(
                client_capacity=2,
                client_refill_per_second=0.1,
                subject_capacity=100,
                subject_refill_per_second=1,
            ),
        )
    )
    attacker = TestClient(app, client=("198.51.100.10", 50000))
    unrelated = TestClient(app, client=("203.0.113.20", 50000))

    for index in range(2):
        response = attacker.post(
            "/api/v2/auth/session/password",
            headers={"Origin": "http://testserver"},
            json={
                "email": f"attacker-{index}@example.test",
                "password": "a deliberately incorrect password",
            },
        )
        assert response.status_code == 401

    blocked = attacker.post(
        "/api/v2/auth/session/password",
        headers={"Origin": "http://testserver"},
        json={
            "email": "attacker-2@example.test",
            "password": "a deliberately incorrect password",
        },
    )
    still_available = unrelated.post(
        "/api/v2/auth/session/password",
        headers={"Origin": "http://testserver"},
        json={
            "email": "owner@example.test",
            "password": "a deliberately incorrect password",
        },
    )

    assert blocked.status_code == 429
    assert still_available.status_code == 401


def test_browser_auth_uses_configured_public_origin_behind_proxy(pg_engine) -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    raw_api_key = "ta_public_origin_seed"
    _, get_session = _seed_api_key(pg_engine, raw_api_key)
    app = FastAPI()
    app.include_router(
        create_auth_router(
            get_session,
            public_base_url="https://ledger.example.com",
        )
    )
    client = TestClient(app)
    login = client.post(
        "/api/v2/auth/session/api-key",
        json={"api_key": raw_api_key},
    )
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    internal_origin = client.post(
        "/api/v2/auth/logout",
        headers={**headers, "Origin": "http://testserver"},
    )
    public_origin = client.post(
        "/api/v2/auth/logout",
        headers={**headers, "Origin": "https://ledger.example.com"},
    )

    assert internal_origin.status_code == 403
    assert public_origin.status_code == 200


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
    assert registration.status_code == 201
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
            "resource": API_RESOURCE,
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
        "resource": API_RESOURCE,
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
    assert registration.status_code == 201
    client_id = registration.json()["client_id"]
    authorization = client.post(
        "/api/v2/oauth/device/authorize",
        json={
            "client_id": client_id,
            "scope": "book:read ledger:read",
            "resource": API_RESOURCE,
        },
    )

    assert authorization.status_code == 200
    device = authorization.json()
    assert device["verification_uri"].endswith("/auth/device")
    assert "/api/v1" not in authorization.text
    assert device["verification_uri_complete"].endswith(
        f"/auth/device?user_code={device['user_code']}"
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
        "resource": API_RESOURCE,
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
        json={
            "client_id": registration.json()["client_id"],
            "scope": "book:read",
            "resource": API_RESOURCE,
        },
    ).json()
    payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": authorization["device_code"],
        "client_id": registration.json()["client_id"],
        "resource": API_RESOURCE,
    }

    pending = client.post("/api/v2/oauth/token", json=payload)
    too_fast = client.post("/api/v2/oauth/token", json=payload)

    assert pending.status_code == 400
    assert pending.json()["error"] == "authorization_pending"
    assert too_fast.status_code == 400
    assert too_fast.json()["error"] == "slow_down"
    assert too_fast.json()["interval"] == authorization["interval"] + 5


def test_standard_form_pkce_refresh_rotation_and_api_key_boundary(pg_engine) -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    raw_api_key = "ta_standard_oauth_seed"
    _, get_session = _seed_api_key(pg_engine, raw_api_key)
    app = FastAPI()
    app.include_router(
        create_auth_router(
            get_session,
            public_base_url="http://testserver",
        )
    )
    client = TestClient(app)

    registration = client.post(
        "/api/v2/oauth/register",
        json={
            "client_name": "ChatGPT-style public client",
            "redirect_uris": ["http://127.0.0.1/callback"],
            "scope": "ledger:read",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "client_uri": "https://example.test/ignored-extension",
        },
    )
    assert registration.status_code == 201
    client_id = registration.json()["client_id"]

    login = client.post(
        "/api/v2/auth/session/api-key",
        json={"api_key": raw_api_key},
    )
    assert login.status_code == 200
    verifier = "s" * 64
    challenge = (
        base64.urlsafe_b64encode(sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    authorize_payload = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": "http://127.0.0.1:49152/callback",
        "scope": "ledger:read",
        "state": "standard-state",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": API_RESOURCE,
    }
    begin = client.get(
        "/api/v2/oauth/authorize",
        params=authorize_payload,
        follow_redirects=False,
    )
    assert begin.status_code == 302
    assert begin.headers["location"].startswith("/auth/callback?")

    approval = client.post(
        "/api/v2/oauth/authorize",
        json={**authorize_payload, "action": "approve"},
        headers={
            "X-CSRF-Token": login.json()["csrf_token"],
            "Origin": "http://testserver",
        },
    )
    assert approval.status_code == 200
    code = parse_qs(urlparse(approval.json()["redirect_uri"]).query)["code"][0]

    token = client.post(
        "/api/v2/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": authorize_payload["redirect_uri"],
            "code_verifier": verifier,
            "resource": API_RESOURCE,
        },
    )
    assert token.status_code == 200
    first = token.json()
    assert first["resource"] == API_RESOURCE
    assert first["refresh_token"].startswith("rt_")

    refreshed = client.post(
        "/api/v2/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": client_id,
            "resource": API_RESOURCE,
        },
    )
    assert refreshed.status_code == 200
    second = refreshed.json()
    assert second["access_token"] != first["access_token"]
    assert second["refresh_token"] != first["refresh_token"]

    replay = client.post(
        "/api/v2/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": client_id,
            "resource": API_RESOURCE,
        },
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    assert (
        client.get(
            "/api/v2/auth/token-status",
            headers={"Authorization": f"Bearer {second['access_token']}"},
        ).status_code
        == 401
    )

    bearer_api_key = client.get(
        "/api/v2/auth/token-status",
        headers={"Authorization": f"Bearer {raw_api_key}"},
    )
    header_api_key = client.get(
        "/api/v2/auth/token-status",
        headers={"X-API-Key": raw_api_key},
    )
    assert bearer_api_key.status_code == 401
    assert "resource_metadata=" in bearer_api_key.headers["WWW-Authenticate"]
    assert header_api_key.status_code == 200
    assert header_api_key.json()["auth_kind"] == "api_key"


def test_mcp_audience_token_is_rejected_by_rest_and_accepted_by_mcp(pg_engine) -> None:
    from track_anywhere.api.dependencies import build_engine_dependencies
    from track_anywhere.api.v2.auth import create_auth_router
    from track_anywhere.mcp.server import create_mcp_runtime

    raw_api_key = "ta_mcp_audience_seed"
    _, get_session = _seed_api_key(pg_engine, raw_api_key)
    auth_app = FastAPI()
    auth_app.include_router(
        create_auth_router(get_session, public_base_url="http://testserver")
    )
    auth_client = TestClient(auth_app)
    registration = auth_client.post(
        "/api/v2/oauth/register",
        json={
            "client_name": "MCP contract client",
            "redirect_uris": ["https://chatgpt.com/connector/oauth/test-callback"],
            "scope": "ledger:read",
        },
    )
    client_id = registration.json()["client_id"]
    login = auth_client.post(
        "/api/v2/auth/session/api-key",
        json={"api_key": raw_api_key},
    )
    verifier = "m" * 64
    challenge = (
        base64.urlsafe_b64encode(sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    redirect_uri = "https://chatgpt.com/connector/oauth/test-callback"
    resource = "http://testserver/mcp"
    approval = auth_client.post(
        "/api/v2/oauth/authorize",
        json={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "ledger:read",
            "state": "mcp-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": resource,
        },
        headers={
            "X-CSRF-Token": login.json()["csrf_token"],
            "Origin": "http://testserver",
        },
    )
    code = parse_qs(urlparse(approval.json()["redirect_uri"]).query)["code"][0]
    token = auth_client.post(
        "/api/v2/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "resource": resource,
        },
    ).json()["access_token"]

    rest_status = auth_client.get(
        "/api/v2/auth/token-status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rest_status.status_code == 401

    runtime = create_mcp_runtime(
        build_engine_dependencies(
            pg_engine,
            expected_runtime_role=pg_engine.url.username,
        ),
        "http://testserver",
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25",
    }
    with TestClient(runtime.application) as mcp_client:
        initialize = mcp_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "contract", "version": "1"},
                },
            },
            headers=headers,
        )
        tools = mcp_client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=headers,
        )
        books = mcp_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "ledger_list_books", "arguments": {}},
            },
            headers=headers,
        )

    assert initialize.status_code == 200
    assert tools.status_code == 200
    assert len(tools.json()["result"]["tools"]) == 8
    assert books.status_code == 200
    assert books.json()["result"]["structuredContent"] == {"items": []}


def test_same_credential_and_browser_session_are_safe_under_concurrency(
    pg_engine,
) -> None:
    from track_anywhere.api.v2.auth import create_auth_router

    raw_api_key = "ta_concurrent_auth_seed"
    _, get_session = _seed_api_key(pg_engine, raw_api_key)
    app = FastAPI()
    app.include_router(create_auth_router(get_session))
    login_client = TestClient(app)
    login = login_client.post(
        "/api/v2/auth/session/api-key",
        json={"api_key": raw_api_key},
    )
    raw_session = login.cookies["ta_session"]

    def request_api_key_status(_: int) -> int:
        with TestClient(app) as client:
            return client.get(
                "/api/v2/auth/token-status",
                headers={"X-API-Key": raw_api_key},
            ).status_code

    def request_browser_session(_: int) -> int:
        with TestClient(app) as client:
            client.cookies.set("ta_session", raw_session)
            return client.get("/api/v2/auth/session").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        credential_statuses = list(pool.map(request_api_key_status, range(32)))
        browser_statuses = list(pool.map(request_browser_session, range(32)))

    assert credential_statuses == [200] * 32
    assert browser_statuses == [200] * 32
