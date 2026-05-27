from __future__ import annotations

from datetime import timedelta
import secrets
from typing import Any

from .credential_commands import IssueCredentialCommand, IssueMachineCredentialCommand, RevokeCredentialByIdCommand, RevokeCredentialCommand
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
            if credential.actor.actor_type in {"agent", "machine"}
        ]

    def issue_agent_credential(
        self,
        token: str,
        scopes: set[str],
        ttl_minutes: int = 30,
        *,
        actor_id: str = "agent",
        actor_type: str = "agent",
        auth_kind: str = "api_key",
        name: str | None = None,
        description: str = "",
    ) -> str:
        actor = self.actor_from_token(token, "credential:write")
        if actor.actor_type != "human":
            raise ValidationError("only human owner credentials can issue machine credentials")
        unknown = scopes - AGENT_ALLOWED_SCOPES
        if unknown:
            raise ValidationError(f"unknown credential scopes: {sorted(unknown)}")
        raw_token, key_prefix = _new_machine_token() if actor_type == "machine" else (None, None)
        agent_token = self.credentials.issue(
            actor_id=actor_id,
            actor_type=actor_type,
            scopes=scopes,
            ttl=timedelta(minutes=ttl_minutes),
            token=raw_token,
            auth_kind=auth_kind,
            name=name,
            description=description,
            key_prefix=key_prefix,
            created_by_actor_id=actor.actor_id,
        )
        credential = self.credentials.get_by_token(agent_token)
        default_book = self.books.ensure_default()
        member = BookMember(
            book_id=default_book.book_id,
            user_id=actor_id,
            role="editor",
            scopes=sorted(scopes),
        )
        self.books.members[(default_book.book_id, actor_id)] = member
        audit_event = self.audit.record(
            operation="credential.issue",
            actor=actor,
            entity_ref=actor_id,
            details={"scopes": sorted(scopes), "auth_kind": auth_kind, "key_prefix": key_prefix},
        )
        if credential is None:
            raise ValidationError("issued credential could not be loaded")
        self.storage.save_credential_issue_state(
            book=default_book,
            member=member,
            credentials=self.credentials.dirty_credentials(),
            audit_event=audit_event,
        )
        self.credentials.mark_clean()
        self.audit.mark_persisted()
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
        self._commit_idempotency()
        return result

    def issue_machine_credential_command(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "credential:write")
        command = IssueMachineCredentialCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            machine_token = self.issue_agent_credential(
                token,
                set(command.scopes),
                command.ttl_minutes,
                actor_id="machine",
                actor_type="machine",
                auth_kind="m2m",
                name=command.name,
                description=command.description,
            )
            return {
                "token": machine_token,
                "scopes": command.scopes,
                "ttl_minutes": command.ttl_minutes,
                "name": command.name,
                "credential_type": command.credential_type,
            }

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="credential.machine.issue",
            request_hash=request_hash,
            fn=run,
            stored_result_factory=_credential_issue_replay_receipt,
        )
        self._commit_idempotency()
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

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="credential.revoke",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._commit_idempotency()
        else:
            self._commit_credential_change()
        return result, replay

    def revoke_credential_by_id_command(self, token: str, credential_id: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "credential:write")
        if actor.actor_type != "human":
            raise ValidationError("only human owner credentials can revoke credentials")
        command = RevokeCredentialByIdCommand.model_validate(payload)
        credential = self.credentials.get_by_jti(credential_id)
        if credential is None or credential.actor.actor_type not in {"agent", "machine"}:
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

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="credential.revoke_by_id",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._commit_idempotency()
        else:
            self._commit_credential_change()
        return result, replay

    def record_security_failure(self, operation: str, details: dict[str, Any] | None = None) -> None:
        event = self.audit.record(operation=operation, actor=SYSTEM_ACTOR, entity_ref=None, details=details or {})
        self.storage.save_audit_event(event)

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


def _credential_public_dict(credential) -> dict[str, Any]:
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


def _credential_issue_replay_receipt(result: dict[str, Any]) -> dict[str, Any]:
    # Agent bearer tokens are one-time secrets. Keep immediate in-memory
    # idempotency replay exact, but never persist the raw token in a receipt.
    return {**result, "token": "[ISSUED_ONCE]"}


def _new_machine_token() -> tuple[str, str]:
    prefix = secrets.token_urlsafe(5).replace("-", "").replace("_", "")[:8]
    key_prefix = f"ta_m2m_{prefix}"
    return f"{key_prefix}_{secrets.token_urlsafe(32)}", key_prefix
