from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .security import Actor


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


@dataclass
class DeviceGrant:
    device_code_hash: str
    user_code_hash: str
    client_id: str
    scopes: tuple[str, ...]
    resource: str | None
    status: str
    expires_at: datetime
    interval_seconds: int
    created_at: datetime
    last_poll_at: datetime | None = None
    poll_count: int = 0
    approved_actor: Actor | None = None
    approved_at: datetime | None = None
