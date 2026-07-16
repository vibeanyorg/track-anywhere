from __future__ import annotations

from uuid import UUID

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy.orm import Session

from ..api.dependencies import SessionFactory
from ..auth.errors import AuthPolicyDenied
from ..auth.oauth import OAUTH_ACCESS_KINDS
from ..auth.sessions import PersistentSessionService
from ..infrastructure.db.repositories.auth import (
    AuthRecordNotFound,
    BookMembershipRepository,
)


MCP_REQUIRED_SCOPE = "ledger:read"
MCP_WRITE_SCOPE = "ledger:write"


class DatabaseTokenVerifier(TokenVerifier):
    def __init__(self, session_factory: SessionFactory, resource: str) -> None:
        self._session_factory = session_factory
        self._resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            with self._session_factory() as session, session.begin():
                credential, user = PersistentSessionService(
                    session
                ).authenticate_credential(
                    token,
                    allowed_auth_kinds=OAUTH_ACCESS_KINDS,
                    required_resource=self._resource,
                )
                if MCP_REQUIRED_SCOPE not in credential.scopes:
                    return None
                if credential.oauth_client_id is None:
                    return None
                return AccessToken(
                    token=token,
                    client_id=credential.oauth_client_id,
                    scopes=list(credential.scopes),
                    expires_at=int(credential.expires_at.timestamp()),
                    resource=credential.resource,
                    subject=user.user_id,
                    claims={
                        "actor_type": credential.actor_type,
                        "auth_kind": credential.auth_kind,
                        "book_id": (
                            None
                            if credential.book_id is None
                            else str(credential.book_id)
                        ),
                    },
                )
        except (AuthPolicyDenied, ValueError):
            return None


def require_access_token() -> AccessToken:
    token = get_access_token()
    if token is None or token.subject is None:
        raise ToolError("Authentication is required. Reconnect the Track Anywhere app.")
    if MCP_REQUIRED_SCOPE not in token.scopes:
        raise ToolError("The connection is missing the ledger:read scope.")
    return token


def require_write_access_token() -> AccessToken:
    token = require_access_token()
    if MCP_WRITE_SCOPE not in token.scopes:
        raise ToolError(
            "This action needs ledger:write. Reconnect the Track Anywhere app "
            "and explicitly approve write access."
        )
    return token


def require_book_access(
    session: Session,
    token: AccessToken,
    book_id: UUID,
) -> None:
    restricted_book_id = (token.claims or {}).get("book_id")
    if restricted_book_id is not None and restricted_book_id != str(book_id):
        raise ToolError("This connection is restricted to a different Book.")
    try:
        membership = BookMembershipRepository(session).get(book_id, token.subject or "")
    except AuthRecordNotFound as error:
        raise ToolError("Book not found or not accessible to this connection.") from error
    if (
        membership.status != "active"
        or membership.revoked_at is not None
        or MCP_REQUIRED_SCOPE not in membership.scopes
    ):
        raise ToolError("Book not found or not accessible to this connection.")


def require_book_write_access(
    session: Session,
    token: AccessToken,
    book_id: UUID,
) -> None:
    require_book_access(session, token, book_id)
    try:
        membership = BookMembershipRepository(session).get(
            book_id,
            token.subject or "",
        )
    except AuthRecordNotFound as error:  # pragma: no cover - read check owns this path.
        raise ToolError("Book not found or not writable by this connection.") from error
    if (
        membership.status != "active"
        or membership.revoked_at is not None
        or MCP_WRITE_SCOPE not in membership.scopes
    ):
        raise ToolError("Book not found or not writable by this connection.")


__all__ = [
    "DatabaseTokenVerifier",
    "MCP_REQUIRED_SCOPE",
    "MCP_WRITE_SCOPE",
    "require_access_token",
    "require_book_access",
    "require_book_write_access",
    "require_write_access_token",
]
