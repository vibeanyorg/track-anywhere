from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import case, func, select

from ...domain.journal import AccountType, PostingSide, TransactionKind
from ...domain.money import AssetPolicy, ScaledUnits
from ...infrastructure.db.models.catalog import AccountRecord
from ...infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
)
from ...infrastructure.db.repositories import RowLock
from ...infrastructure.db.repositories.catalogs import CatalogRepository
from ...serialization.canonical_json import JSONValue, format_utc_microseconds
from ..command_bus import execute_financial
from ..idempotency import CommandActor, CommandOutcome, IdempotencyValidationError
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..unit_of_work import UnitOfWork
from .post_transaction import (
    Authorize,
    PostTransactionCommand,
    PostTransactionPosting,
    authorize_journal_write,
    build_post_transaction_plan,
)


_BALANCE_LITERAL = re.compile(r"[0-9]+(?:\.[0-9]+)?", flags=re.ASCII)
_POSTING_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/journal.record-adjustment",
)


class AdjustmentAccountUnavailable(ValueError):
    pass


class AdjustmentBalanceMismatch(ValueError):
    pass


class AdjustmentProjectionMismatch(ValueError):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordAdjustmentCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    expected_stream_version: int
    account_id: UUID
    asset_code: str
    expected_balance: str
    actual_balance: str
    effective_at: datetime

    operation: str = field(default="journal.adjustment.record", init=False)

    def __post_init__(self) -> None:
        for name, value in (
            ("book_id", self.book_id),
            ("command_id", self.command_id),
            ("transaction_id", self.transaction_id),
            ("account_id", self.account_id),
        ):
            if type(value) is not UUID:
                raise IdempotencyValidationError(f"{name} must be a UUID")
        if type(self.expected_stream_version) is not int or self.expected_stream_version != 0:
            raise IdempotencyValidationError(
                "an adjustment transaction stream must start at version zero"
            )
        if (
            type(self.asset_code) is not str
            or not self.asset_code
            or len(self.asset_code) > 16
            or self.asset_code.upper() != self.asset_code
        ):
            raise IdempotencyValidationError("asset_code is invalid")
        for name, value in (
            ("expected_balance", self.expected_balance),
            ("actual_balance", self.actual_balance),
        ):
            if type(value) is not str or _BALANCE_LITERAL.fullmatch(value) is None:
                raise IdempotencyValidationError(
                    f"{name} must be a non-negative plain-decimal string"
                )
        try:
            format_utc_microseconds(self.effective_at)
        except (TypeError, ValueError):
            raise IdempotencyValidationError(
                "effective_at must be a timezone-aware datetime"
            ) from None

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "account_id": str(self.account_id),
            "actual_balance": self.actual_balance,
            "asset_code": self.asset_code,
            "effective_at": format_utc_microseconds(self.effective_at),
            "expected_balance": self.expected_balance,
            "expected_stream_version": self.expected_stream_version,
            "transaction_id": str(self.transaction_id),
        }


