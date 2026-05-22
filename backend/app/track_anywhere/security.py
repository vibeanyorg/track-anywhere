from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from .deployment_security import DeploymentSecurityConfig, validate_startup_security
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
    "password",
    "raw_memo",
    "raw_note",
    "raw_text",
    "refresh_token",
    "secret",
    "target_token",
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


@dataclass(frozen=True)
class CredentialReference:
    token_hash: str


@dataclass
class Credential:
    token_hash: str
    actor: Actor
    issued_at: datetime
    expires_at: datetime
    jti: str
    revoked_at: datetime | None = None
    auth_kind: str = "api_key"
    name: str | None = None
    description: str = ""
    key_prefix: str | None = None
    created_by_actor_id: str | None = None
    last_used_at: datetime | None = None
    rotated_from_jti: str | None = None

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
        auth_kind: str = "api_key",
        name: str | None = None,
        description: str = "",
        key_prefix: str | None = None,
        created_by_actor_id: str | None = None,
        rotated_from_jti: str | None = None,
    ) -> str:
        token = token or f"ta_{uuid4().hex}"
        token_hash = hash_secret(token)
        self._credentials[token_hash] = Credential(
            token_hash=token_hash,
            actor=Actor(actor_id=actor_id, actor_type=actor_type, scopes=frozenset(scopes)),
            issued_at=utcnow(),
            expires_at=utcnow() + ttl,
            jti=uuid4().hex,
            auth_kind=auth_kind,
            name=name,
            description=description,
            key_prefix=key_prefix or f"ta_...{token_hash[:8]}",
            created_by_actor_id=created_by_actor_id,
            rotated_from_jti=rotated_from_jti,
        )
        return token

    def verify(self, token: str | CredentialReference, required_scope: str | None = None) -> Actor:
        token_hash = token.token_hash if isinstance(token, CredentialReference) else hash_secret(token)
        credential = self._credentials.get(token_hash)
        if credential is None or not credential.active:
            raise PolicyDenied("credential is missing, expired, or revoked")
        if required_scope is not None and required_scope not in credential.actor.scopes:
            raise PolicyDenied(f"credential lacks required scope: {required_scope}")
        credential.last_used_at = utcnow()
        return credential.actor

    def list(self) -> list[Credential]:
        return sorted(
            self._credentials.values(),
            key=lambda credential: (credential.issued_at, credential.jti),
            reverse=True,
        )

    def get_by_jti(self, jti: str) -> Credential | None:
        return next((credential for credential in self._credentials.values() if credential.jti == jti), None)

    def get_by_token(self, token: str) -> Credential | None:
        return self._credentials.get(hash_secret(token))

    def get(self, token: str | CredentialReference) -> Credential | None:
        token_hash = token.token_hash if isinstance(token, CredentialReference) else hash_secret(token)
        return self._credentials.get(token_hash)

    def revoke(self, token: str) -> None:
        credential = self._credentials.get(hash_secret(token))
        if credential is not None:
            credential.revoked_at = utcnow()

    def revoke_by_jti(self, jti: str) -> bool:
        credential = self.get_by_jti(jti)
        if credential is None:
            return False
        credential.revoked_at = utcnow()
        return True


@dataclass
class BrowserSession:
    session_id: str
    csrf_token_hash: str
    issued_at: datetime
    expires_at: datetime
    credential_hash: str | None = None
    identity: dict[str, Any] | None = None
    revoked_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None and self.expires_at > utcnow()


class BrowserSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSession] = {}

    def issue(
        self,
        *,
        ttl: timedelta = timedelta(hours=8),
        credential_token: str | None = None,
        credential_hash: str | None = None,
        identity: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        if credential_token and credential_hash:
            raise ValueError("pass either credential_token or credential_hash, not both")
        session_id = f"sess_{uuid4().hex}"
        csrf_token = f"csrf_{uuid4().hex}"
        self._sessions[session_id] = BrowserSession(
            session_id=session_id,
            csrf_token_hash=hash_secret(csrf_token),
            issued_at=utcnow(),
            expires_at=utcnow() + ttl,
            credential_hash=credential_hash or (hash_secret(credential_token) if credential_token else None),
            identity=dict(identity) if identity is not None else None,
        )
        return session_id, csrf_token

    def rotate(
        self,
        session_id: str,
        *,
        credential_token: str | None = None,
        credential_hash: str | None = None,
        identity: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        session = self._sessions.get(session_id)
        if session is not None:
            session.revoked_at = utcnow()
        return self.issue(credential_token=credential_token, credential_hash=credential_hash, identity=identity)

    def verify_csrf(self, session_id: str, csrf_token: str | None) -> bool:
        session = self._sessions.get(session_id)
        return bool(session and session.active and csrf_token and session.csrf_token_hash == hash_secret(csrf_token))

    def credential_for(self, session_id: str | None) -> CredentialReference | None:
        if session_id is None:
            return None
        session = self._sessions.get(session_id)
        if session is None or not session.active or session.credential_hash is None:
            return None
        return CredentialReference(session.credential_hash)

    def revoke(self, session_id: str | None) -> None:
        if session_id is None:
            return
        session = self._sessions.get(session_id)
        if session is not None:
            session.revoked_at = utcnow()

    def identity_for(self, session_id: str | None) -> dict[str, Any] | None:
        if session_id is None:
            return None
        session = self._sessions.get(session_id)
        if session is None or not session.active or session.identity is None:
            return None
        return dict(session.identity)


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

