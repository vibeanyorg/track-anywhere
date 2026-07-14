from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import select

from ...domain.investments.events import InvestmentLotAcquired
from ...infrastructure.db.models.projections import JournalTransactionRecord
from ...infrastructure.db.repositories.catalogs import CatalogRepository
from ...serialization.canonical_json import JSONValue, format_utc_microseconds
from ..command_bus import execute_financial
from ..event_batch import PendingEvent
from ..idempotency import CommandActor, CommandOutcome, IdempotencyValidationError
from ..journal.post_transaction import Authorize, authorize_journal_write
from ..journal.reverse_transaction import TransactionNotFound
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..unit_of_work import UnitOfWork


_CANONICAL_UNITS = re.compile(r"[1-9][0-9]{0,37}", flags=re.ASCII)
_ACQUIRE_EVENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/investment-lot.acquire",
)


@dataclass(frozen=True, slots=True)
class AcquireLotCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    lot_id: UUID
    instrument_asset_code: str
    settlement_asset_code: str
    quantity_units: str
    cost_units: str
    effective_at: datetime
    fee_units: str | None = None
    expected_stream_version: int = 0
    operation: str = field(default="investments.lot.acquire", init=False)

    def __post_init__(self) -> None:
        for name, value in (
            ("book_id", self.book_id),
            ("command_id", self.command_id),
            ("transaction_id", self.transaction_id),
            ("lot_id", self.lot_id),
        ):
            if type(value) is not UUID:
                raise IdempotencyValidationError(f"{name} must be a UUID")
        _validate_asset_pair(
            self.instrument_asset_code,
            self.settlement_asset_code,
        )
        for name, value in (
            ("quantity_units", self.quantity_units),
            ("cost_units", self.cost_units),
        ):
            _validate_units(name, value)
        if self.fee_units is not None:
            _validate_units("fee_units", self.fee_units)
        if (
            type(self.expected_stream_version) is not int
            or self.expected_stream_version != 0
        ):
            raise IdempotencyValidationError(
                "a new investment lot stream must start at version zero"
            )
        try:
            format_utc_microseconds(self.effective_at)
        except (TypeError, ValueError):
            raise IdempotencyValidationError(
                "effective_at must be a timezone-aware datetime"
            ) from None

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "cost_units": self.cost_units,
            "effective_at": format_utc_microseconds(self.effective_at),
            "expected_stream_version": self.expected_stream_version,
            "fee_units": self.fee_units,
            "instrument_asset_code": self.instrument_asset_code,
            "lot_id": str(self.lot_id),
            "quantity_units": self.quantity_units,
            "settlement_asset_code": self.settlement_asset_code,
            "transaction_id": str(self.transaction_id),
        }


UnitOfWorkFactory = Callable[[], UnitOfWork]


def execute_acquire_lot(
    command: AcquireLotCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
    authorize: Authorize = authorize_journal_write,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(command) is not AcquireLotCommand:
        raise IdempotencyValidationError("command must be an AcquireLotCommand")
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError("unexpected acquire lot command")
        return _build_acquire_plan(command, uow, locked_head, actor=actor)

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


def _build_acquire_plan(
    command: AcquireLotCommand,
    uow: UnitOfWork,
    locked_head: LockedBookHead,
    *,
    actor: CommandActor,
) -> LedgerWritePlan:
    if locked_head.book_id != command.book_id:
        raise IdempotencyValidationError("locked Book does not match command")
    transaction = _load_linked_transaction(
        uow,
        book_id=command.book_id,
        transaction_id=command.transaction_id,
    )
    _validate_assets(
        uow,
        command.instrument_asset_code,
        command.settlement_asset_code,
    )
    pending = PendingEvent(
        event_id=uuid5(_ACQUIRE_EVENT_NAMESPACE, str(command.command_id)),
        stream_type="investment_lot",
        stream_id=command.lot_id,
        payload=InvestmentLotAcquired(
            transaction_id=command.transaction_id,
            lot_id=command.lot_id,
            instrument_asset_code=command.instrument_asset_code,
            settlement_asset_code=command.settlement_asset_code,
            quantity_units=command.quantity_units,
            cost_units=command.cost_units,
            fee_units=command.fee_units,
        ),
        command_id=command.command_id,
        actor_subject_id=actor.subject_id,
        correlation_id=command.command_id,
        causation_event_id=transaction.source_event_id,
        effective_at=command.effective_at,
    )
    return LedgerWritePlan(
        expected_stream_versions={
            ("investment_lot", command.lot_id): command.expected_stream_version
        },
        events=(pending,),
        response_schema_version=1,
        status_code=201,
        body={
            "transaction_id": str(command.transaction_id),
            "lot_id": str(command.lot_id),
            "as_of_book_position": locked_head.last_position + 1,
        },
    )


def _load_linked_transaction(
    uow: UnitOfWork,
    *,
    book_id: UUID,
    transaction_id: UUID,
) -> JournalTransactionRecord:
    transaction = uow.session.execute(
        select(JournalTransactionRecord)
        .where(
            JournalTransactionRecord.book_id == book_id,
            JournalTransactionRecord.transaction_id == transaction_id,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if transaction is None:
        raise TransactionNotFound("linked journal transaction not found in Book")
    return transaction


def _validate_assets(
    uow: UnitOfWork,
    instrument_asset_code: str,
    settlement_asset_code: str,
) -> None:
    catalogs = CatalogRepository(uow.session)
    for asset_code in (instrument_asset_code, settlement_asset_code):
        asset = catalogs.get_asset(asset_code)
        if asset.status != "active":
            raise ValueError(f"investment asset is unavailable: {asset_code}")


def _validate_asset_pair(instrument: str, settlement: str) -> None:
    for name, value in (
        ("instrument_asset_code", instrument),
        ("settlement_asset_code", settlement),
    ):
        if (
            type(value) is not str
            or not value
            or len(value) > 16
            or value.upper() != value
        ):
            raise IdempotencyValidationError(f"{name} is invalid")
    if instrument == settlement:
        raise IdempotencyValidationError("instrument and settlement assets must differ")


def _validate_units(name: str, value: object) -> None:
    if type(value) is not str or _CANONICAL_UNITS.fullmatch(value) is None:
        raise IdempotencyValidationError(
            f"{name} must be a canonical positive integer string"
        )


__all__ = [
    "AcquireLotCommand",
    "execute_acquire_lot",
]