def execute_record_adjustment(
    command: RecordAdjustmentCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    uow_factory,
    authorize: Authorize = authorize_journal_write,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(command) is not RecordAdjustmentCommand:
        raise IdempotencyValidationError(
            "command must be a RecordAdjustmentCommand"
        )
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError("unexpected adjustment command")
        journal_command = _journal_command(command, uow, locked_head)
        return build_post_transaction_plan(
            journal_command,
            uow,
            locked_head,
            actor=actor,
            allow_credit_card_adjustment=True,
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
    command: RecordAdjustmentCommand,
    uow: UnitOfWork,
    locked_head: LockedBookHead,
) -> PostTransactionCommand:
    if locked_head.book_id != command.book_id:
        raise IdempotencyValidationError("locked Book does not match command")

    catalogs = CatalogRepository(uow.session)
    account = catalogs.get_account(
        command.book_id,
        command.account_id,
        lock=RowLock.SHARE,
    )
    if account.status != "active":
        raise AdjustmentAccountUnavailable("account must be active")
    is_asset = account.account_type == AccountType.ASSET.value
    is_credit_card = (
        account.account_type == AccountType.LIABILITY.value
        and account.account_subtype == "credit_card"
    )
    if not (is_asset or is_credit_card):
        raise AdjustmentAccountUnavailable(
            "balance reconciliation requires an asset or credit-card account"
        )
    if account.system_role not in {None, "standard"}:
        raise AdjustmentAccountUnavailable(
            "system-managed accounts cannot be reconciled"
        )
    if account.asset_code != command.asset_code:
        raise AdjustmentAccountUnavailable(
            "account and asset_code must use the same asset"
        )

    asset = catalogs.get_asset(command.asset_code, lock=RowLock.SHARE)
    if asset.status != "active":
        raise AdjustmentAccountUnavailable("asset is unavailable")
    policy = AssetPolicy(
        input_scale=asset.input_scale,
        ledger_scale=asset.ledger_scale,
    )
    expected_units = _parse_non_negative_balance(command.expected_balance, policy)
    actual_units = _parse_non_negative_balance(command.actual_balance, policy)
    current_accounting_units = _current_accounting_units(command, uow)
    current_units = (
        current_accounting_units if is_asset else -current_accounting_units
    )
    if current_units < 0:
        raise AdjustmentBalanceMismatch(
            "a negative natural balance requires a general journal correction"
        )
    if current_units != expected_units:
        raise AdjustmentBalanceMismatch(
            "expected balance does not match the current ledger balance"
        )
    if actual_units == current_units:
        raise AdjustmentBalanceMismatch(
            "actual balance already matches the current ledger balance"
        )

    adjustment_account = _adjustment_account(command, uow)
    difference = actual_units - current_units
    amount = ScaledUnits(
        units=abs(difference),
        scale=asset.ledger_scale,
    ).decode()
    if (difference > 0) is is_asset:
        debit_account_id = command.account_id
        credit_account_id = adjustment_account.account_id
    else:
        debit_account_id = adjustment_account.account_id
        credit_account_id = command.account_id

    return PostTransactionCommand(
        book_id=command.book_id,
        command_id=command.command_id,
        transaction_id=command.transaction_id,
        expected_stream_version=command.expected_stream_version,
        kind=TransactionKind.ADJUSTMENT,
        postings=(
            PostTransactionPosting(
                posting_id=_posting_id(command.command_id, "debit"),
                account_id=debit_account_id,
                asset_code=command.asset_code,
                side=PostingSide.DEBIT,
                amount=amount,
            ),
            PostTransactionPosting(
                posting_id=_posting_id(command.command_id, "credit"),
                account_id=credit_account_id,
                asset_code=command.asset_code,
                side=PostingSide.CREDIT,
                amount=amount,
            ),
        ),
        effective_at=command.effective_at,
    )


def _parse_non_negative_balance(raw: str, policy: AssetPolicy) -> int:
    if not any(character != "0" for character in raw if character != "."):
        return 0
    return policy.parse_online(raw).units


def _current_accounting_units(
    command: RecordAdjustmentCommand,
    uow: UnitOfWork,
) -> int:
    projected = uow.session.scalar(
        select(AccountBalanceRecord.balance_units).where(
            AccountBalanceRecord.book_id == command.book_id,
            AccountBalanceRecord.account_id == command.account_id,
            AccountBalanceRecord.asset_code == command.asset_code,
        )
    )
    signed_units = case(
        (JournalPostingRecord.side == "debit", JournalPostingRecord.units),
        else_=-JournalPostingRecord.units,
    )
    reference = uow.session.scalar(
        select(func.coalesce(func.sum(signed_units), 0))
        .select_from(JournalPostingRecord)
        .join(
            JournalTransactionRecord,
            (JournalTransactionRecord.book_id == JournalPostingRecord.book_id)
            & (
                JournalTransactionRecord.transaction_id
                == JournalPostingRecord.transaction_id
            ),
        )
        .where(
            JournalPostingRecord.book_id == command.book_id,
            JournalPostingRecord.account_id == command.account_id,
            JournalPostingRecord.asset_code == command.asset_code,
        )
    )
    projected_units = 0 if projected is None else int(projected)
    reference_units = 0 if reference is None else int(reference)
    if projected_units != reference_units:
        raise AdjustmentProjectionMismatch(
            "account balance projection does not match journal postings"
        )
    return reference_units


def _adjustment_account(
    command: RecordAdjustmentCommand,
    uow: UnitOfWork,
):
    account_ids = tuple(
        uow.session.scalars(
            select(AccountRecord.account_id).where(
                AccountRecord.book_id == command.book_id,
                AccountRecord.asset_code == command.asset_code,
                AccountRecord.account_type == AccountType.SYSTEM.value,
                AccountRecord.account_subtype == "system_adjustment",
                AccountRecord.status == "active",
            )
        )
    )
    if len(account_ids) != 1:
        raise AdjustmentAccountUnavailable(
            "exactly one active system adjustment account is required for the asset"
        )
    return CatalogRepository(uow.session).get_account(
        command.book_id,
        account_ids[0],
        lock=RowLock.SHARE,
    )


def _posting_id(command_id: UUID, side: str) -> UUID:
    return uuid5(_POSTING_NAMESPACE, f"{command_id}:{side}")


__all__ = [
    "AdjustmentAccountUnavailable",
    "AdjustmentBalanceMismatch",
    "AdjustmentProjectionMismatch",
    "RecordAdjustmentCommand",
    "execute_record_adjustment",
]
