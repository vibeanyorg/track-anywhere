from __future__ import annotations

from typing import Annotated, Protocol

from .base import ServiceDependency


class AuthPagesRouteService(Protocol):
    def actor_from_token(self, token, required_scope: str | None = None): ...
    def authorize_platform_oauth_for_actor(self, payload, actor) -> dict[str, str]: ...


AuthPagesService = Annotated[AuthPagesRouteService, ServiceDependency]
