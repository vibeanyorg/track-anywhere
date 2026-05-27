from __future__ import annotations

from typing import Annotated, Any, Protocol

from .base import AuditRecorder, ServiceDependency


class CredentialRouteService(AuditRecorder, Protocol):
    def list_agent_credentials(self, token): ...
    def issue_agent_credential_command(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def issue_machine_credential_command(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def revoke_credential_command(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def revoke_credential_by_id_command(
        self,
        token,
        credential_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ): ...


CredentialService = Annotated[CredentialRouteService, ServiceDependency]

