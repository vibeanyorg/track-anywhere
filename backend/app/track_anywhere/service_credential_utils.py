from __future__ import annotations

import secrets
from typing import Any


def credential_public_dict(credential) -> dict[str, Any]:
    return {
        "credential_id": credential.jti,
        "key_prefix": credential.key_prefix or f"ta_...{credential.token_hash[:8]}",
        "actor_id": credential.actor.actor_id,
        "actor_type": credential.actor.actor_type,
        "auth_kind": credential.auth_kind,
        "name": credential.name,
        "description": credential.description,
        "scopes": sorted(credential.actor.scopes),
        "issued_at": credential.issued_at.isoformat(),
        "expires_at": credential.expires_at.isoformat(),
        "last_used_at": credential.last_used_at.isoformat() if credential.last_used_at else None,
        "revoked_at": credential.revoked_at.isoformat() if credential.revoked_at else None,
        "active": credential.active,
    }


def credential_issue_replay_receipt(result: dict[str, Any]) -> dict[str, Any]:
    # Bearer tokens are one-time secrets. Keep immediate in-memory
    # idempotency replay exact, but never persist the raw token in a receipt.
    return {**result, "token": "[ISSUED_ONCE]"}


def new_machine_token() -> tuple[str, str]:
    prefix = secrets.token_urlsafe(5).replace("-", "").replace("_", "")[:8]
    key_prefix = f"ta_m2m_{prefix}"
    return f"{key_prefix}_{secrets.token_urlsafe(32)}", key_prefix
