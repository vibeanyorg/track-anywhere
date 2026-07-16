from __future__ import annotations

import base64
import hashlib
import hmac
from hashlib import sha256
import secrets
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .contracts import AGENT_ALLOWED_SCOPES, DEVICE_GRANT_TYPE
from .errors import AuthPolicyDenied, AuthSecurityError


PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 390_000
PASSWORD_SALT_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)


def secret_digest(value: str) -> bytes:
    if type(value) is not str or not value:
        raise ValueError("secret must be a non-empty string")
    return sha256(value.encode("utf-8")).digest()


def verify_password_hash(password: str, encoded: str) -> bool:
    """Verify the one password-hash format accepted by the V2 auth schema."""

    try:
        algorithm, iterations_text, salt, digest = encoded.split("$", 3)
        iterations = int(iterations_text)
    except (AttributeError, ValueError):
        return False
    if algorithm != PASSWORD_HASH_ALGORITHM:
        return False
    if iterations != PASSWORD_HASH_ITERATIONS:
        return False
    if len(salt) != 24 or any(
        character not in PASSWORD_SALT_ALPHABET for character in salt
    ):
        return False
    if len(digest) != 64 or digest.casefold() != digest:
        return False
    try:
        bytes.fromhex(digest)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(candidate, digest)


def hash_password(password: str) -> str:
    if type(password) is not str or not password:
        raise ValueError("password must be a non-empty string")
    salt = secrets.token_urlsafe(18)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return (
        f"{PASSWORD_HASH_ALGORITHM}${PASSWORD_HASH_ITERATIONS}"
        f"${salt}${digest}"
    )


def new_secret(prefix: str) -> str:
    if type(prefix) is not str or not prefix:
        raise ValueError("secret prefix must be non-empty")
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def new_user_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    token = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{token[:4]}-{token[4:]}"


def normalize_user_code(value: str) -> str:
    return value.strip().upper().replace(" ", "").replace("-", "")


def require_same_origin(
    *,
    origin: str | None,
    referer: str | None,
    allowed_origin: str,
) -> None:
    if origin is not None:
        if origin == allowed_origin:
            return
        raise AuthSecurityError("missing or invalid Origin/Referer")
    if referer:
        parsed = urlparse(referer)
        referer_origin = f"{parsed.scheme}://{parsed.netloc}"
        if referer_origin == allowed_origin:
            return
    raise AuthSecurityError("missing or invalid Origin/Referer")


def parse_requested_scopes(scope: str) -> tuple[str, ...]:
    scopes = tuple(dict.fromkeys(item for item in scope.split() if item))
    if not scopes:
        raise AuthSecurityError("at least one OAuth scope is required")
    unknown = set(scopes) - AGENT_ALLOWED_SCOPES
    if unknown:
        raise AuthSecurityError(f"unknown OAuth scopes: {sorted(unknown)}")
    return scopes


def require_scope_subset(requested: tuple[str, ...], available: set[str]) -> None:
    if not set(requested).issubset(available):
        raise AuthPolicyDenied("requested OAuth scope is not available")


def validate_redirect_uri(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise AuthSecurityError("redirect_uri must be an absolute http(s) URL")
    if parsed.scheme == "http" and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise AuthSecurityError("http redirect_uri is only allowed on loopback")
    return value


def redirect_uri_matches(registered: str, requested: str) -> bool:
    registered_parsed = urlparse(validate_redirect_uri(registered))
    requested_parsed = urlparse(validate_redirect_uri(requested))
    if registered_parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return hmac.compare_digest(registered, requested)
    if requested_parsed.hostname != registered_parsed.hostname:
        return False
    return (
        requested_parsed.scheme == registered_parsed.scheme == "http"
        and requested_parsed.path == registered_parsed.path
        and requested_parsed.params == registered_parsed.params
        and requested_parsed.query == registered_parsed.query
    )


def redirect_with_params(value: str, params: dict[str, str | None]) -> str:
    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    for key, item in params.items():
        if item is not None:
            query[key] = [item]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def pkce_challenge(verifier: str) -> str:
    digest = sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_server_metadata(issuer: str) -> dict[str, object]:
    base = issuer.rstrip("/")
    return {
        "issuer": f"{base}/",
        "authorization_endpoint": f"{base}/api/v2/oauth/authorize",
        "token_endpoint": f"{base}/api/v2/oauth/token",
        "device_authorization_endpoint": f"{base}/api/v2/oauth/device/authorize",
        "registration_endpoint": f"{base}/api/v2/oauth/register",
        "revocation_endpoint": f"{base}/api/v2/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": [
            "authorization_code",
            "refresh_token",
            DEVICE_GRANT_TYPE,
        ],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": sorted(AGENT_ALLOWED_SCOPES),
        "resource_parameter_supported": True,
    }


def protected_resource_metadata(
    issuer: str,
    resource: str,
    *,
    scopes: tuple[str, ...] | None = None,
) -> dict[str, object]:
    base = issuer.rstrip("/")
    return {
        "resource": resource,
        "authorization_servers": [f"{base}/"],
        "bearer_methods_supported": ["header"],
        "scopes_supported": sorted(scopes or AGENT_ALLOWED_SCOPES),
    }


def protected_resource_metadata_url(resource: str) -> str:
    parsed = urlparse(resource)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AuthSecurityError("resource must be an absolute http(s) URL")
    suffix = parsed.path.rstrip("/")
    return urlunparse(
        parsed._replace(
            path=f"/.well-known/oauth-protected-resource{suffix}",
            params="",
            query="",
            fragment="",
        )
    )


__all__ = [
    "authorization_server_metadata",
    "hash_password",
    "new_secret",
    "new_user_code",
    "normalize_user_code",
    "parse_requested_scopes",
    "pkce_challenge",
    "protected_resource_metadata",
    "protected_resource_metadata_url",
    "redirect_uri_matches",
    "redirect_with_params",
    "require_scope_subset",
    "require_same_origin",
    "secret_digest",
    "validate_redirect_uri",
    "verify_password_hash",
]
