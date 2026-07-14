from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import select

from ...domain.investments.allocation import (
    AllocationMethod,
    InvestmentEvent,
    SpecificLotRequest,
    reduce_investment_events,
    select_lot_allocations,
)
from ...domain.investments.events import (
    InvestmentLotAcquired,
    InvestmentLotDisposed,
)
from ...infrastructure.db.models.event_store import LedgerEventRecord
from ...serialization.canonical_json import JSONValue, format_utc_microseconds
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from ..command_bus import execute_financial
from ..event_batch import PendingEvent
from ..idempotency import CommandActor, CommandOutcome, IdempotencyValidationError
from ..journal.post_transaction import Authorize, authorize_journal_write
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..unit_of_work import UnitOfWork
from .acquire_lot import (
    _load_linked_transaction,
    _validate_asset_pair,
    _validate_assets,
    _validate_units,
)


_DISPOSE_EVENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/investment-lot.dispose",
)


@dataclass(frozen=True, slots=True)
class DisposeLotCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    instrument_asset_code: str
    settlement_asset_code: str
    quantity_units: str
    proceeds_units: str
    allocation_method: AllocationMethod
    effective_at: datetime
    fee_units: str | None = None
    specific_lots: tuple[SpecificLotRequest, ...] = ()
    expected_stream_version: int = 0
    operation: str = field(default="investments.lot.dispose", init=False)

    def __post_init__(self) -> None:
        for name, value in (
            ("book_id", self.book_id),
            ("command_id", self.command_id),
            ("transaction_id", self.transaction_id),
        ):
            if type(value) is not UUID:
                raise IdempotencyValidationError(f"{name} must be a UUID")
        _validate_asset_pair(
            self.instrument_asset_code,
            self.settlement_asset_code,
        )
        _validate_units("quantity_units", self.quantity_units)
        _validate_units("proceeds_units", self.proceeds_units)
        if self.fee_units is not None:
            _validate_units("fee_units", self.fee_units)
        if type(self.allocation_method) is not AllocationMethod:
            raise IdempotencyValidationError("allocation_method is invalid")
        if type(self.specific_lots) is not tuple or any(
            type(request) is not SpecificLotRequest for request in self.specific_lots
        ):
            raise IdempotencyValidationError(
                "specific_lots must be an immutable typed tuple"
            )
        if (self.allocation_method is AllocationMethod.FIFO and self.specific_lots) or (
            self.allocation_method is AllocationMethod.SPECIFIC_ID
            and not self.specific_lots
        ):
            raise IdempotencyValidationError(
                "specific lot requests must match the allocation method"
            )
        if (
            type(self.expected_stream_version) is not int
            or self.expected_stream_version != 0
        ):
            raise IdempotencyValidationError(
                "a disposal transaction stream must start at version zero"
            )
        try:
            format_utc_microseconds(self.effective_at)
        except (TypeError, ValueError):
            raise IdempotencyValidationError(
                "effective_at must be a timezone-aware datetime"
            ) from None

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "allocation_method": self.allocation_method.value,
            "effective_at": format_utc_microseconds(self.effective_at),
            "expected_stream_version": self.expected_stream_version,
            "fee_units": self.fee_units,
            "instrument_asset_code": self.instrument_asset_code,
            "proceeds_units": self.proceeds_units,
            "quantity_units": self.quantity_units,
            "settlement_asset_code": self.settlement_asset_code,
            "specific_lots": [
                {
                    "lot_id": str(request.lot_id),
                    "quantity_units": request.quantity_units,
                }
                for request in self.specific_lots
            ],
            "transaction_id": str(self.transaction_id),
        }


UnitOfWorkFactory = Callable[[], UnitOfWork]


def execute_dispose_lot(
    command: DisposeLotCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
    authorize: Authorize = authorize_journal_write,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(command) is not DisposeLotCommand:
        raise IdempotencyValidationError("command must be a DisposeLotCommand")
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError("unexpected dispose lot command")
        return _build_dispose_plan(command, uow, locked_head, actor=actor)

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


def _build_dispose_plan(
    command: DisposeLotCommand,
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
    state = reduce_investment_events(_load_investment_events(uow, command.book_id))
    allocations = select_lot_allocations(
        state,
        instrument_asset_code=command.instrument_asset_code,
        settlement_asset_code=command.settlement_asset_code,
        quantity_units=int(command.quantity_units),
        method=command.allocation_method,
        command_id=command.command_id,
        specific_lots=command.specific_lots,
    )
    cost_basis_units = sum(int(item.cost_units) for item in allocations)
    pending = PendingEvent(
        event_id=uuid5(_DISPOSE_EVENT_NAMESPACE, str(command.command_id)),
        stream_type="investment_disposal",
        stream_id=command.transaction_id,
        payload=InvestmentLotDisposed(
            transaction_id=command.transaction_id,
            instrument_asset_code=command.instrument_asset_code,
            settlement_asset_code=command.settlement_asset_code,
            quantity_units=command.quantity_units,
            proceeds_units=command.proceeds_units,
            cost_basis_units=str(cost_basis_units),
            fee_units=command.fee_units,
            allocations=allocations,
        ),
        command_id=command.command_id,
        actor_subject_id=actor.subject_id,
        correlation_id=command.command_id,
        causation_event_id=transaction.source_event_id,
        effective_at=command.effective_at,
    )
    return LedgerWritePlan(
        expected_stream_versions={
            ("investment_disposal", command.transaction_id): (
                command.expected_stream_version
            )
        },
        events=(pending,),
        response_schema_version=1,
        status_code=201,
        body={
            "transaction_id": str(command.transaction_id),
            "quantity_units": command.quantity_units,
            "cost_basis_units": str(cost_basis_units),
            "as_of_book_position": locked_head.last_position + 1,
        },
    )


def _load_investment_events(
    uow: UnitOfWork,
    book_id: UUID,
) -> tuple[InvestmentEvent, ...]:
    stored_events = tuple(
        uow.session.scalars(
            select(LedgerEventRecord)
            .where(
                LedgerEventRecord.book_id == book_id,
                LedgerEventRecord.event_type.in_(
                    ("InvestmentLotAcquired", "InvestmentLotDisposed")
                ),
            )
            .order_by(LedgerEventRecord.book_position)
        )
    )
    events: list[InvestmentEvent] = []
    for stored in stored_events:
        payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
            stored.event_type,
            stored.event_schema_version,
            stored.payload,
        )
        if type(payload) not in (InvestmentLotAcquired, InvestmentLotDisposed):
            raise RuntimeError("stored investment event has an invalid contract")
        events.append(
            InvestmentEvent(
                source_position=stored.book_position,
                effective_at=stored.effective_at,
                payload=payload,
            )
        )
    return tuple(events)


__all__ = [
    "DisposeLotCommand",
    "execute_dispose_lot",
]
