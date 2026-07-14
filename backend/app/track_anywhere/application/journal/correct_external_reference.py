from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import select

from ...domain.journal.events import (
    ExternalReferenceKind,
    FinancialExternalReferenceCorrected,
)
from ...infrastructure.db.models.projections import (
    TransactionExternalReferenceRecord,
)
from ...serialization.canonical_json import JSONValue, format_utc_microseconds
from ..command_bus import execute_financial
from ..event_batch import PendingEvent
from ..idempotency import CommandActor, CommandOutcome, IdempotencyValidationError
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..unit_of_work import UnitOfWork
from .assign_reporting_lines import _load_reporting_target
from .post_transaction import Authorize, authorize_journal_write


_PROVIDER_CODE = re.compile(r"[a-z][a-z0-9_-]{0,31}", flags=re.ASCII)
_REFERENCE_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", flags=re.ASCII)
_CORRECT_REFERENCE_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/external-reference.correct",
)


class ExternalReferenceUnchanged(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CorrectExternalReferenceCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    provider_code: str
    reference_kind: ExternalReferenceKind
    corrected_reference: str
    expected_stream_version: int
    effective_at: datetime
    operation: str = field(default="journal.external_reference.correct", init=False)

    def __post_init__(self) -> None:
        for name, value in (
            ("book_id", self.book_id),
            ("command_id", self.command_id),
            ("transaction_id", self.transaction_id),
        ):
            if type(value) is not UUID:
                raise IdempotencyValidationError(f"{name} must be a UUID")
        if (
            type(self.provider_code) is not str
            or _PROVIDER_CODE.fullmatch(self.provider_code) is None
        ):
            raise IdempotencyValidationError("provider_code is invalid")
        if type(self.reference_kind) is not ExternalReferenceKind:
            raise IdempotencyValidationError("reference_kind is invalid")
        if (
            type(self.corrected_reference) is not str
            or _REFERENCE_VALUE.fullmatch(self.corrected_reference) is None
        ):
            raise IdempotencyValidationError("corrected_reference is invalid")
        if (
            type(self.expected_stream_version) is not int
            or self.expected_stream_version < 0
        ):
            raise IdempotencyValidationError(
                "expected_stream_version must be a non-negative integer"
            )
        try:
            format_utc_microseconds(self.effective_at)
        except (TypeError, ValueError):
            raise IdempotencyValidationError(
                "effective_at must be a timezone-aware datetime"
            ) from None

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "corrected_reference": self.corrected_reference,
            "effective_at": format_utc_microseconds(self.effective_at),
            "expected_stream_version": self.expected_stream_version,
            "provider_code": self.provider_code,
            "reference_kind": self.reference_kind.value,
            "transaction_id": str(self.transaction_id),
        }


UnitOfWorkFactory = Callable[[], UnitOfWork]


def execute_correct_external_reference(
    command: CorrectExternalReferenceCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
    authorize: Authorize = authorize_journal_write,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(command) is not CorrectExternalReferenceCommand:
        raise IdempotencyValidationError(
            "command must be a CorrectExternalReferenceCommand"
        )
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError(
                "unexpected external-reference correction command"
            )
        return _build_correction_plan(command, uow, locked_head, actor=actor)

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


def _build_correction_plan(
    command: CorrectExternalReferenceCommand,
    uow: UnitOfWork,
    locked_head: LockedBookHead,
    *,
    actor: CommandActor,
) -> LedgerWritePlan:
    if locked_head.book_id != command.book_id:
        raise IdempotencyValidationError("locked Book does not match command")
    transaction = _load_reporting_target(
        uow,
        book_id=command.book_id,
        transaction_id=command.transaction_id,
    )
    current = uow.session.execute(
        select(TransactionExternalReferenceRecord)
        .where(
            TransactionExternalReferenceRecord.book_id == command.book_id,
            TransactionExternalReferenceRecord.transaction_id == command.transaction_id,
            TransactionExternalReferenceRecord.provider_code == command.provider_code,
            TransactionExternalReferenceRecord.reference_kind
            == command.reference_kind.value,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    previous_reference = None if current is None else current.reference_value
    if previous_reference == command.corrected_reference:
        raise ExternalReferenceUnchanged("external reference is already current")
    payload = FinancialExternalReferenceCorrected(
        transaction_id=command.transaction_id,
        provider_code=command.provider_code,
        reference_kind=command.reference_kind,
        previous_reference=previous_reference,
        corrected_reference=command.corrected_reference,
    )
    pending = PendingEvent(
        event_id=uuid5(_CORRECT_REFERENCE_NAMESPACE, str(command.command_id)),
        stream_type="external_reference",
        stream_id=command.transaction_id,
        payload=payload,
        command_id=command.command_id,
        actor_subject_id=actor.subject_id,
        correlation_id=command.command_id,
        causation_event_id=(
            transaction.source_event_id if current is None else current.source_event_id
        ),
        effective_at=command.effective_at,
    )
    return LedgerWritePlan(
        expected_stream_versions={
            ("external_reference", command.transaction_id): (
                command.expected_stream_version
            )
        },
        events=(pending,),
        response_schema_version=1,
        status_code=201,
        body={
            "transaction_id": str(command.transaction_id),
            "provider_code": command.provider_code,
            "reference_kind": command.reference_kind.value,
            "reference": command.corrected_reference,
            "as_of_book_position": locked_head.last_position + 1,
        },
    )


__all__ = [
    "CorrectExternalReferenceCommand",
    "ExternalReferenceUnchanged",
    "execute_correct_external_reference",
]
