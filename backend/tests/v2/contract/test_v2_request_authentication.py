from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import cast
from uuid import UUID

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from track_anywhere.api.v2.schemas import RequestActor, create_actor_dependency
from track_anywhere.auth.sessions import PersistentSessionService


BOOK_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_TOKEN = "sess_reader"
CSRF_TOKEN = "csrf_reader"


class _SessionSentinel:
    pass


SESSION = _SessionSentinel()


def _get_session() -> Iterator[Session]:
    yield cast(Session, SESSION)


def _client(public_base_url: str | None = None) -> TestClient:
    authenticate = create_actor_dependency(_get_session)
    app = FastAPI()
    app.state.public_base_url = public_base_url

    @app.get("/read")
    def read(actor: RequestActor = Depends(authenticate)) -> dict[str, str]:
        return {"actor": actor.command_actor.subject_id}

    @app.post("/write")
    def write(actor: RequestActor = Depends(authenticate)) -> dict[str, str]:
        return {"actor": actor.command_actor.subject_id}

    return TestClient(app)


@pytest.fixture
def browser_session(monkeypatch: pytest.MonkeyPatch) -> None:
    active = SimpleNamespace(
        user=SimpleNamespace(user_id="human:reader"),
        credential=SimpleNamespace(
            book_id=BOOK_ID,
            scopes=("ledger:read", "ledger:write"),
        ),
    )

    def current(
        service: PersistentSessionService,
        raw_session: str | None,
        *,
        lock: bool = False,
    ) -> SimpleNamespace | None:
        assert service._session is SESSION
        return active if raw_session == SESSION_TOKEN else None

    def verify_csrf(
        _service: PersistentSessionService,
        _active: SimpleNamespace,
        csrf_token: str | None,
    ) -> bool:
        return csrf_token == CSRF_TOKEN

    monkeypatch.setattr(PersistentSessionService, "current", current)
    monkeypatch.setattr(PersistentSessionService, "verify_csrf", verify_csrf)


def test_cookie_backed_safe_read_does_not_require_csrf_or_origin(
    browser_session: None,
) -> None:
    client = _client()
    client.cookies.set("ta_session", SESSION_TOKEN)

    response = client.get("/read")

    assert response.status_code == 200
    assert response.json() == {"actor": "human:reader"}


def test_cookie_backed_mutation_still_requires_csrf_and_same_origin(
    browser_session: None,
) -> None:
    client = _client()
    client.cookies.set("ta_session", SESSION_TOKEN)

    missing_csrf = client.post("/write")
    wrong_origin = client.post(
        "/write",
        headers={"X-CSRF-Token": CSRF_TOKEN, "Origin": "https://evil.example"},
    )
    conflicting_headers = client.post(
        "/write",
        headers={
            "X-CSRF-Token": CSRF_TOKEN,
            "Origin": "https://evil.example",
            "Referer": "http://testserver/write",
        },
    )
    referer_fallback = client.post(
        "/write",
        headers={
            "X-CSRF-Token": CSRF_TOKEN,
            "Referer": "http://testserver/write",
        },
    )
    allowed = client.post(
        "/write",
        headers={"X-CSRF-Token": CSRF_TOKEN, "Origin": "http://testserver"},
    )

    assert missing_csrf.status_code == 403
    assert wrong_origin.status_code == 403
    assert conflicting_headers.status_code == 403
    assert referer_fallback.status_code == 200
    assert allowed.status_code == 200
    assert allowed.json() == {"actor": "human:reader"}


def test_cookie_mutation_uses_configured_public_origin_behind_proxy(
    browser_session: None,
) -> None:
    client = _client("https://ledger.example.com")
    client.cookies.set("ta_session", SESSION_TOKEN)

    internal_origin = client.post(
        "/write",
        headers={"X-CSRF-Token": CSRF_TOKEN, "Origin": "http://testserver"},
    )
    public_origin = client.post(
        "/write",
        headers={
            "X-CSRF-Token": CSRF_TOKEN,
            "Origin": "https://ledger.example.com",
        },
    )

    assert internal_origin.status_code == 403
    assert public_origin.status_code == 200
