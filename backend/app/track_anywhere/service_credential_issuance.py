from __future__ import annotations

from datetime import timedelta
from typing import Any

from .books import BookMember
from .credential_commands import IssueCredentialCommand, IssueMachineCredentialCommand
from .errors import ValidationError
from .service_auth import AGENT_ALLOWED_SCOPES
from .service_credential_utils import credential_issue_replay_receipt, new_machine_token


class CredentialIssuanceUseCases:
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
        raw_token, key_prefix = new_machine_token() if actor_type == "machine" else (None, None)
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
        self.books.save_member(member)
        audit_event = self.audit.record(
            operation="credential.issue",
            actor=actor,
            entity_ref=actor_id,
            details={"scopes": sorted(scopes), "auth_kind": auth_kind, "key_prefix": key_prefix},
        )
        if credential is None:
            raise ValidationError("issued credential could not be loaded")
        self._commit_book_change()
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
            stored_result_factory=credential_issue_replay_receipt,
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
            stored_result_factory=credential_issue_replay_receipt,
        )
        self._commit_idempotency()
        return result
