from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from .errors import PolicyDenied, SecurityPreconditionFailed


SENSITIVE_KEYS = {
    "account_number",
    "authorization",
    "access_token",
    "card_number",
    "credential",
    "idempotency_key",
    "memo",
    "nl_note",
    "note",
    "notes",
    "ocr_text",
    "raw_memo",
    "raw_note",
    "raw_text",
    "refresh_token",
    "request_body",
    "secret",
    "token",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def hash_secret(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_KEYS:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


@dataclass(frozen=True)
class Actor:
    actor_id: str
    actor_type: str
    scopes: frozenset[str]


@dataclass
class Credential:
    token_hash: str
    actor: Actor
    issued_at: datetime
    expires_at: datetime
    jti: str
    revoked_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None and self.expires_at > utcnow()


class CredentialStore:
    def __init__(self) -> None:
        self._credentials: dict[str, Credential] = {}

    def issue(
        self,
        *,
        actor_id: str,
        actor_type: str,
        scopes: set[str],
        ttl: timedelta = timedelta(hours=1),
        token: str | None = None,
    ) -> str:
        token = token or f"ta_{uuid4().hex}"
        token_hash = hash_secret(token)
        self._credentials[token_hash] = Credential(
            token_hash=token_hash,
            actor=Actor(actor_id=actor_id, actor_type=actor_type, scopes=frozenset(scopes)),
            issued_at=utcnow(),
            expires_at=utcnow() + ttl,
            jti=uuid4().hex,
        )
        return token

    def verify(self, token: str, required_scope: str | None = None) -> Actor:
        credential = self._credentials.get(hash_secret(token))
        if credential is None or not credential.active:
            raise PolicyDenied("credential is missing, expired, or revoked")
        if required_scope is not None and required_scope not in credential.actor.scopes:
            raise PolicyDenied(f"credential lacks required scope: {required_scope}")
        return credential.actor

    def revoke(self, token: str) -> None:
        credential = self._credentials.get(hash_secret(token))
        if credential is not None:
            credential.revoked_at = utcnow()


@dataclass
class BrowserSession:
    session_id: str
    csrf_token_hash: str
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None and self.expires_at > utcnow()


class BrowserSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSession] = {}

    def issue(self, *, ttl: timedelta = timedelta(hours=8)) -> tuple[str, str]:
        session_id = f"sess_{uuid4().hex}"
        csrf_token = f"csrf_{uuid4().hex}"
        self._sessions[session_id] = BrowserSession(
            session_id=session_id,
            csrf_token_hash=hash_secret(csrf_token),
            issued_at=utcnow(),
            expires_at=utcnow() + ttl,
        )
        return session_id, csrf_token

    def rotate(self, session_id: str) -> tuple[str, str]:
        session = self._sessions.get(session_id)
        if session is not None:
            session.revoked_at = utcnow()
        return self.issue()

    def verify_csrf(self, session_id: str, csrf_token: str | None) -> bool:
        session = self._sessions.get(session_id)
        return bool(session and session.active and csrf_token and session.csrf_token_hash == hash_secret(csrf_token))


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def validate_web_security(
    *,
    method: str,
    auth_mode: str,
    csrf_token: str | None,
    expected_csrf_token: str | None,
    origin: str | None,
    referer: str | None,
    allowed_origin: str,
) -> None:
    if method.upper() not in MUTATING_METHODS or auth_mode != "session":
        return
    if not csrf_token or csrf_token != expected_csrf_token:
        raise SecurityPreconditionFailed("missing or invalid CSRF token")
    origin_ok = origin == allowed_origin
    referer_ok = bool(referer and referer.startswith(allowed_origin))
    if not origin_ok and not referer_ok:
        raise SecurityPreconditionFailed("missing or invalid Origin/Referer")


@dataclass(frozen=True)
class DeploymentSecurityConfig:
    mode: str = "local"
    tls_enabled: bool = False
    key_provider_configured: bool = False
    encrypted_volume_documented: bool = False
    backup_encryption_documented: bool = False
    attachment_scanner_available: bool = False
    debug_raw_payload: bool = False
    local_dev_no_scan: bool = False


def validate_startup_security(config: DeploymentSecurityConfig) -> list[str]:
    warnings: list[str] = []
    if config.local_dev_no_scan and config.mode != "local":
        raise SecurityPreconditionFailed("local_dev_no_scan is only allowed in local mode")
    if config.mode == "local":
        if config.debug_raw_payload:
            warnings.append("debug raw payload override enabled in local mode")
        if config.local_dev_no_scan:
            warnings.append("attachment scanner bypass enabled in local mode")
        return warnings
    if not config.tls_enabled:
        raise SecurityPreconditionFailed("non-local deployment requires TLS")
    if not (config.key_provider_configured or config.encrypted_volume_documented):
        raise SecurityPreconditionFailed("non-local deployment requires key provider or encrypted-volume constraint")
    if not config.backup_encryption_documented:
        raise SecurityPreconditionFailed("non-local deployment requires backup encryption/restoration plan")
    if not config.attachment_scanner_available:
        raise SecurityPreconditionFailed("non-local deployment requires attachment scanner")
    if config.debug_raw_payload:
        raise SecurityPreconditionFailed("raw payload debug override is forbidden in non-local mode")
    return warnings
