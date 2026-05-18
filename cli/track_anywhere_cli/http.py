from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import CliConfig
from .exit_codes import (
    EXIT_AUTH,
    EXIT_IDEMPOTENCY_CONFLICT,
    EXIT_NOT_FOUND,
    EXIT_POLICY_DENIED,
    EXIT_SECURITY_PRECONDITION,
    EXIT_STALE_VERSION,
    EXIT_SUCCESS,
    EXIT_VALIDATION,
)


def request_json(config: CliConfig, method: str, path: str, payload: dict[str, Any] | None = None, key: str | None = None) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if config.token:
        headers["Authorization"] = f"Bearer {config.token}"
    if key:
        headers["X-Idempotency-Key"] = key
    req = urllib.request.Request(f"{config.base_url}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
        except Exception:
            parsed = {"detail": str(exc)}
        return exc.code, parsed


def with_query(path: str, params: dict[str, Any]) -> str:
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value not in (None, "")})
    return f"{path}?{query}" if query else path


def exit_for_status(status: int, detail: Any) -> int:
    if status < 400:
        return EXIT_SUCCESS
    text = json.dumps(detail)
    if status == 401:
        return EXIT_AUTH
    if status == 403:
        return EXIT_POLICY_DENIED
    if status == 409 and "idempotency" in text:
        return EXIT_IDEMPOTENCY_CONFLICT
    if status == 409:
        return EXIT_STALE_VERSION
    if status == 404:
        return EXIT_NOT_FOUND
    if status == 400:
        return EXIT_SECURITY_PRECONDITION
    return EXIT_VALIDATION
