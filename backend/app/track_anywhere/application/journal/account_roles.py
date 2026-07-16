from __future__ import annotations

from ...infrastructure.db.repositories.catalogs import AccountSnapshot


class SystemManagedAccountForbidden(ValueError):
    pass


def require_standard_accounts(*accounts: AccountSnapshot) -> None:
    if any(account.system_role not in {None, "standard"} for account in accounts):
        raise SystemManagedAccountForbidden(
            "system-managed accounts cannot be used by this semantic command"
        )


__all__ = ["SystemManagedAccountForbidden", "require_standard_accounts"]
