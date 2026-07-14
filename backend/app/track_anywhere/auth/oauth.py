from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..infrastructure.db.models.auth import (
    CredentialRecord,
    OAuthAuthorizationGrantRecord,
    OAuthClientRecord,
    OAuthClientRedirectUriRecord,
    UserRecord,
)
from ..infrastructure.db.repositories.auth import (
    AuthRecordNotFound,
    AuthRepository,
    CredentialUnavailable,
)
from .contracts import (
    OAuthAuthorizationCodeTokenCommand,
    OAuthAuthorizeCommand,
    OAuthRegisterCommand,
)
from .errors import AuthPolicyDenied, AuthSecurityError, OAuthFlowError
from .security import (
    new_secret,
    parse_requested_scopes,
    pkce_challenge,
    redirect_with_params,
    require_scope_subset,
    secret_digest,
    validate_redirect_uri,
)
from .sessions import ActiveBrowserSession


class PersistentOAuthService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def register_client(self, command: OAuthRegisterCommand) -> dict[str, object]:
        scopes = parse_requested_scopes(command.scope)
        redirects = tuple(dict.fromkeys(validate_redirect_uri(uri) for uri in command.redirect_uris))
        client_id = new_secret("client")
        client = OAuthClientRecord(
            client_id=client_id,
            client_name=command.client_name.strip(),
            client_type="public",
            client_secret_hash=None,
            scopes=list(scopes),
            status="active",
        )
        self._session.add(client)
        self._session.flush([client])
        self._session.add_all(
            OAuthClientRedirectUriRecord(
                client_id=client_id,
                redirect_uri=uri,
                status="active",
            )
            for uri in redirects
        )
        self._session.flush()
        return {
            "client_id": client_id,
            "client_name": client.client_name,
            "redirect_uris": list(redirects),
            "grant_types": ["authorization_code", "urn:ietf:params:oauth:grant-type:device_code"],
            "response_types": ["code"],
            "scope": " ".join(scopes),
            "token_endpoint_auth_method": "none",
        }

    def authorize(
        self,
        command: OAuthAuthorizeCommand,
        active: ActiveBrowserSession,
    ) -> dict[str, str]:
        client = self._active_client(command.client_id)
        self._require_redirect(client.client_id, command.redirect_uri)
        scopes = parse_requested_scopes(command.scope)
        require_scope_subset(scopes, set(client.scopes))
        require_scope_subset(scopes, set(active.credential.scopes))
        if command.action == "deny":
            return {
                "redirect_uri": redirect_with_params(
                    command.redirect_uri,
                    {"error": "access_denied", "state": command.state},
                )
            }
        raw_code = new_secret("code")
        now = datetime.now(UTC)
        self._session.add(
            OAuthAuthorizationGrantRecord(
                code_hash=secret_digest(raw_code),
                client_id=client.client_id,
                redirect_uri=command.redirect_uri,
                actor_subject_id=active.user.user_id,
                scopes=list(scopes),
                code_challenge=command.code_challenge,
                challenge_method="S256",
                resource=command.resource,
                created_at=now,
                expires_at=now + timedelta(minutes=10),
                used_at=None,
                revoked_at=None,
            )
        )
        self._session.flush()
        return {
            "redirect_uri": redirect_with_params(
                command.redirect_uri,
                {"code": raw_code, "state": command.state},
            )
        }

    def exchange_code(
        self,
        command: OAuthAuthorizationCodeTokenCommand,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        grant = self._session.execute(
            select(OAuthAuthorizationGrantRecord)
            .where(OAuthAuthorizationGrantRecord.code_hash == secret_digest(command.code))
            .with_for_update()
        ).scalar_one_or_none()
        if (
            grant is None
            or grant.used_at is not None
            or grant.revoked_at is not None
            or grant.expires_at <= now
            or grant.client_id != command.client_id
            or grant.redirect_uri != command.redirect_uri
            or grant.resource != command.resource
            or grant.code_challenge != pkce_challenge(command.code_verifier)
        ):
            raise OAuthFlowError("invalid_grant", "authorization code is invalid")
        grant.used_at = now
        return self.issue_access_token(
            actor_subject_id=grant.actor_subject_id,
            scopes=tuple(grant.scopes),
            auth_kind="pkce",
            issued_at=now,
        )

    def token_status(self, raw_token: str) -> dict[str, object]:
        repository = AuthRepository(self._session)
        try:
            credential = repository.mark_credential_used(
                secret_digest(raw_token), datetime.now(UTC)
            )
            user = repository.get_user(credential.actor_subject_id)
        except (AuthRecordNotFound, CredentialUnavailable, ValueError) as error:
            raise AuthPolicyDenied("credential is missing, expired, or revoked") from error
        if user.status != "active":
            raise AuthPolicyDenied("credential subject is disabled")
        return {
            "credential_id": str(credential.credential_id),
            "actor_subject_id": credential.actor_subject_id,
            "actor_type": credential.actor_type,
            "auth_kind": credential.auth_kind,
            "book_id": None if credential.book_id is None else str(credential.book_id),
            "scopes": list(credential.scopes),
            "expires_at": credential.expires_at.isoformat(),
        }

    def _active_client(self, client_id: str) -> OAuthClientRecord:
        client = self._session.get(OAuthClientRecord, client_id)
        if client is None or client.status != "active":
            raise AuthSecurityError("unknown OAuth client")
        return client

    def _require_redirect(self, client_id: str, redirect_uri: str) -> None:
        redirect = self._session.get(OAuthClientRedirectUriRecord, (client_id, redirect_uri))
        if redirect is None or redirect.status != "active":
            raise AuthSecurityError("redirect_uri is not registered")

    def revoke(self, raw_token: str) -> dict[str, bool]:
        try:
            AuthRepository(self._session).revoke_credential(
                secret_digest(raw_token), datetime.now(UTC)
            )
        except AuthRecordNotFound:
            pass
        return {"revoked": True}

    def issue_access_token(
        self,
        *,
        actor_subject_id: str,
        scopes: tuple[str, ...],
        auth_kind: str,
        issued_at: datetime,
    ) -> dict[str, object]:
        user = self._session.get(UserRecord, actor_subject_id)
        if user is None or user.status != "active":
            raise OAuthFlowError("invalid_grant", "authorization subject is unavailable")
        raw_token = new_secret("ta")
        expires_at = issued_at + timedelta(hours=1)
        self._session.add(
            CredentialRecord(
                credential_id=uuid4(),
                token_hash=secret_digest(raw_token),
                jti=uuid4(),
                actor_subject_id=user.user_id,
                actor_type=user.subject_type,
                auth_kind=auth_kind,
                book_id=None,
                scopes=list(scopes),
                issued_at=issued_at,
                expires_at=expires_at,
                revoked_at=None,
                last_used_at=None,
            )
        )
        self._session.flush()
        return {
            "access_token": raw_token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": " ".join(scopes),
        }


__all__ = ["PersistentOAuthService"]
