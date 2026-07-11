from __future__ import annotations

from typing import Any

from .commands import CreateUserCommand
from .users import AppUser


class UserUseCases:
    def create_user(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[AppUser, bool]:
        actor = self.actor_from_token(token, "user:write")
        command = CreateUserCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            user = self.users.create(username=command.username, display_name=command.display_name)
            self.audit.record(
                operation="user.create",
                actor=actor,
                entity_ref=user.user_id,
                details=command.model_dump(mode="json"),
            )
            return user

        user, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="user.create",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_user_change(user))
        return user, replay

    def list_users(self, token: str) -> list[AppUser]:
        self.actor_from_token(token, "user:read")
        return self.users.list()
