from __future__ import annotations

import base64
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import PolicyDenied, ValidationError
from .security import Actor, hash_secret, utcnow
from .service_auth import AGENT_ALLOWED_SCOPES


DEFAULT_PLATFORM_CLIENT_ID = "track-anywhere-web"
DEFAULT_PLATFORM_SCOPE = "account:read book:read ledger:read"
ACCESS_TOKEN_TTL = timedelta(hours=1)
AUTHORIZATION_CODE_TTL = timedelta(minutes=10)
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


class OAuthRegisterCommand(PlatformAuthCommand):
    client_name: str = Field(min_length=1, max_length=120)
    redirect_uris: list[str] = Field(min_length=1, max_length=12)
    client_uri: str | None = Field(default=None, max_length=512)
    logo_uri: str | None = Field(default=None, max_length=512)
    scope: str = Field(default=DEFAULT_PLATFORM_SCOPE, min_length=1, max_length=512)
    token_endpoint_auth_method: Literal["none"] = "none"


class OAuthRevokeCommand(PlatformAuthCommand):
    token: str = Field(min_length=1, max_length=512)


@dataclass(frozen=True)
class PlatformClient:
    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]
    scopes: tuple[str, ...]
    client_uri: str | None = None
    logo_uri: str | None = None


@dataclass
class AuthorizationGrant:
    code_hash: str
    client_id: str
    redirect_uri: str
    actor: Actor
    scopes: tuple[str, ...]
    code_challenge: str
    resource: str | None
    expires_at: datetime
    used: bool = False


