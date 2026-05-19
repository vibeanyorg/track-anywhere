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
    "budget:read",
    "book:read",
    "book:write",
    "attachment:write",
    "credential:write",
    "user:read",
    "user:write",
}
AGENT_ALLOWED_SCOPES = OWNER_SCOPES - {"credential:write"}
SYSTEM_ACTOR = Actor(actor_id="system", actor_type="system", scopes=frozenset())

VIEWER_SCOPES = {scope for scope in OWNER_SCOPES if scope.endswith(":read")}
EDITOR_SCOPES = VIEWER_SCOPES | {
    "account:write",
    "attachment:write",
    "budget:write",
    "capture:draft",
    "category:write",
    "credit-card:write",
    "investment:write",
    "ledger:confirm",
    "ledger:reverse",
    "recurring:write",
}
ADMIN_SCOPES = EDITOR_SCOPES | {
    "book:write",
    "user:read",
    "user:write",
}

ROLE_SCOPES = {
    "owner": OWNER_SCOPES,
    "admin": ADMIN_SCOPES,
    "editor": EDITOR_SCOPES,
    "viewer": VIEWER_SCOPES,
}


def scopes_for_role(role: str) -> set[str]:
    try:
        return set(ROLE_SCOPES[role])
    except KeyError as exc:
        raise ValueError(f"unknown role: {role}") from exc
