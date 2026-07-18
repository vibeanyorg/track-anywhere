from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.types import Message, Scope

from track_anywhere.server import (
    MAX_REQUEST_BODY_BYTES_ENV,
    ProtocolApplication,
    create_server,
)
from track_anywhere.mcp.server import create_mcp_runtime


def _unused_session() -> Iterator[Session]:
    raise AssertionError("body-limit probes must not open a database session")
    yield


def _form_probe_router(
    _get_session: object,
    *,
    cookie_secure: bool = False,
) -> APIRouter:
    del cookie_secure
    router = APIRouter(prefix="/api/v2")

    @router.post("/form-probe")
    async def form_probe(request: Request) -> dict[str, object]:
        form = await request.form()
        return {"items": form.multi_items()}

    return router


def _client(*, max_request_body_bytes: int = 1_048_576) -> TestClient:
    return TestClient(
        create_server(
            get_session=_unused_session,
            auth_router_factory=_form_probe_router,
            public_base_url="http://testserver",
            max_request_body_bytes=max_request_body_bytes,
        )
    )


def _protocol_probe(*, max_request_body_bytes: int) -> ProtocolApplication:
    rest = FastAPI()

    @rest.api_route("/api/probe", methods=["GET", "POST"])
    async def probe(request: Request) -> dict[str, int]:
        body = await request.body() if request.method == "POST" else b""
        return {"size": len(body)}

    return ProtocolApplication(
        rest_application=rest,
        discovery_application=FastAPI(),
        mcp_runtime=None,
        max_request_body_bytes=max_request_body_bytes,
    )


def _invoke(
    application: ProtocolApplication,
    *,
    method: str = "POST",
    headers: list[tuple[bytes, bytes]] | None = None,
    request_messages: list[Message] | None = None,
    fail_on_receive: bool = False,
    path: str = "/api/probe",
    root_path: str = "",
) -> tuple[int, dict[str, Any], int, dict[str, str]]:
    response_messages: list[Message] = []
    queued_messages = list(
        request_messages
        or [{"type": "http.request", "body": b"", "more_body": False}]
    )
    receive_calls = 0
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "root_path": root_path,
    }

    async def receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        if fail_on_receive:
            raise AssertionError("request body must not be read")
        if queued_messages:
            return queued_messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        response_messages.append(message)

    asyncio.run(application(scope, receive, send))
    start = next(
        message
        for message in response_messages
        if message["type"] == "http.response.start"
    )
    body = b"".join(
        message.get("body", b"")
        for message in response_messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        name.decode("latin-1").lower(): value.decode("latin-1")
        for name, value in start.get("headers", [])
    }
    return int(start["status"]), json.loads(body), receive_calls, response_headers


def test_declared_oversized_request_is_rejected_without_reading_body() -> None:
    status, payload, receive_calls, response_headers = _invoke(
        _protocol_probe(max_request_body_bytes=32),
        headers=[(b"content-length", b"33")],
        fail_on_receive=True,
    )

    assert status == 413
    assert payload == {"detail": "Request body is too large"}
    assert receive_calls == 0
    assert response_headers["cache-control"] == "no-store"
    assert response_headers["pragma"] == "no-cache"


def test_streamed_body_cannot_bypass_limit_with_false_content_length() -> None:
    status, payload, receive_calls, _response_headers = _invoke(
        _protocol_probe(max_request_body_bytes=32),
        headers=[(b"content-length", b"1")],
        request_messages=[
            {"type": "http.request", "body": b"a" * 16, "more_body": True},
            {"type": "http.request", "body": b"b" * 17, "more_body": False},
        ],
    )

    assert status == 413
    assert payload == {"detail": "Request body is too large"}
    assert receive_calls == 2


def test_request_at_exact_limit_is_replayed_to_route() -> None:
    status, payload, receive_calls, _response_headers = _invoke(
        _protocol_probe(max_request_body_bytes=32),
        request_messages=[
            {"type": "http.request", "body": b"a" * 16, "more_body": True},
            {"type": "http.request", "body": b"b" * 16, "more_body": False},
        ],
    )

    assert status == 200
    assert payload == {"size": 32}
    assert receive_calls == 2


def test_many_tiny_frames_are_coalesced_and_replayed_at_exact_limit() -> None:
    fragments: list[Message] = [
        {"type": "http.request", "body": b"", "more_body": True}
        for _ in range(2_048)
    ]
    fragments.append(
        {"type": "http.request", "body": b"x" * 32, "more_body": False}
    )

    status, payload, receive_calls, _response_headers = _invoke(
        _protocol_probe(max_request_body_bytes=32),
        request_messages=fragments,
    )

    assert status == 200
    assert payload == {"size": 32}
    assert receive_calls == 2_049


def test_get_request_is_not_pre_read() -> None:
    status, payload, receive_calls, _response_headers = _invoke(
        _protocol_probe(max_request_body_bytes=32),
        method="GET",
        headers=[(b"content-length", b"999")],
        fail_on_receive=True,
    )

    assert status == 200
    assert payload == {"size": 0}
    assert receive_calls == 0


def test_root_path_cannot_bypass_the_limit() -> None:
    status, payload, receive_calls, _response_headers = _invoke(
        _protocol_probe(max_request_body_bytes=32),
        headers=[(b"content-length", b"33")],
        fail_on_receive=True,
        path="/ledger/api/probe",
        root_path="/ledger",
    )

    assert status == 413
    assert payload == {"detail": "Request body is too large"}
    assert receive_calls == 0


def test_legitimate_form_request_remains_available() -> None:
    response = _client(max_request_body_bytes=64).post(
        "/api/v2/form-probe",
        data={"grant_type": "authorization_code"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [["grant_type", "authorization_code"]],
    }


def test_urlencoded_form_enforces_starlette_default_field_limit() -> None:
    body = "&".join(f"field_{index}=value" for index in range(1_001))

    response = _client().post(
        "/api/v2/form-probe",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 400
    assert "Too many fields" in response.text


def test_semicolon_is_not_treated_as_a_urlencoded_field_separator() -> None:
    response = _client().post(
        "/api/v2/form-probe",
        content="first=one;second=two",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [["first", "one;second=two"]],
    }


def test_mcp_request_uses_the_same_body_limit() -> None:
    client = _client(max_request_body_bytes=32)

    oversized = client.post("/mcp", content=b"x" * 33)
    ordinary = client.post("/mcp", content=b"{}")

    assert oversized.status_code == 413
    assert oversized.json() == {"detail": "Request body is too large"}
    assert ordinary.status_code == 503


def test_ordinary_request_reaches_real_mcp_oauth_boundary() -> None:
    runtime = create_mcp_runtime(
        SimpleNamespace(session_factory=lambda: None),
        "http://testserver",
    )
    application = ProtocolApplication(
        rest_application=FastAPI(),
        discovery_application=FastAPI(),
        mcp_runtime=runtime,
        max_request_body_bytes=1_048_576,
    )

    with TestClient(application) as client:
        response = client.post(
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
            headers={
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-11-25",
            },
        )

    assert response.status_code == 401


@pytest.mark.parametrize("value", ["", "0", "1023", "16777217", "not-a-number"])
def test_request_body_limit_environment_is_fail_closed(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MAX_REQUEST_BODY_BYTES_ENV, value)
    monkeypatch.delenv("TRACK_ANYWHERE_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="MAX_REQUEST_BODY_BYTES"):
        create_server(public_base_url="http://testserver")