class PlatformKeyExchange:
    def __init__(self) -> None:
        self._clients: dict[str, PlatformClient] = {}
        self._codes: dict[str, AuthorizationGrant] = {}
        self._register_default_client()

    def register_client(self, command: OAuthRegisterCommand) -> dict[str, object]:
        redirect_uris = tuple(_validate_redirect_uri(uri) for uri in command.redirect_uris)
        scopes = _parse_requested_scopes(command.scope)
        unknown_scopes = set(scopes) - AGENT_ALLOWED_SCOPES
        if unknown_scopes:
            raise ValidationError(f"unknown OAuth scopes: {sorted(unknown_scopes)}")

        client_id = f"client_{secrets.token_urlsafe(24)}"
        client = PlatformClient(
            client_id=client_id,
            client_name=command.client_name,
            redirect_uris=redirect_uris,
            scopes=tuple(scopes),
            client_uri=command.client_uri,
            logo_uri=command.logo_uri,
        )
        self._clients[client_id] = client
        return self.client_public_dict(client)

    def list_clients(self) -> list[dict[str, object]]:
        clients = sorted(self._clients.values(), key=lambda client: (client.client_name, client.client_id))
        return [self.client_public_dict(client) for client in clients]

    def authorize(self, command: OAuthAuthorizeCommand, actor: Actor) -> dict[str, str]:
        if command.action == "deny":
            return {"redirect_uri": _redirect_with_params(command.redirect_uri, {"error": "access_denied", "state": command.state})}

        client = self._client_for(command.client_id)
        redirect_uri = _validate_redirect_uri(command.redirect_uri)
        if redirect_uri not in client.redirect_uris:
            raise ValidationError("redirect_uri is not registered for this client")

        scopes = _parse_requested_scopes(command.scope)
        unknown_scopes = set(scopes) - set(client.scopes)
        if unknown_scopes:
            raise ValidationError(f"client is not allowed to request scopes: {sorted(unknown_scopes)}")
        disallowed_scopes = set(scopes) - set(actor.scopes)
        if disallowed_scopes:
            raise PolicyDenied(f"actor lacks requested scopes: {sorted(disallowed_scopes)}")
        if "credential:write" in scopes:
            raise PolicyDenied("platform exchanges may not mint credential:write tokens")

        code = f"code_{secrets.token_urlsafe(32)}"
        code_hash = hash_secret(code)
        self._codes[code_hash] = AuthorizationGrant(
            code_hash=code_hash,
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            actor=actor,
            scopes=tuple(scopes),
            code_challenge=command.code_challenge,
            resource=command.resource,
            expires_at=utcnow() + AUTHORIZATION_CODE_TTL,
        )
        return {"redirect_uri": _redirect_with_params(redirect_uri, {"code": code, "state": command.state})}

    def exchange_code(self, command: OAuthTokenCommand, service) -> dict[str, object]:
        grant = self._codes.get(hash_secret(command.code))
        if grant is None or grant.used or grant.expires_at <= utcnow():
            raise PolicyDenied("authorization code is invalid or expired")
        if grant.client_id != command.client_id:
            raise PolicyDenied("authorization code client mismatch")
        if grant.redirect_uri != _validate_redirect_uri(command.redirect_uri):
            raise PolicyDenied("authorization code redirect_uri mismatch")
        if grant.resource != command.resource:
            raise PolicyDenied("authorization code resource mismatch")
        if _pkce_challenge(command.code_verifier) != grant.code_challenge:
            raise PolicyDenied("invalid PKCE code verifier")

        grant.used = True
        access_token = service.credentials.issue(
            actor_id=grant.actor.actor_id,
            actor_type=grant.actor.actor_type,
            scopes=set(grant.scopes),
            ttl=ACCESS_TOKEN_TTL,
        )
        service.audit.record(
            operation="oauth.token.exchange",
            actor=grant.actor,
            entity_ref=grant.client_id,
            details={"scopes": sorted(grant.scopes), "resource": grant.resource},
        )
        service._persist()
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
            "scope": " ".join(grant.scopes),
        }

    def revoke(self, command: OAuthRevokeCommand, service) -> dict[str, bool]:
        service.credentials.revoke(command.token)
        service._persist()
        return {"revoked": True}

    def authorization_server_metadata(self, issuer: str) -> dict[str, object]:
        base = issuer.rstrip("/")
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/api/v1/oauth/authorize",
            "token_endpoint": f"{base}/api/v1/oauth/token",
            "registration_endpoint": f"{base}/api/v1/oauth/register",
            "revocation_endpoint": f"{base}/api/v1/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": sorted(AGENT_ALLOWED_SCOPES),
        }

    def protected_resource_metadata(self, issuer: str) -> dict[str, object]:
        base = issuer.rstrip("/")
        return {
            "resource": f"{base}/api/v1",
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
            "scopes_supported": sorted(AGENT_ALLOWED_SCOPES),
        }

    def client_public_dict(self, client: PlatformClient) -> dict[str, object]:
        return {
            "client_id": client.client_id,
            "client_name": client.client_name,
            "redirect_uris": list(client.redirect_uris),
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "scope": " ".join(client.scopes),
            "token_endpoint_auth_method": "none",
            "client_uri": client.client_uri,
            "logo_uri": client.logo_uri,
        }

    def _client_for(self, client_id: str) -> PlatformClient:
        client = self._clients.get(client_id)
        if client is None:
            raise ValidationError("unknown OAuth client")
        return client

    def _register_default_client(self) -> None:
        scopes = tuple(sorted(AGENT_ALLOWED_SCOPES))
        self._clients[DEFAULT_PLATFORM_CLIENT_ID] = PlatformClient(
            client_id=DEFAULT_PLATFORM_CLIENT_ID,
            client_name="Track Anywhere Web",
            redirect_uris=(
                "http://localhost:3000/auth/callback",
                "http://127.0.0.1:3000/auth/callback",
            ),
            scopes=scopes,
        )


def _parse_requested_scopes(scope: str) -> list[str]:
    scopes = [item.strip() for item in scope.split() if item.strip()]
    if not scopes:
        raise ValidationError("at least one OAuth scope is required")
    deduped = list(dict.fromkeys(scopes))
    unknown_scopes = set(deduped) - AGENT_ALLOWED_SCOPES
    if unknown_scopes:
        raise ValidationError(f"unknown OAuth scopes: {sorted(unknown_scopes)}")
    return deduped


def _validate_redirect_uri(redirect_uri: str) -> str:
    parsed = urlparse(redirect_uri)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("redirect_uri must be an absolute http(s) URL")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValidationError("http redirect_uri is only allowed for local development")
    return redirect_uri


def _redirect_with_params(redirect_uri: str, params: dict[str, str | None]) -> str:
    parsed = urlparse(redirect_uri)
    query = parse_qs(parsed.query)
    for key, value in params.items():
        if value is not None:
            query[key] = [value]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _pkce_challenge(verifier: str) -> str:
    digest = sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
