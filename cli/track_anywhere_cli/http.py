from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import CliConfig, validate_transport_url
from .exit_codes import (
    EXIT_AUTH,
    EXIT_EXTERNAL_DEPENDENCY,
    EXIT_IDEMPOTENCY_CONFLICT,
    EXIT_NOT_FOUND,
    EXIT_POLICY_DENIED,
    EXIT_SECURITY_PRECONDITION,
    EXIT_STALE_VERSION,
    EXIT_SUCCESS,
    EXIT_VALIDATION,
)

V2_API_PREFIX = "/api/v2/"
OAUTH_METADATA_PATHS = frozenset(
    {
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/api/v2",
        "/.well-known/oauth-authorization-server",
    }
)


class FormPayload(dict[str, Any]):
    """Marker mapping encoded as application/x-www-form-urlencoded."""


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            "HTTP redirects are disabled for CLI API requests",
            headers,
            fp,
        )


_NO_REDIRECT_OPENER = urllib.request.build_opener(_RejectRedirectHandler())


def _open_request(request: urllib.request.Request, *, timeout: int):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def request_json(
    config: CliConfig,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    key: str | None = None,
) -> tuple[int, Any]:
    route_path = path.split("?", 1)[0]
    try:
        requested_endpoint = validate_transport_url(f"{config.base_url}{path}")
    except ValueError:
        requested_endpoint = None
    is_discovered_oauth_endpoint = (
        config.oauth_endpoint is not None
        and requested_endpoint == config.oauth_endpoint
    )
    if (
        not path.startswith(V2_API_PREFIX)
        and route_path not in OAUTH_METADATA_PATHS
        and not is_discovered_oauth_endpoint
    ):
        return 400, {
            "detail": "The CLI only permits API V2 and OAuth metadata routes.",
            "error": {
                "code": "unsupported_api_route",
                "category": "security",
                "message": "The CLI only permits API V2 and OAuth metadata routes.",
                "retryable": False,
            },
        }
    try:
        validate_transport_url(config.base_url)
    except ValueError as exc:
        return 400, {
            "detail": str(exc),
            "error": {
                "code": "insecure_transport",
                "category": "security",
                "message": str(exc),
                "retryable": False,
            },
        }
    if payload is None:
        body = None
    elif isinstance(payload, FormPayload):
        body = urllib.parse.urlencode(payload, doseq=True).encode("utf-8")
    else:
        body = json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = (
            "application/x-www-form-urlencoded"
            if isinstance(payload, FormPayload)
            else "application/json"
        )
    if config.api_key:
        headers["X-API-Key"] = config.api_key
    elif config.token:
        headers["Authorization"] = f"Bearer {config.token}"
    if key:
        headers["X-Idempotency-Key"] = key
    req = urllib.request.Request(
        f"{config.base_url}{path}", data=body, headers=headers, method=method
    )
    try:
        with _open_request(req, timeout=10) as response:
            try:
                return response.status, json.loads(response.read().decode("utf-8"))
            except json.JSONDecodeError as exc:
                return 502, _external_error_payload(
                    code="invalid_json_response",
                    message=f"API returned invalid JSON: {exc}",
                    retryable=True,
                    detail={"path": path, "method": method},
                )
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
        except Exception:
            parsed = {"detail": str(exc)}
        return exc.code, parsed
    except (TimeoutError, socket.timeout) as exc:
        return 504, _external_error_payload(
            code="write_outcome_unknown"
            if method.upper() not in {"GET", "HEAD", "OPTIONS"}
            else "api_timeout",
            message=(
                "API write request timed out before a response; outcome is unknown. "
                "Do not retry with a new idempotency key."
                if method.upper() not in {"GET", "HEAD", "OPTIONS"}
                else f"API request timed out: {exc}"
            ),
            retryable=method.upper() in {"GET", "HEAD", "OPTIONS"},
            detail={"base_url": config.base_url, "path": path, "method": method},
        )
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, socket.timeout):
            return 504, _external_error_payload(
                code="write_outcome_unknown"
                if method.upper() not in {"GET", "HEAD", "OPTIONS"}
                else "api_timeout",
                message=(
                    "API write request timed out before a response; outcome is unknown. "
                    "Do not retry with a new idempotency key."
                    if method.upper() not in {"GET", "HEAD", "OPTIONS"}
                    else f"API request timed out: {exc}"
                ),
                retryable=method.upper() in {"GET", "HEAD", "OPTIONS"},
                detail={"base_url": config.base_url, "path": path, "method": method},
            )
        return 503, _external_error_payload(
            code="api_unreachable",
            message=f"API request failed: {exc}",
            retryable=True,
            detail={"base_url": config.base_url, "path": path, "method": method},
        )


def with_query(path: str, params: dict[str, Any]) -> str:
    query = urllib.parse.urlencode(
        {key: value for key, value in params.items() if value not in (None, "")}
    )
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
    if status in {408, 429, 500, 502, 503, 504}:
        return EXIT_EXTERNAL_DEPENDENCY
    return EXIT_VALIDATION


def _external_error_payload(
    *, code: str, message: str, retryable: bool, detail: dict[str, Any]
) -> dict[str, Any]:
    return {
        "detail": message,
        "error": {
            "code": code,
            "category": "external_dependency",
            "message": message,
            "retryable": retryable,
            "detail": detail,
        },
    }
