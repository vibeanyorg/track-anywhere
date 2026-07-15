from __future__ import annotations

from typing import Any

from track_anywhere_cli.config import CliConfig

from .api_clients import BackendApiClient


def requester_for_backend(client: BackendApiClient):
    def requester(
        config: CliConfig,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        key: str | None = None,
    ) -> tuple[int, Any]:
        headers = {}
        if config.api_key:
            headers["X-API-Key"] = config.api_key
        elif config.token:
            headers["Authorization"] = f"Bearer {config.token}"
        if key:
            headers["X-Idempotency-Key"] = key

        if method == "GET":
            response = client.get(path, headers=headers)
        elif method == "POST":
            response = client.post(path, json_body=payload, headers=headers)
        elif method == "PATCH":
            response = client.patch(path, json_body=payload, headers=headers)
        else:
            raise AssertionError(f"unsupported CLI contract method: {method}")
        return response.status_code, response.data

    return requester
