from __future__ import annotations

from typing import Any

from .errors import PolicyDenied
from .platform_auth_models import (
    DEVICE_GRANT_TYPE,
    OAuthDeviceTokenCommand,
    OAuthRevokeCommand,
    OAuthTokenCommand,
    OAuthTokenError,
    normalize_user_code,
)
from .security import hash_secret


class _PlatformCredentialWriter:
    def __init__(self, service) -> None:
        self._service = service

    def save_credential(self, _credential) -> None:
        self._service._commit_credential_change()

    def save_credential_and_audit_event(self, _credential, _audit_event) -> None:
        self._service._commit_credential_change()


class _PlatformGrantStore:
    def __init__(self, service) -> None:
        self._service = service

    def save_authorization_grant(self, grant) -> None:
        self._service._commit_authorization_grant_change(grant)

    def load_authorization_grant(self, code_hash: str):
        return self._service.storage.load_authorization_grant(code_hash)

    def save_device_grant(self, grant) -> None:
        self._service._commit_device_grant_change(grant)

    def load_device_grant_by_device_hash(self, device_code_hash: str):
        return self._service.storage.load_device_grant_by_device_hash(device_code_hash)

    def load_device_grant_by_user_hash(self, user_code_hash: str):
        return self._service.storage.load_device_grant_by_user_hash(user_code_hash)


class PlatformAuthUseCases:
    def _platform_credential_writer(self) -> _PlatformCredentialWriter:
        return _PlatformCredentialWriter(self)

    def _platform_grant_store(self) -> _PlatformGrantStore:
        return _PlatformGrantStore(self)

    def authorize_platform_oauth(self, exchange: Any, command: Any, actor: Any):
        return exchange.authorize(command, actor, grant_store=self._platform_grant_store())

    def list_platform_oauth_clients(self, token) -> list[dict[str, object]]:
        try:
            self.actor_from_token(token, "credential:write")
            return self.platform_key_exchange.list_clients()
        except PolicyDenied as exc:
            self.record_security_failure("oauth.client_list_denied", {"reason": str(exc)})
            raise

    def register_platform_oauth_client(self, payload: Any) -> dict[str, object]:
        return self.platform_key_exchange.register_client(payload)

    def authorize_platform_oauth_request(self, token, payload: Any):
        try:
            actor = self.actor_from_token(token)
            return self.authorize_platform_oauth(self.platform_key_exchange, payload, actor)
        except PolicyDenied as exc:
            self.record_security_failure("oauth.authorize_denied", {"client_id": payload.client_id, "reason": str(exc)})
            raise

    def exchange_platform_oauth_token_payload(self, payload: dict[str, Any]) -> dict[str, object]:
        try:
            if payload.get("grant_type") == DEVICE_GRANT_TYPE:
                command = OAuthDeviceTokenCommand.model_validate(payload)
                return self.exchange_platform_device_code(self.platform_key_exchange, command)
            command = OAuthTokenCommand.model_validate(payload)
            return self.exchange_platform_code(self.platform_key_exchange, command)
        except OAuthTokenError as exc:
            self.record_security_failure("oauth.token_denied", {"reason": exc.error})
            raise
        except PolicyDenied as exc:
            self.record_security_failure("oauth.token_denied", {"reason": str(exc)})
            raise

    def create_platform_device_authorization(self, exchange: Any, command: Any, issuer: str):
        return exchange.create_device_authorization(command, issuer, grant_store=self._platform_grant_store())

    def create_platform_device_authorization_request(self, payload: Any, issuer: str) -> dict[str, object]:
        return self.create_platform_device_authorization(self.platform_key_exchange, payload, issuer)

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
            grant_store=self._platform_grant_store(),
            approved_scopes=approved_scopes,
        )

    def pending_device_grant_for_user_code(self, user_code: str):
        return self._platform_grant_store().load_device_grant_by_user_hash(hash_secret(normalize_user_code(user_code)))

    def exchange_platform_code(self, exchange: Any, command: Any):
        return exchange.exchange_code(
            command,
            grant_store=self._platform_grant_store(),
            credentials=self.credentials,
            audit=self.audit,
            credential_writer=self._platform_credential_writer(),
        )

    def exchange_platform_device_code(self, exchange: Any, command: Any):
        return exchange.exchange_device_code(
            command,
            grant_store=self._platform_grant_store(),
            credentials=self.credentials,
            audit=self.audit,
            credential_writer=self._platform_credential_writer(),
        )

    def revoke_platform_token(self, exchange: Any, command: Any):
        return exchange.revoke(command, credentials=self.credentials, credential_writer=self._platform_credential_writer())

    def revoke_platform_oauth_token_payload(self, payload: dict[str, Any]) -> dict[str, bool]:
        command = OAuthRevokeCommand.model_validate(payload)
        return self.revoke_platform_token(self.platform_key_exchange, command)
