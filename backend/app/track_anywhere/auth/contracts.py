from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


OWNER_SCOPES = frozenset(
    {
        "book:read",
        "book:write",
        "ledger:read",
        "ledger:write",
    }
)
AGENT_ALLOWED_SCOPES = OWNER_SCOPES
VIEWER_SCOPES = frozenset(scope for scope in OWNER_SCOPES if scope.endswith(":read"))
AUDITOR_SCOPES = VIEWER_SCOPES
EDITOR_SCOPES = VIEWER_SCOPES | frozenset({"ledger:write"})
ADMIN_SCOPES = EDITOR_SCOPES | frozenset({"book:write"})
ROLE_SCOPES = {
    "owner": OWNER_SCOPES,
    "admin": ADMIN_SCOPES,
    "editor": EDITOR_SCOPES,
    "viewer": VIEWER_SCOPES,
    "auditor": AUDITOR_SCOPES,
}

DEFAULT_PLATFORM_SCOPE = "book:read ledger:read"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
PKCE_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
PASSWORD_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+$")


class AuthCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OAuthProtocolCommand(BaseModel):
    # OAuth extension metadata is explicitly extensible. Unknown extension
    # parameters are ignored while known protocol parameters remain validated.
    model_config = ConfigDict(extra="ignore")


class ApiKeySessionCommand(AuthCommand):
    api_key: str = Field(min_length=1, max_length=512)


class PasswordSessionCommand(AuthCommand):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if (
            len(normalized) > 254
            or not PASSWORD_EMAIL_PATTERN.fullmatch(normalized)
        ):
            raise ValueError("email must be a valid address")
        return normalized


class PasswordSignupCommand(PasswordSessionCommand):
    display_name: str = Field(min_length=1, max_length=120)
    setup_key: str = Field(min_length=1, max_length=512)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("display_name must be nonblank")
        return normalized


class OAuthRegisterCommand(OAuthProtocolCommand):
    client_name: str = Field(min_length=1, max_length=120)
    redirect_uris: list[str] = Field(min_length=1, max_length=12)
    scope: str = Field(default=DEFAULT_PLATFORM_SCOPE, min_length=1, max_length=512)
    token_endpoint_auth_method: Literal["none"] = "none"
    grant_types: tuple[
        Literal[
            "authorization_code",
            "refresh_token",
            DEVICE_GRANT_TYPE,
        ],
        ...,
    ] = ("authorization_code", "refresh_token")
    response_types: tuple[Literal["code"], ...] = ("code",)


class OAuthAuthorizeCommand(OAuthProtocolCommand):
    response_type: Literal["code"] = "code"
    client_id: str = Field(min_length=1, max_length=256)
    redirect_uri: str = Field(min_length=1, max_length=512)
    scope: str = Field(default=DEFAULT_PLATFORM_SCOPE, min_length=1, max_length=512)
    state: str | None = Field(default=None, max_length=512)
    code_challenge: str = Field(min_length=43, max_length=128)
    code_challenge_method: Literal["S256"] = "S256"
    resource: str = Field(min_length=1, max_length=512)
    action: Literal["approve", "deny"] = "approve"

    @field_validator("code_challenge")
    @classmethod
    def validate_code_challenge(cls, value: str) -> str:
        if not PKCE_PATTERN.fullmatch(value):
            raise ValueError("code_challenge must be base64url PKCE text")
        return value


class OAuthAuthorizationCodeTokenCommand(OAuthProtocolCommand):
    grant_type: Literal["authorization_code"] = "authorization_code"
    code: str = Field(min_length=1, max_length=512)
    client_id: str = Field(min_length=1, max_length=256)
    redirect_uri: str = Field(min_length=1, max_length=512)
    code_verifier: str = Field(min_length=43, max_length=128)
    resource: str = Field(min_length=1, max_length=512)

    @field_validator("code_verifier")
    @classmethod
    def validate_code_verifier(cls, value: str) -> str:
        if not PKCE_PATTERN.fullmatch(value):
            raise ValueError("code_verifier must be base64url PKCE text")
        return value


class OAuthRefreshTokenCommand(OAuthProtocolCommand):
    grant_type: Literal["refresh_token"] = "refresh_token"
    refresh_token: str = Field(min_length=1, max_length=512)
    client_id: str = Field(min_length=1, max_length=256)
    resource: str = Field(min_length=1, max_length=512)
    scope: str | None = Field(default=None, min_length=1, max_length=512)


class OAuthDeviceAuthorizeCommand(OAuthProtocolCommand):
    client_id: str = Field(min_length=1, max_length=256)
    scope: str = Field(default=DEFAULT_PLATFORM_SCOPE, min_length=1, max_length=512)
    resource: str = Field(min_length=1, max_length=512)


class OAuthDeviceTokenCommand(OAuthProtocolCommand):
    grant_type: Literal[DEVICE_GRANT_TYPE] = DEVICE_GRANT_TYPE
    device_code: str = Field(min_length=1, max_length=512)
    client_id: str = Field(min_length=1, max_length=256)
    resource: str = Field(min_length=1, max_length=512)


class OAuthRevokeCommand(OAuthProtocolCommand):
    token: str = Field(min_length=1, max_length=512)
    token_type_hint: Literal["access_token", "refresh_token"] | None = None
    client_id: str | None = Field(default=None, min_length=1, max_length=256)


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
    "OAuthRefreshTokenCommand",
    "OAuthRegisterCommand",
    "OAuthRevokeCommand",
    "OWNER_SCOPES",
    "PasswordSessionCommand",
    "PasswordSignupCommand",
    "ROLE_SCOPES",
    "VIEWER_SCOPES",
]
