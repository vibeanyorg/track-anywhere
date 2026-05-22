from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .storage_models import Base


class CredentialRecord(Base):
    __tablename__ = "credentials"

    token_hash: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(80))
    actor_type: Mapped[str] = mapped_column(String(40))
    scopes: Mapped[list[str]] = mapped_column(JSON)
    issued_at: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[str] = mapped_column(String(80))
    jti: Mapped[str] = mapped_column(String(80))
    revoked_at: Mapped[str | None] = mapped_column(String(80), nullable=True)
    auth_kind: Mapped[str] = mapped_column(String(40), default="api_key")
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str] = mapped_column(String(240), default="")
    key_prefix: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_by_actor_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_used_at: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rotated_from_jti: Mapped[str | None] = mapped_column(String(80), nullable=True)


class OAuthAuthorizationGrantRecord(Base):
    __tablename__ = "oauth_authorization_grants"

    code_hash: Mapped[str] = mapped_column(String(80), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(256))
    redirect_uri: Mapped[str] = mapped_column(String(512))
    actor_id: Mapped[str] = mapped_column(String(80))
    actor_type: Mapped[str] = mapped_column(String(40))
    actor_scopes: Mapped[list[str]] = mapped_column(JSON)
    scopes: Mapped[list[str]] = mapped_column(JSON)
    code_challenge: Mapped[str] = mapped_column(String(128))
    resource: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[str] = mapped_column(String(80))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class OAuthDeviceGrantRecord(Base):
    __tablename__ = "oauth_device_grants"

    device_code_hash: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_code_hash: Mapped[str] = mapped_column(String(80), index=True)
    client_id: Mapped[str] = mapped_column(String(256))
    scopes: Mapped[list[str]] = mapped_column(JSON)
    resource: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[str] = mapped_column(String(80))
    interval_seconds: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(80))
    last_poll_at: Mapped[str | None] = mapped_column(String(80), nullable=True)
    poll_count: Mapped[int] = mapped_column(Integer, default=0)
    approved_actor_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approved_actor_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approved_actor_scopes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    approved_at: Mapped[str | None] = mapped_column(String(80), nullable=True)
