from __future__ import annotations

from typing import Annotated, Protocol

from .base import ServiceDependency


class AuthDeviceRouteService(Protocol):
    def actor_from_token(self, token, required_scope: str | None = None): ...
    def approve_platform_device_user_code_for_actor(
        self,
        user_code: str,
        actor,
        action: str,
        *,
        approved_scopes: list[str] | None,
    ): ...
    def pending_device_grant_for_user_code(self, user_code: str): ...


AuthDeviceService = Annotated[AuthDeviceRouteService, ServiceDependency]
