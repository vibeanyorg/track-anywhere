from __future__ import annotations

from .security import Actor


OWNER_SCOPES = {
    "account:read",
    "account:write",
    "category:read",
    "category:write",
    "credit-card:read",
    "credit-card:write",
    "capture:draft",
    "ledger:confirm",
    "ledger:read",
    "ledger:reverse",
    "recurring:read",
    "recurring:write",
    "investment:read",
    "investment:write",
    "budget:write",
    "attachment:write",
    "credential:write",
    "user:read",
    "user:write",
}
AGENT_ALLOWED_SCOPES = OWNER_SCOPES - {"credential:write"}
SYSTEM_ACTOR = Actor(actor_id="system", actor_type="system", scopes=frozenset())
