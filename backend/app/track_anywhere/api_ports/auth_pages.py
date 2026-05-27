from __future__ import annotations

from typing import AbstractSet, Annotated, Any, Protocol

from ..password_auth import PasswordAccount
from .base import ServiceDependency


class AuthPagesRouteService(Protocol):
    def actor_from_token(self, token, required_scope: str | None = None): ...
    def authorize_platform_oauth_for_actor(self, payload, actor) -> dict[str, str]: ...
    def authenticate_password_account(self, *, email: str, password: str) -> PasswordAccount: ...
    def create_password_account(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None,
        signup_allowed_emails: AbstractSet[str],
    ) -> PasswordAccount: ...
    def login_password_account(self, account: PasswordAccount) -> dict[str, Any]: ...


AuthPagesService = Annotated[AuthPagesRouteService, ServiceDependency]
