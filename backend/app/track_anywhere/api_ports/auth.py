from __future__ import annotations

from typing import AbstractSet, Annotated, Any, Protocol

from ..auth_identities import OAuthIdentity
from ..password_auth import PasswordAccount
from .base import AuditRecorder, ServiceDependency


class AuthRouteService(AuditRecorder, Protocol):
    def actor_from_token(self, token, required_scope: str | None = None): ...
    def credential_status(self, token) -> dict[str, object]: ...
    def create_password_account(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None,
        signup_allowed_emails: AbstractSet[str],
    ) -> PasswordAccount: ...
    def authenticate_password_account(self, *, email: str, password: str) -> PasswordAccount: ...
    def login_password_account(self, account: PasswordAccount) -> dict[str, Any]: ...
    def login_oauth_identity(
        self,
        identity: OAuthIdentity,
        *,
        role: str = "viewer",
        ttl_minutes: int = 480,
    ) -> dict[str, Any]: ...


AuthService = Annotated[AuthRouteService, ServiceDependency]
