from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from ...domain.journal import AccountType, PostingSide, TransactionKind
from ...infrastructure.db.repositories import RowLock
from ...infrastructure.db.repositories.catalogs import CatalogRepository
from ...serialization.canonical_json import JSONValue, format_utc_microseconds
from ..command_bus import execute_financial
from ..idempotency import CommandActor, CommandOutcome, IdempotencyValidationError
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..unit_of_work import UnitOfWork
from .account_roles import require_standard_accounts
from .post_transaction import (
    Authorize,
    PostTransactionCommand,
    PostTransactionPosting,
    authorize_journal_write,
    build_post_transaction_plan,
)


_AMOUNT_LITERAL = re.compile(r"[0-9]+(?:\.[0-9]+)?", flags=re.ASCII)
_POSTING_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/journal.record-simple",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SimpleTransactionCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    expected_stream_version: int
    asset_code: str
    amount: str
    effective_at: datetime

    operation: str = field(init=False)

    def __post_init__(self) -> None:
        for name, value in (
            ("book_id", self.book_id),
            ("command_id", self.command_id),
            ("transaction_id", self.transaction_id),
        ):
            if type(value) is not UUID:
                raise IdempotencyValidationError(f"{name} must be a UUID")
        if type(self.expected_stream_version) is not int or self.expected_stream_version != 0:
            raise IdempotencyValidationError(
                "a simple transaction stream must start at version zero"
            )
        if (
            type(self.asset_code) is not str
            or not self.asset_code
            or len(self.asset_code) > 16
            or self.asset_code.upper() != self.asset_code
        ):
            raise IdempotencyValidationError("asset_code is invalid")
        if (
            type(self.amount) is not str
            or _AMOUNT_LITERAL.fullmatch(self.amount) is None
            or self.amount.rstrip("0").rstrip(".") in {"", "0"}
        ):
            raise IdempotencyValidationError(
                "amount must be a positive unsigned plain-decimal string"
            )
        try:
            format_utc_microseconds(self.effective_at)
        except (TypeError, ValueError):
            raise IdempotencyValidationError(
                "effective_at must be a timezone-aware datetime"
            ) from None

    def _base_payload(self) -> dict[str, JSONValue]:
        return {
            "amount": self.amount,
            "asset_code": self.asset_code,
            "effective_at": format_utc_microseconds(self.effective_at),
            "expected_stream_version": self.expected_stream_version,
            "transaction_id": str(self.transaction_id),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordExpenseCommand(SimpleTransactionCommand):
    source_account_id: UUID
    expense_account_id: UUID

    operation: str = field(default="journal.expense.record", init=False)

    def __post_init__(self) -> None:
        SimpleTransactionCommand.__post_init__(self)
        _require_account_ids(self.source_account_id, self.expense_account_id)

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            **self._base_payload(),
            "expense_account_id": str(self.expense_account_id),
            "source_account_id": str(self.source_account_id),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordTransferCommand(SimpleTransactionCommand):
    source_account_id: UUID
    target_account_id: UUID

    operation: str = field(default="journal.transfer.record", init=False)

    def __post_init__(self) -> None:
        SimpleTransactionCommand.__post_init__(self)
        _require_account_ids(self.source_account_id, self.target_account_id)

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            **self._base_payload(),
            "source_account_id": str(self.source_account_id),
            "target_account_id": str(self.target_account_id),
        }


def execute_record_expense(
    command: RecordExpenseCommand,
    **kwargs,
) -> CommandOutcome:
    if type(command) is not RecordExpenseCommand:
        raise IdempotencyValidationError("command must be a RecordExpenseCommand")
    return _execute_simple(command, **kwargs)


def execute_record_transfer(
    command: RecordTransferCommand,
    **kwargs,
) -> CommandOutcome:
    if type(command) is not RecordTransferCommand:
        raise IdempotencyValidationError("command must be a RecordTransferCommand")
    return _execute_simple(command, **kwargs)


def _execute_simple(
    command: SimpleTransactionCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    uow_factory,
    authorize: Authorize = authorize_journal_write,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError("unexpected simple transaction command")
        journal_command = _journal_command(command, uow)
        return build_post_transaction_plan(
            journal_command,
            uow,
            locked_head,
            actor=actor,
        )

    return execute_financial(
        command,
        raw_key=raw_key,
        actor=actor,
        authorize=authorize,
        handler=handler,
        uow_factory=uow_factory,
        ledger_committer=committer,
        max_attempts=max_attempts,
    )


def _journal_command(
    command: SimpleTransactionCommand,
    uow: UnitOfWork,
) -> PostTransactionCommand:
    catalogs = CatalogRepository(uow.session)
    if type(command) is RecordExpenseCommand:
        source = catalogs.get_account(
            command.book_id,
            command.source_account_id,
            lock=RowLock.SHARE,
        )
        target = catalogs.get_account(
            command.book_id,
            command.expense_account_id,
            lock=RowLock.SHARE,
        )
        require_standard_accounts(source, target)
        if source.account_type != AccountType.ASSET.value:
            raise ValueError("source account must be an asset account")
        if target.account_type != AccountType.EXPENSE.value:
            raise ValueError("expense account must be an expense account")
        kind = TransactionKind.STANDARD
        debit_account_id = command.expense_account_id
        credit_account_id = command.source_account_id
    elif type(command) is RecordTransferCommand:
        source = catalogs.get_account(
            command.book_id,
            command.source_account_id,
            lock=RowLock.SHARE,
        )
        target = catalogs.get_account(
            command.book_id,
            command.target_account_id,
            lock=RowLock.SHARE,
        )
        require_standard_accounts(source, target)
        if source.account_type != AccountType.ASSET.value:
            raise ValueError("source account must be an asset account")
        if target.account_type != AccountType.ASSET.value:
            raise ValueError("target account must be an asset account")
        kind = TransactionKind.TRANSFER
        debit_account_id = command.target_account_id
        credit_account_id = command.source_account_id
    else:  # pragma: no cover - callers enforce the closed command union.
        raise IdempotencyValidationError("unsupported simple transaction command")

    if source.account_id == target.account_id:
        raise ValueError("source and target accounts must be different")
    if source.asset_code != command.asset_code or target.asset_code != command.asset_code:
        raise ValueError("accounts and asset_code must use the same asset")

    return PostTransactionCommand(
        book_id=command.book_id,
        command_id=command.command_id,
        transaction_id=command.transaction_id,
        expected_stream_version=command.expected_stream_version,
        kind=kind,
        postings=(
            PostTransactionPosting(
                posting_id=_posting_id(command.command_id, "debit"),
                account_id=debit_account_id,
                asset_code=command.asset_code,
                side=PostingSide.DEBIT,
                amount=command.amount,
            ),
            PostTransactionPosting(
                posting_id=_posting_id(command.command_id, "credit"),
                account_id=credit_account_id,
                asset_code=command.asset_code,
                side=PostingSide.CREDIT,
                amount=command.amount,
            ),
        ),
        effective_at=command.effective_at,
    )


def _require_account_ids(first: UUID, second: UUID) -> None:
    if type(first) is not UUID or type(second) is not UUID:
        raise IdempotencyValidationError("account IDs must be UUIDs")
    if first == second:
        raise IdempotencyValidationError("source and target accounts must be different")


def _posting_id(command_id: UUID, side: str) -> UUID:
    return uuid5(_POSTING_NAMESPACE, f"{command_id}:{side}")


__all__ = [
    "RecordExpenseCommand",
    "RecordTransferCommand",
    "execute_record_expense",
    "execute_record_transfer",
]
