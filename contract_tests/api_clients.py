from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol


os.environ.setdefault("TRACK_ANYWHERE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend_django.config.settings")


@dataclass(frozen=True)
class ContractResponse:
    status_code: int
    data: Any
    headers: dict[str, str]


class BackendApiClient(Protocol):
    name: str

    def get(self, path: str, *, headers: dict[str, str] | None = None) -> ContractResponse:
        ...

    def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ContractResponse:
        ...

    def patch(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ContractResponse:
        ...

    def openapi_paths(self) -> dict[str, list[str]]:
        ...


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def parse_json_response(status_code: int, headers: dict[str, str], content: bytes) -> ContractResponse:
    if not content:
        data: Any = None
    else:
        data = json.loads(content.decode("utf-8"))
    return ContractResponse(status_code=status_code, data=data, headers=headers)


class FastApiClient:
    name = "fastapi"

    def __init__(self) -> None:
        from fastapi.testclient import TestClient
        from track_anywhere.api import app

        self._client = TestClient(app)

    def get(self, path: str, *, headers: dict[str, str] | None = None) -> ContractResponse:
        response = self._client.get(path, headers=headers)
        return ContractResponse(response.status_code, response.json(), dict(response.headers))

    def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ContractResponse:
        response = self._client.post(path, json=json_body, headers=headers)
        return ContractResponse(response.status_code, response.json(), dict(response.headers))

    def patch(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ContractResponse:
        response = self._client.patch(path, json=json_body, headers=headers)
        return ContractResponse(response.status_code, response.json(), dict(response.headers))

    def openapi_paths(self) -> dict[str, list[str]]:
        from track_anywhere.api import app

        return {
            path: sorted(method for method in details if method in {"get", "post", "put", "patch", "delete"})
            for path, details in sorted(app.openapi()["paths"].items())
        }


class DjangoApiClient:
    name = "django"
    _migrated = False

    def __init__(self) -> None:
        import django
        from django.apps import apps
        from django.core.management import call_command
        from django.test import Client

        if not apps.ready:
            django.setup()
        if not DjangoApiClient._migrated:
            call_command("migrate", run_syncdb=True, verbosity=0)
            DjangoApiClient._migrated = True
        self._client = Client()

    def get(self, path: str, *, headers: dict[str, str] | None = None) -> ContractResponse:
        response = self._client.get(path, headers=headers)
        return parse_json_response(response.status_code, dict(response.headers), response.content)

    def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ContractResponse:
        response = self._client.post(path, json_body, content_type="application/json", headers=headers)
        return parse_json_response(response.status_code, dict(response.headers), response.content)

    def patch(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ContractResponse:
        response = self._client.patch(path, json_body, content_type="application/json", headers=headers)
        return parse_json_response(response.status_code, dict(response.headers), response.content)

    def openapi_paths(self) -> dict[str, list[str]]:
        from backend_django.track_anywhere_django.api import api

        return {
            path: sorted(method for method in details if method in {"get", "post", "put", "patch", "delete"})
            for path, details in sorted(api.get_openapi_schema()["paths"].items())
        }
