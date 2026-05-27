from __future__ import annotations

from typing import Any

from .errors import ValidationError
from .service_credential_utils import credential_public_dict


class CredentialQueryUseCases:
    def list_agent_credentials(self, token: str) -> list[dict[str, Any]]:
        actor = self.actor_from_token(token, "credential:write")
        if actor.actor_type != "human":
            raise ValidationError("only human owner credentials can list API keys")
        return [
            credential_public_dict(credential)
            for credential in self.credentials.list()
            if credential.actor.actor_type in {"agent", "machine"}
        ]

    def credential_status(self, token) -> dict[str, Any]:
        actor = self.actor_from_token(token)
        credential = self.credentials.get(token)
        if credential is None:
            raise ValidationError("credential not found")
        return {
            "authenticated": True,
            "credential_id": credential.jti,
            "auth_kind": credential.auth_kind,
            "actor_id": actor.actor_id,
            "actor_type": actor.actor_type,
            "key_prefix": credential.key_prefix,
            "scopes": sorted(actor.scopes),
            "issued_at": credential.issued_at.isoformat(),
            "expires_at": credential.expires_at.isoformat(),
            "last_used_at": credential.last_used_at.isoformat() if credential.last_used_at else None,
        }
