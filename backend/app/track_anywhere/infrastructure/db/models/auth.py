from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


_NOW = text("clock_timestamp()")
_SCOPE_ARRAY_CHECK = (
    "jsonb_typeof(scopes) = 'array' and "
    "scopes = jsonb_path_query_array(scopes, "
    '\'$[*] ? (@.type() == "string" && @ like_regex "\\\\S")\')'
)


class UserRecord(V2Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "subject_type in ('human', 'machine')", name="subject_type_valid"
        ),
        CheckConstraint(
            "btrim(current_display_name) <> ''", name="display_name_nonblank"
        ),
        CheckConstraint("status in ('active', 'disabled')", name="status_valid"),
        UniqueConstraint("user_id", "subject_type", name="uq_users_id_subject_type"),
    )

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(16))
    current_display_name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class BookMemberRecord(V2Base):
    __tablename__ = "book_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "role in ('owner', 'admin', 'editor', 'viewer', 'auditor')",
            name="role_valid",
        ),
        CheckConstraint("status in ('active', 'revoked')", name="status_valid"),
        CheckConstraint(_SCOPE_ARRAY_CHECK, name="scopes_array"),
        CheckConstraint(
            "(status = 'active' and revoked_at is null) "
            "or (status = 'revoked' and revoked_at is not null)",
            name="revocation_shape",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuthIdentityRecord(V2Base):
    __tablename__ = "auth_identities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "provider", "subject", name="uq_auth_identities_provider_subject"
        ),
        CheckConstraint("btrim(provider) <> ''", name="provider_nonblank"),
        CheckConstraint("btrim(subject) <> ''", name="subject_nonblank"),
        CheckConstraint("status in ('active', 'disabled')", name="status_valid"),
    )

    identity_id: Mapped[UUID] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    picture_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class PasswordAccountRecord(V2Base):
    __tablename__ = "password_accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("btrim(password_hash) <> ''", name="password_hash_nonblank"),
        CheckConstraint(
            "normalized_email = lower(btrim(normalized_email)) "
            "and normalized_email <> ''",
            name="email_normalized",
        ),
        CheckConstraint("status in ('active', 'disabled')", name="status_valid"),
    )

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    normalized_email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class CredentialRecord(V2Base):
    __tablename__ = "credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["actor_subject_id", "actor_type"],
            ["users.user_id", "users.subject_type"],
            name="fk_credentials_actor_subject_type",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("token_hash", name="uq_credentials_token_hash"),
        UniqueConstraint(
            "token_hash",
            "actor_subject_id",
            name="uq_credentials_token_actor",
        ),
        UniqueConstraint("jti", name="uq_credentials_jti"),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint("actor_type in ('human', 'machine')", name="actor_type_valid"),
        CheckConstraint(
            "auth_kind in ('api_key', 'pkce', 'device', 'browser_session')",
            name="auth_kind_valid",
        ),
        CheckConstraint(_SCOPE_ARRAY_CHECK, name="scopes_array"),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
        CheckConstraint(
            "revoked_at is null or revoked_at >= issued_at",
            name="revoked_after_issue",
        ),
        CheckConstraint(
            "last_used_at is null or last_used_at >= issued_at",
            name="last_used_after_issue",
        ),
        CheckConstraint(
            "actor_type <> 'machine' or book_id is not null",
            name="machine_book_required",
        ),
    )

    credential_id: Mapped[UUID] = mapped_column(primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    jti: Mapped[UUID] = mapped_column()
    actor_subject_id: Mapped[str] = mapped_column(String(128))
    actor_type: Mapped[str] = mapped_column(String(16))
    auth_kind: Mapped[str] = mapped_column(String(32))
    book_id: Mapped[UUID | None] = mapped_column(nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OAuthClientRecord(V2Base):
    __tablename__ = "oauth_clients"
    __table_args__ = (
        CheckConstraint("btrim(client_name) <> ''", name="client_name_nonblank"),
        CheckConstraint(
            "client_type in ('public', 'confidential')", name="client_type_valid"
        ),
        CheckConstraint(
            "(client_type = 'public' and client_secret_hash is null) "
            "or (client_type = 'confidential' "
            "and octet_length(client_secret_hash) = 32)",
            name="client_secret_shape",
        ),
        CheckConstraint(_SCOPE_ARRAY_CHECK, name="scopes_array"),
        CheckConstraint("status in ('active', 'disabled')", name="status_valid"),
    )

    client_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    client_name: Mapped[str] = mapped_column(Text)
    client_type: Mapped[str] = mapped_column(String(16))
    client_secret_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class OAuthClientRedirectUriRecord(V2Base):
    __tablename__ = "oauth_client_redirect_uris"
    __table_args__ = (
        ForeignKeyConstraint(
            ["client_id"],
            ["oauth_clients.client_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("btrim(redirect_uri) <> ''", name="redirect_uri_nonblank"),
        CheckConstraint("status in ('active', 'disabled')", name="status_valid"),
    )

    client_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    redirect_uri: Mapped[str] = mapped_column(String(512), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class OAuthAuthorizationGrantRecord(V2Base):
    __tablename__ = "oauth_authorization_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["client_id", "redirect_uri"],
            [
                "oauth_client_redirect_uris.client_id",
                "oauth_client_redirect_uris.redirect_uri",
            ],
            name="fk_oauth_authorization_grants_registered_redirect",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["actor_subject_id"],
            ["users.user_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("octet_length(code_hash) = 32", name="code_hash_length"),
        CheckConstraint(_SCOPE_ARRAY_CHECK, name="scopes_array"),
        CheckConstraint(
            "length(code_challenge) between 43 and 128",
            name="code_challenge_length",
        ),
        CheckConstraint("challenge_method = 'S256'", name="challenge_method_s256"),
        CheckConstraint("expires_at > created_at", name="expiry_after_create"),
        CheckConstraint(
            "used_at is null or used_at >= created_at", name="used_after_create"
        ),
        CheckConstraint(
            "revoked_at is null or revoked_at >= created_at",
            name="revoked_after_create",
        ),
        CheckConstraint(
            "used_at is null or revoked_at is null", name="terminal_state_exclusive"
        ),
    )

    code_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(256))
    redirect_uri: Mapped[str] = mapped_column(String(512))
    actor_subject_id: Mapped[str] = mapped_column(String(128))
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    code_challenge: Mapped[str] = mapped_column(String(128))
    challenge_method: Mapped[str] = mapped_column(String(8))
    resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OAuthDeviceGrantRecord(V2Base):
    __tablename__ = "oauth_device_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["client_id"],
            ["oauth_clients.client_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["approved_actor_subject_id"],
            ["users.user_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("user_code_hash", name="uq_oauth_device_user_code_hash"),
        CheckConstraint(
            "octet_length(device_code_hash) = 32", name="device_hash_length"
        ),
        CheckConstraint("octet_length(user_code_hash) = 32", name="user_hash_length"),
        CheckConstraint(_SCOPE_ARRAY_CHECK, name="scopes_array"),
        CheckConstraint(
            "status in ('pending', 'approved', 'denied', 'consumed', 'expired')",
            name="status_valid",
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_create"),
        CheckConstraint("interval_seconds > 0", name="interval_positive"),
        CheckConstraint("poll_count >= 0", name="poll_count_nonnegative"),
        CheckConstraint(
            "last_poll_at is null or last_poll_at >= created_at",
            name="last_poll_after_create",
        ),
        CheckConstraint(
            "(approved_actor_subject_id is null and approved_at is null) "
            "or (approved_actor_subject_id is not null and approved_at is not null)",
            name="approval_pair",
        ),
        CheckConstraint(
            "(status = 'pending' and approved_at is null and consumed_at is null) "
            "or (status = 'approved' and approved_at is not null "
            "and consumed_at is null) "
            "or (status = 'consumed' and approved_at is not null "
            "and consumed_at is not null and consumed_at >= approved_at) "
            "or (status = 'denied' and approved_at is null "
            "and consumed_at is null) "
            "or (status = 'expired' and consumed_at is null)",
            name="lifecycle_shape",
        ),
    )

    device_code_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    user_code_hash: Mapped[bytes] = mapped_column(LargeBinary)
    client_id: Mapped[str] = mapped_column(String(256))
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    interval_seconds: Mapped[int] = mapped_column(Integer)
    last_poll_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    poll_count: Mapped[int] = mapped_column(Integer)
    approved_actor_subject_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BrowserSessionRecord(V2Base):
    __tablename__ = "browser_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["credential_hash", "user_id"],
            ["credentials.token_hash", "credentials.actor_subject_id"],
            name="fk_browser_sessions_credential_subject",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("octet_length(session_hash) = 32", name="session_hash_length"),
        CheckConstraint("octet_length(csrf_token_hash) = 32", name="csrf_hash_length"),
        CheckConstraint(
            "octet_length(credential_hash) = 32", name="credential_hash_length"
        ),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
        CheckConstraint(
            "revoked_at is null or revoked_at >= issued_at",
            name="revoked_after_issue",
        ),
        CheckConstraint(
            "last_seen_at is null or last_seen_at >= issued_at",
            name="last_seen_after_issue",
        ),
    )

    session_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    csrf_token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    credential_hash: Mapped[bytes] = mapped_column(LargeBinary)
    user_id: Mapped[str] = mapped_column(String(128))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = [
    "AuthIdentityRecord",
    "BookMemberRecord",
    "BrowserSessionRecord",
    "CredentialRecord",
    "OAuthAuthorizationGrantRecord",
    "OAuthClientRecord",
    "OAuthClientRedirectUriRecord",
    "OAuthDeviceGrantRecord",
    "PasswordAccountRecord",
    "UserRecord",
]
