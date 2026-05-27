from __future__ import annotations

from typing import Any

from .credential_commands import RevokeCredentialByIdCommand, RevokeCredentialCommand
from .errors import ValidationError


class CredentialRevocationUseCases:
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
        self._commit_replay_or(replay, self._commit_credential_change)
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
        self._commit_replay_or(replay, self._commit_credential_change)
        return result, replay
