from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, case, func, select

from ...infrastructure.db.models.catalog import AccountRecord
from ...infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
)
from ..idempotency import CommandActor
from ..ledger_committer import LedgerCommitter
from ..unit_of_work import UnitOfWork
from ._authorization import require_catalog_write


class AccountUnavailable(LookupError):
    pass


class AccountAlreadyClosed(ValueError):
    pass


class AccountBalanceNonzero(RuntimeError):
    pass


class AccountBalanceProjectionMismatch(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CloseAccount:
    book_id: UUID
    account_id: UUID

    def __post_init__(self) -> None:
        if type(self.book_id) is not UUID or type(self.account_id) is not UUID:
            raise ValueError("account identifiers must be UUIDs")


def close_account(
    command: CloseAccount,
    *,
    actor: CommandActor,
    uow_factory: Callable[[], UnitOfWork],
    ledger_committer: LedgerCommitter | None = None,
) -> dict[str, object]:
    committer = ledger_committer or LedgerCommitter()
    with uow_factory() as uow:
        require_catalog_write(uow.session, actor, command.book_id)
        locked_head = committer.execute_under_book_lock(uow.session, command.book_id)
        account = uow.session.scalar(
            select(AccountRecord)
            .where(
                AccountRecord.book_id == command.book_id,
                AccountRecord.account_id == command.account_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise AccountUnavailable("account not found in requested Book")
        if account.status == "closed":
            raise AccountAlreadyClosed("account is already closed")
        projection = uow.session.execute(
            select(AccountBalanceRecord).where(
                AccountBalanceRecord.book_id == command.book_id,
                AccountBalanceRecord.account_id == command.account_id,
                AccountBalanceRecord.asset_code == account.asset_code,
            )
        ).scalar_one_or_none()
        reference_units, latest_posting_position = uow.session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                JournalPostingRecord.side == "debit",
                                JournalPostingRecord.units,
                            ),
                            else_=-JournalPostingRecord.units,
                        )
                    ),
                    0,
                ),
                func.max(JournalTransactionRecord.source_position),
            )
            .join(
                JournalTransactionRecord,
                and_(
                    JournalTransactionRecord.book_id
                    == JournalPostingRecord.book_id,
                    JournalTransactionRecord.transaction_id
                    == JournalPostingRecord.transaction_id,
                ),
            )
            .where(
                JournalPostingRecord.book_id == command.book_id,
                JournalPostingRecord.account_id == command.account_id,
                JournalPostingRecord.asset_code == account.asset_code,
            )
        ).one()
        if latest_posting_position is not None and projection is None:
            raise AccountBalanceProjectionMismatch(
                "account balance projection is missing"
            )
        if (
            projection is not None
            and latest_posting_position is not None
            and projection.as_of_position < int(latest_posting_position)
        ):
            raise AccountBalanceProjectionMismatch(
                "account balance projection is stale"
            )
        projected = 0 if projection is None else int(projection.balance_units)
        reference = 0 if reference_units is None else int(reference_units)
        if projected != reference:
            raise AccountBalanceProjectionMismatch(
                "account balance projection does not match journal postings"
            )
        if reference != 0:
            raise AccountBalanceNonzero(
                "account must have zero balance before close"
            )
        account.status = "closed"
        uow.session.flush()
        return {
            "account_id": str(command.account_id),
            "as_of_book_position": locked_head.last_position,
            "status": "closed",
        }


__all__ = [
    "AccountAlreadyClosed",
    "AccountBalanceNonzero",
    "AccountBalanceProjectionMismatch",
    "AccountUnavailable",
    "CloseAccount",
    "close_account",
]
