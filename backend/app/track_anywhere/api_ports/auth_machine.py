from __future__ import annotations

from typing import Annotated, Any, Protocol

from .base import ServiceDependency


class AuthMachineRouteService(Protocol):
    def actor_from_token(self, token, required_scope: str | None = None): ...
    def issue_machine_credential_command(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def list_agent_credentials(self, token): ...
    def revoke_credential_by_id_command(
        self,
        token,
        credential_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ): ...


AuthMachineService = Annotated[AuthMachineRouteService, ServiceDependency]
