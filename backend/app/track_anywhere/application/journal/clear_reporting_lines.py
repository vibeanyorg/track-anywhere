from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from ...domain.reporting.events import ReportingLinesCleared
from ...serialization.canonical_json import JSONValue, format_utc_microseconds
from ..command_bus import execute_financial
from ..event_batch import PendingEvent
from ..idempotency import CommandActor, CommandOutcome, IdempotencyValidationError
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..unit_of_work import UnitOfWork
from .assign_reporting_lines import _load_reporting_target
from .post_transaction import Authorize, authorize_journal_write


_CLEAR_EVENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/reporting.clear",
)


@dataclass(frozen=True, slots=True)
class ClearReportingLinesCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    expected_revision: int
    effective_at: datetime
    operation: str = field(default="reporting.clear", init=False)

    def __post_init__(self) -> None:
        for name, value in (
            ("book_id", self.book_id),
            ("command_id", self.command_id),
            ("transaction_id", self.transaction_id),
        ):
            if type(value) is not UUID:
                raise IdempotencyValidationError(f"{name} must be a UUID")
        if type(self.expected_revision) is not int or self.expected_revision < 0:
            raise IdempotencyValidationError(
                "expected_revision must be a non-negative integer"
            )
        try:
            format_utc_microseconds(self.effective_at)
        except (TypeError, ValueError):
            raise IdempotencyValidationError(
                "effective_at must be a timezone-aware datetime"
            ) from None

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "effective_at": format_utc_microseconds(self.effective_at),
            "expected_revision": self.expected_revision,
            "transaction_id": str(self.transaction_id),
        }


UnitOfWorkFactory = Callable[[], UnitOfWork]


def execute_clear_reporting_lines(
    command: ClearReportingLinesCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
    authorize: Authorize = authorize_journal_write,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(command) is not ClearReportingLinesCommand:
        raise IdempotencyValidationError("command must be a ClearReportingLinesCommand")
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError("unexpected reporting clear command")
        if locked_head.book_id != command.book_id:
            raise IdempotencyValidationError("locked Book does not match command")
        transaction = _load_reporting_target(
            uow,
            book_id=command.book_id,
            transaction_id=command.transaction_id,
        )
        revision = command.expected_revision + 1
        pending = PendingEvent(
            event_id=uuid5(_CLEAR_EVENT_NAMESPACE, str(command.command_id)),
            stream_type="reporting_lines",
            stream_id=command.transaction_id,
            payload=ReportingLinesCleared(
                transaction_id=command.transaction_id,
                classification_revision=revision,
            ),
            command_id=command.command_id,
            actor_subject_id=actor.subject_id,
            correlation_id=command.command_id,
            causation_event_id=transaction.source_event_id,
            effective_at=command.effective_at,
        )
        return LedgerWritePlan(
            expected_stream_versions={
                ("reporting_lines", command.transaction_id): command.expected_revision
            },
            events=(pending,),
            response_schema_version=1,
            status_code=201,
            body={
                "transaction_id": str(command.transaction_id),
                "classification_revision": revision,
                "as_of_book_position": locked_head.last_position + 1,
            },
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


__all__ = [
    "ClearReportingLinesCommand",
    "execute_clear_reporting_lines",
]
