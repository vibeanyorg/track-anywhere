from __future__ import annotations

import base64
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import ValidationError
from .service_auth import AGENT_ALLOWED_SCOPES


DEFAULT_PLATFORM_CLIENT_ID = "track-anywhere-web"
DEFAULT_PLATFORM_SCOPE = "account:read book:read ledger:read"
ACCESS_TOKEN_TTL = timedelta(hours=1)
AUTHORIZATION_CODE_TTL = timedelta(minutes=10)
DEVICE_CODE_TTL = timedelta(minutes=15)
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DeviceGrantType = Literal["urn:ietf:params:oauth:grant-type:device_code"]
PKCE_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


class PlatformAuthCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiKeySessionCommand(PlatformAuthCommand):
    api_key: str = Field(min_length=1, max_length=512)


class OAuthAuthorizeCommand(PlatformAuthCommand):
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


class OAuthTokenCommand(PlatformAuthCommand):
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


class OAuthDeviceAuthorizeCommand(PlatformAuthCommand):
    client_id: str = Field(min_length=1, max_length=256)
    scope: str = Field(default=DEFAULT_PLATFORM_SCOPE, min_length=1, max_length=512)
    resource: str | None = Field(default=None, max_length=512)


class OAuthDeviceTokenCommand(PlatformAuthCommand):
    grant_type: DeviceGrantType = DEVICE_GRANT_TYPE
    device_code: str = Field(min_length=1, max_length=512)
    client_id: str = Field(min_length=1, max_length=256)
    resource: str | None = Field(default=None, max_length=512)


class OAuthRegisterCommand(PlatformAuthCommand):
    client_name: str = Field(min_length=1, max_length=120)
    redirect_uris: list[str] = Field(min_length=1, max_length=12)
    client_uri: str | None = Field(default=None, max_length=512)
    logo_uri: str | None = Field(default=None, max_length=512)
    scope: str = Field(default=DEFAULT_PLATFORM_SCOPE, min_length=1, max_length=512)
    token_endpoint_auth_method: Literal["none"] = "none"


class OAuthRevokeCommand(PlatformAuthCommand):
    token: str = Field(min_length=1, max_length=512)


class OAuthTokenError(Exception):
    def __init__(self, error: str, description: str, extra: dict[str, object] | None = None) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.extra = extra or {}


@dataclass(frozen=True)
class PlatformClient:
    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]
    scopes: tuple[str, ...]
    client_uri: str | None = None
    logo_uri: str | None = None


def parse_requested_scopes(scope: str) -> list[str]:
    scopes = [item.strip() for item in scope.split() if item.strip()]
    if not scopes:
        raise ValidationError("at least one OAuth scope is required")
    deduped = list(dict.fromkeys(scopes))
    unknown_scopes = set(deduped) - AGENT_ALLOWED_SCOPES
    if unknown_scopes:
        raise ValidationError(f"unknown OAuth scopes: {sorted(unknown_scopes)}")
    return deduped


def validate_redirect_uri(redirect_uri: str) -> str:
    parsed = urlparse(redirect_uri)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("redirect_uri must be an absolute http(s) URL")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValidationError("http redirect_uri is only allowed for local development")
    return redirect_uri


def redirect_uri_allowed(client: PlatformClient, redirect_uri: str) -> bool:
    if redirect_uri in client.redirect_uris:
        return True
    parsed = urlparse(redirect_uri)
    return (
        client.client_id == DEFAULT_PLATFORM_CLIENT_ID
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed.path in {"/auth/callback", "/api/v1/auth/callback", "/callback"}
    )


def redirect_with_params(redirect_uri: str, params: dict[str, str | None]) -> str:
    parsed = urlparse(redirect_uri)
    query = parse_qs(parsed.query)
    for key, value in params.items():
        if value is not None:
            query[key] = [value]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def pkce_challenge(verifier: str) -> str:
    digest = sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_user_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    token = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{token[:4]}-{token[4:]}"


def normalize_user_code(value: str) -> str:
    return value.strip().upper().replace(" ", "").replace("-", "")
