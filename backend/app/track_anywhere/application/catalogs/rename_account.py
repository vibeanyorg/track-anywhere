from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from ...infrastructure.db.models.catalog import AccountMutationRecord, AccountRecord
from ..idempotency import CommandActor, IdempotencyConflict
from ..ledger_committer import LedgerCommitter
from ..unit_of_work import UnitOfWork
from ._authorization import require_catalog_write
from .close_account import AccountUnavailable


class SystemManagedAccount(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RenameAccount:
    book_id: UUID
    account_id: UUID
    current_name: str
    request_id: UUID

    def __post_init__(self) -> None:
        if any(
            type(value) is not UUID
            for value in (self.book_id, self.account_id, self.request_id)
        ):
            raise ValueError("account identifiers and request_id must be UUIDs")
        if type(self.current_name) is not str or not self.current_name.strip():
            raise ValueError("current_name must be nonblank")
        if len(self.current_name.strip()) > 512:
            raise ValueError("current_name must contain at most 512 characters")


def rename_account(
    command: RenameAccount,
    *,
    actor: CommandActor,
    uow_factory: Callable[[], UnitOfWork],
    ledger_committer: LedgerCommitter | None = None,
) -> tuple[dict[str, object], bool]:
    name = command.current_name.strip()
    payload = {
        "book_id": str(command.book_id),
        "account_id": str(command.account_id),
        "current_name": name,
        "request_id": str(command.request_id),
    }
    with uow_factory() as uow:
        require_catalog_write(uow.session, actor, command.book_id)
        (ledger_committer or LedgerCommitter()).execute_under_book_lock(
            uow.session, command.book_id
        )
        receipt = uow.session.get(
            AccountMutationRecord, (command.book_id, command.request_id)
        )
        if receipt is not None:
            if (
                receipt.command != payload
                or receipt.actor_subject_id != actor.subject_id
            ):
                raise IdempotencyConflict()
            return receipt.after, True
        account = uow.session.scalar(
            select(AccountRecord)
            .where(
                AccountRecord.book_id == command.book_id,
                AccountRecord.account_id == command.account_id,
            )
            .with_for_update()
        )
        if account is None:
            raise AccountUnavailable("account not found in requested Book")
        if account.system_role is not None or account.account_type == "system":
            raise SystemManagedAccount("system-managed accounts cannot be renamed")
        before = _snapshot(account)
        account.current_name = name
        account.version += 1
        account.updated_at = datetime.now(timezone.utc)
        uow.session.flush()
        after = _snapshot(account)
        uow.session.add(
            AccountMutationRecord(
                book_id=command.book_id,
                request_id=command.request_id,
                account_id=command.account_id,
                actor_subject_id=actor.subject_id,
                command=payload,
                before=before,
                after=after,
            )
        )
    with uow_factory() as uow:
        receipt = uow.session.get(
            AccountMutationRecord, (command.book_id, command.request_id)
        )
        if receipt is None or receipt.command != payload:
            raise RuntimeError(
                "account rename verification pending; retry the exact same request_id"
            )
        account = uow.session.get(
            AccountRecord, (command.book_id, command.account_id)
        )
        if account is None or _snapshot(account) != receipt.after:
            raise RuntimeError(
                "account rename verification pending; retry the exact same request_id"
            )
        return receipt.after, False


def _snapshot(account: AccountRecord) -> dict[str, object]:
    return {
        "account_id": str(account.account_id),
        "asset_code": account.asset_code,
        "account_type": account.account_type,
        "account_subtype": account.account_subtype,
        "system_role": account.system_role,
        "current_name": account.current_name,
        "status": account.status,
        "version": account.version,
    }


__all__ = ["RenameAccount", "SystemManagedAccount", "rename_account"]
