from __future__ import annotations

from typing import Any

IDEMPOTENCY_SECRET_KEYS = {
    "account_number",
    "api_key",
    "authorization",
    "access_token",
    "card_number",
    "credential",
    "csrf_token",
    "idempotency_key",
    "memo",
    "note",
    "notes",
    "password",
    "raw_memo",
    "refresh_token",
    "target_token",
    "token",
    "secret",
}


def redact_idempotency_result(value: Any) -> Any:
    """Redact secrets and free-text notes from persisted replay data."""

    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if _is_idempotency_secret_key(key) and item not in ("", None)
                else redact_idempotency_result(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_idempotency_result(item) for item in value]
    return value


def _is_idempotency_secret_key(key: str) -> bool:
    key_lower = key.lower()
    return (
        key_lower in IDEMPOTENCY_SECRET_KEYS
        or key_lower.endswith("_token")
        or key_lower.endswith("_secret")
        or key_lower.endswith("_password")
    )
