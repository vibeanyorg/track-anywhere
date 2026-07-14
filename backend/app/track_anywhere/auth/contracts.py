from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


OWNER_SCOPES = frozenset(
    {
        "account:read",
        "account:write",
        "attachment:write",
        "book:read",
        "book:write",
        "budget:read",
        "budget:write",
        "capture:draft",
        "category:read",
        "category:write",
        "credential:write",
        "credit-card:read",
        "credit-card:write",
        "investment:read",
        "investment:write",
        "ledger:confirm",
        "ledger:read",
        "ledger:reverse",
        "recurring:read",
        "recurring:write",
        "user:read",
        "user:write",
    }
)
AGENT_ALLOWED_SCOPES = OWNER_SCOPES - {"credential:write"}
VIEWER_SCOPES = frozenset(scope for scope in OWNER_SCOPES if scope.endswith(":read"))
AUDITOR_SCOPES = VIEWER_SCOPES
EDITOR_SCOPES = VIEWER_SCOPES | frozenset(
    {
        "account:write",
        "attachment:write",
        "budget:write",
        "capture:draft",
        "category:write",
        "credit-card:write",
        "investment:write",
        "ledger:confirm",
        "ledger:reverse",
        "recurring:write",
    }
)
ADMIN_SCOPES = EDITOR_SCOPES | frozenset(
    {"book:write", "user:read", "user:write"}
)
ROLE_SCOPES = {
    "owner": OWNER_SCOPES,
    "admin": ADMIN_SCOPES,
    "editor": EDITOR_SCOPES,
    "viewer": VIEWER_SCOPES,
    "auditor": AUDITOR_SCOPES,
}

DEFAULT_PLATFORM_SCOPE = "account:read book:read ledger:read"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
PKCE_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


class AuthCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiKeySessionCommand(AuthCommand):
    api_key: str = Field(min_length=1, max_length=512)


class OAuthRegisterCommand(AuthCommand):
    client_name: str = Field(min_length=1, max_length=120)
    redirect_uris: list[str] = Field(min_length=1, max_length=12)
    scope: str = Field(default=DEFAULT_PLATFORM_SCOPE, min_length=1, max_length=512)
    token_endpoint_auth_method: Literal["none"] = "none"


class OAuthAuthorizeCommand(AuthCommand):
    client_id: str = Field(min_length=1, max_length=256)
    redirect_uri: str = Field(min_length=1, max_length=512)
    scope: str = Field(default=DEFAULT_PLATFORM_SCOPE, min_length=1, max_length=512)
    state: str | None = Field(default=None, max_length=512)
    code_challenge: str = Field(min_length=43, max_length=128)
    code_challenge_method: Literal["S256"] = "S256"
    resource: str | None = Field(default=None, max_length=512)
    action: Literal["approve", "deny"] = "approve"

    @field_validator("code_challenge")
    @classmethod
    def validate_code_challenge(cls, value: str) -> str:
        if not PKCE_PATTERN.fullmatch(value):
            raise ValueError("code_challenge must be base64url PKCE text")
        return value


class OAuthAuthorizationCodeTokenCommand(AuthCommand):
    grant_type: Literal["authorization_code"] = "authorization_code"
    code: str = Field(min_length=1, max_length=512)
    client_id: str = Field(min_length=1, max_length=256)
    redirect_uri: str = Field(min_length=1, max_length=512)
    code_verifier: str = Field(min_length=43, max_length=128)
    resource: str | None = Field(default=None, max_length=512)

    @field_validator("code_verifier")
    @classmethod
    def validate_code_verifier(cls, value: str) -> str:
        if not PKCE_PATTERN.fullmatch(value):
            raise ValueError("code_verifier must be base64url PKCE text")
        return value


class OAuthDeviceAuthorizeCommand(AuthCommand):
    client_id: str = Field(min_length=1, max_length=256)
    scope: str = Field(default=DEFAULT_PLATFORM_SCOPE, min_length=1, max_length=512)
    resource: str | None = Field(default=None, max_length=512)


class OAuthDeviceTokenCommand(AuthCommand):
    grant_type: Literal[DEVICE_GRANT_TYPE] = DEVICE_GRANT_TYPE
    device_code: str = Field(min_length=1, max_length=512)
    client_id: str = Field(min_length=1, max_length=256)
    resource: str | None = Field(default=None, max_length=512)


class OAuthRevokeCommand(AuthCommand):
    token: str = Field(min_length=1, max_length=512)


class DeviceApprovalCommand(AuthCommand):
    user_code: str = Field(min_length=1, max_length=32)
    action: Literal["approve", "deny"] = "approve"
    approved_scopes: tuple[str, ...] | None = None


__all__ = [
    "ADMIN_SCOPES",
    "AGENT_ALLOWED_SCOPES",
    "AUDITOR_SCOPES",
    "ApiKeySessionCommand",
    "DEFAULT_PLATFORM_SCOPE",
    "DEVICE_GRANT_TYPE",
    "DeviceApprovalCommand",
    "EDITOR_SCOPES",
    "OAuthAuthorizationCodeTokenCommand",
    "OAuthAuthorizeCommand",
    "OAuthDeviceAuthorizeCommand",
    "OAuthDeviceTokenCommand",
    "OAuthRegisterCommand",
    "OAuthRevokeCommand",
    "OWNER_SCOPES",
    "ROLE_SCOPES",
    "VIEWER_SCOPES",
]
