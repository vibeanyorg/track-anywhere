from __future__ import annotations

from datetime import timedelta
from typing import Any

from .commands import IssueCredentialCommand, RevokeCredentialCommand
from .errors import ValidationError
from .service_auth import AGENT_ALLOWED_SCOPES, SYSTEM_ACTOR


class CredentialUseCases:
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

    def record_security_failure(self, operation: str, details: dict[str, Any] | None = None) -> None:
        self.audit.record(operation=operation, actor=SYSTEM_ACTOR, entity_ref=None, details=details or {})
        self._persist()
