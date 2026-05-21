from __future__ import annotations

from datetime import timedelta
from typing import Any

from .commands import IssueCredentialCommand, RevokeCredentialByIdCommand, RevokeCredentialCommand
from .books import BookMember
from .errors import ValidationError
from .service_auth import AGENT_ALLOWED_SCOPES, SYSTEM_ACTOR


class CredentialUseCases:
    def list_agent_credentials(self, token: str) -> list[dict[str, Any]]:
        actor = self.actor_from_token(token, "credential:write")
        if actor.actor_type != "human":
            raise ValidationError("only human owner credentials can list API keys")
        return [
            _credential_public_dict(credential)
            for credential in self.credentials.list()
            if credential.actor.actor_type == "agent"
        ]

    def issue_agent_credential(self, token: str, scopes: set[str], ttl_minutes: int = 30) -> str:
        actor = self.actor_from_token(token, "credential:write")
        if actor.actor_type != "human":
            raise ValidationError("only human owner credentials can issue agent credentials")
        unknown = scopes - AGENT_ALLOWED_SCOPES
        if unknown:
            raise ValidationError(f"unknown credential scopes: {sorted(unknown)}")
        agent_token = self.credentials.issue(
            actor_id="agent",
            actor_type="agent",
            scopes=scopes,
            ttl=timedelta(minutes=ttl_minutes),
        )
        default_book = self.books.ensure_default()
        self.books.members[(default_book.book_id, "agent")] = BookMember(
            book_id=default_book.book_id,
            user_id="agent",
            role="editor",
            scopes=sorted(scopes),
        )
        self.audit.record(
            operation="credential.issue",
            actor=actor,
            entity_ref="agent",
            details={"scopes": sorted(scopes), "token": agent_token},
        )
        self._persist()
        return agent_token

    def issue_agent_credential_command(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "credential:write")
        command = IssueCredentialCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            agent_token = self.issue_agent_credential(token, set(command.scopes), command.ttl_minutes)
            return {"token": agent_token, "scopes": command.scopes, "ttl_minutes": command.ttl_minutes}

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="credential.issue",
            request_hash=request_hash,
            fn=run,
            stored_result_factory=_credential_issue_replay_receipt,
        )
        self._persist()
        return result

    def revoke_credential_command(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "credential:write")
        if actor.actor_type != "human":
            raise ValidationError("only human owner credentials can revoke credentials")
        command = RevokeCredentialCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            self.credentials.revoke(command.target_token)
            self.audit.record(
                operation="credential.revoke",
                actor=actor,
                entity_ref="credential",
                details={"target_token": command.target_token, "reason": command.reason},
            )
            return {"revoked": True}

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="credential.revoke",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def revoke_credential_by_id_command(self, token: str, credential_id: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "credential:write")
        if actor.actor_type != "human":
            raise ValidationError("only human owner credentials can revoke credentials")
        command = RevokeCredentialByIdCommand.model_validate(payload)
        credential = self.credentials.get_by_jti(credential_id)
        if credential is None or credential.actor.actor_type != "agent":
            raise ValidationError("API key credential not found")
        request_hash = self._hash_command_payload(command, {"credential_id": credential_id})

        def run():
            self.credentials.revoke_by_jti(credential_id)
            self.audit.record(
                operation="credential.revoke",
                actor=actor,
                entity_ref=credential_id,
                details={"reason": command.reason},
            )
            return {"revoked": True, "credential_id": credential_id}

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="credential.revoke_by_id",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def record_security_failure(self, operation: str, details: dict[str, Any] | None = None) -> None:
        event = self.audit.record(operation=operation, actor=SYSTEM_ACTOR, entity_ref=None, details=details or {})
        self.storage.save_audit_event(event)


def _credential_public_dict(credential) -> dict[str, Any]:
    return {
        "credential_id": credential.jti,
        "key_prefix": f"ta_...{credential.token_hash[:8]}",
        "actor_id": credential.actor.actor_id,
        "actor_type": credential.actor.actor_type,
        "scopes": sorted(credential.actor.scopes),
        "issued_at": credential.issued_at.isoformat(),
        "expires_at": credential.expires_at.isoformat(),
        "revoked_at": credential.revoked_at.isoformat() if credential.revoked_at else None,
        "active": credential.active,
    }


def _credential_issue_replay_receipt(result: dict[str, Any]) -> dict[str, Any]:
    # Agent bearer tokens are one-time secrets. Keep immediate in-memory
    # idempotency replay exact, but never persist the raw token in a receipt.
    return {**result, "token": "[ISSUED_ONCE]"}
