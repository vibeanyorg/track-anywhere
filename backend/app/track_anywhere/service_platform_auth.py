from __future__ import annotations

from typing import Any

from .platform_auth_models import normalize_user_code
from .security import hash_secret


class PlatformAuthUseCases:
    def authorize_platform_oauth(self, exchange: Any, command: Any, actor: Any):
        return exchange.authorize(command, actor, grant_store=self.storage)

    def create_platform_device_authorization(self, exchange: Any, command: Any, issuer: str):
        return exchange.create_device_authorization(command, issuer, grant_store=self.storage)

    def approve_platform_device_user_code(
        self,
        exchange: Any,
        user_code: str,
        actor: Any,
        action: str,
        *,
        approved_scopes: list[str] | None,
    ):
        return exchange.approve_device_user_code(
            user_code,
            actor,
            action,
            grant_store=self.storage,
            approved_scopes=approved_scopes,
        )

    def pending_device_grant_for_user_code(self, user_code: str):
        return self.storage.load_device_grant_by_user_hash(hash_secret(normalize_user_code(user_code)))

    def exchange_platform_code(self, exchange: Any, command: Any):
        return exchange.exchange_code(
            command,
            grant_store=self.storage,
            credentials=self.credentials,
            audit=self.audit,
            credential_writer=self.storage,
        )

    def exchange_platform_device_code(self, exchange: Any, command: Any):
        return exchange.exchange_device_code(
            command,
            grant_store=self.storage,
            credentials=self.credentials,
            audit=self.audit,
            credential_writer=self.storage,
        )

    def revoke_platform_token(self, exchange: Any, command: Any):
        return exchange.revoke(command, credentials=self.credentials, credential_writer=self.storage)
