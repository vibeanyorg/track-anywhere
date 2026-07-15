from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class ContractResponse:
    status_code: int
    data: Any
    headers: dict[str, str]


class BackendApiClient(Protocol):
    name: str
    api_key: str
    database_url: str

    def get(
        self, path: str, *, headers: dict[str, str] | None = None
    ) -> ContractResponse: ...

    def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ContractResponse: ...

    def patch(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ContractResponse: ...

    def openapi_paths(self) -> dict[str, list[str]]: ...

    def close(self) -> None: ...


def parse_json_response(
    status_code: int,
    headers: dict[str, str],
    content: bytes,
) -> ContractResponse:
    if not content:
        data: Any = None
    else:
        data = json.loads(content.decode("utf-8"))
    return ContractResponse(status_code=status_code, data=data, headers=headers)


class FastApiClient:
    name = "fastapi-v2"

    def __init__(self, database_url: str, *, expected_runtime_role: str) -> None:
        # The contract fixture installs this exact PG17 URL before this method
        # imports the application composition root. There is intentionally no
        # SQLite fallback in this harness.
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine

        from track_anywhere.api import create_app

        self.database_url = database_url
        self.api_key = f"ta_contract_{uuid4().hex}"
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._seed_actor()
        self._application = create_app(
            engine=self._engine,
            expected_runtime_role=expected_runtime_role,
            cookie_secure=False,
            public_base_url="https://ledger.example.com",
        )
        self._client = TestClient(self._application)

    def _seed_actor(self) -> None:
        from sqlalchemy.orm import Session

        from track_anywhere.infrastructure.db.models.auth import (
            CredentialRecord,
            UserRecord,
        )

        now = datetime.now(UTC)
        with Session(self._engine) as session, session.begin():
            session.add(
                UserRecord(
                    user_id="human:contract-v2",
                    subject_type="human",
                    current_display_name="V2 contract",
                    status="active",
                )
            )
            session.flush()
            session.add(
                CredentialRecord(
                    credential_id=uuid4(),
                    token_hash=sha256(self.api_key.encode()).digest(),
                    jti=uuid4(),
                    actor_subject_id="human:contract-v2",
                    actor_type="human",
                    auth_kind="api_key",
                    book_id=None,
                    scopes=[
                        "book:read",
                        "book:write",
                        "ledger:read",
                        "ledger:write",
                    ],
                    issued_at=now - timedelta(minutes=1),
                    expires_at=now + timedelta(hours=1),
                    revoked_at=None,
                    last_used_at=None,
                )
            )

    def get(
        self, path: str, *, headers: dict[str, str] | None = None
    ) -> ContractResponse:
        response = self._client.get(path, headers=headers)
        return parse_json_response(
            response.status_code,
            dict(response.headers),
            response.content,
        )

    def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ContractResponse:
        response = self._client.post(path, json=json_body, headers=headers)
        return parse_json_response(
            response.status_code,
            dict(response.headers),
            response.content,
        )

    def patch(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ContractResponse:
        response = self._client.patch(path, json=json_body, headers=headers)
        return parse_json_response(
            response.status_code,
            dict(response.headers),
            response.content,
        )

    def openapi_paths(self) -> dict[str, list[str]]:
        return {
            path: sorted(
                method
                for method in details
                if method in {"get", "post", "put", "patch", "delete"}
            )
            for path, details in sorted(self._application.openapi()["paths"].items())
        }

    def close(self) -> None:
        self._client.close()
        self._engine.dispose()
