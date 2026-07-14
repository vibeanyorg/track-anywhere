from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from ...domain.journal.events import (
    FinancialExternalReference,
    ReversalReasonCode,
)
from ...domain.journal.models import TransactionKind
from ...serialization.canonical_json import JSONValue, format_utc_microseconds
from ..command_bus import execute_financial
from ..idempotency import (
    CommandActor,
    CommandOutcome,
    IdempotencyValidationError,
)
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..unit_of_work import UnitOfWork
from .post_transaction import (
    Authorize,
    PostTransactionCommand,
    PostTransactionPosting,
    _build_posted_payload,
    _build_posted_pending,
    authorize_journal_write,
)
from .reverse_transaction import (
    TransactionIdAlreadyExists,
    _build_reversal_pending,
    _ensure_transaction_id_available,
    _load_reversal_source,
)


_CORRECTION_EVENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/journal.correct",
)
_CORRECTION_KINDS = frozenset(
    {
        TransactionKind.STANDARD,
        TransactionKind.OPENING,
        TransactionKind.ADJUSTMENT,
        TransactionKind.TRANSFER,
    }
)


@dataclass(frozen=True, slots=True)
class CorrectionReplacement:
    transaction_id: UUID
    expected_stream_version: int
    kind: TransactionKind
    postings: tuple[PostTransactionPosting, ...]
    effective_at: datetime
    description_ref: UUID | None = None
    external_references: tuple[FinancialExternalReference, ...] = ()

    def __post_init__(self) -> None:
        if type(self.transaction_id) is not UUID:
            raise IdempotencyValidationError(
                "replacement transaction_id must be a UUID"
            )
        if (
            type(self.expected_stream_version) is not int
            or self.expected_stream_version != 0
        ):
            raise IdempotencyValidationError(
                "replacement transaction stream must start at version zero"
            )
        if type(self.kind) is not TransactionKind or self.kind not in _CORRECTION_KINDS:
            raise IdempotencyValidationError(
                "replacement kind must use the general journal contract"
            )
        if (
            type(self.postings) is not tuple
            or len(self.postings) < 2
            or any(
                type(posting) is not PostTransactionPosting for posting in self.postings
            )
        ):
            raise IdempotencyValidationError(
                "replacement postings must be a typed immutable tuple"
            )
        try:
            format_utc_microseconds(self.effective_at)
        except (TypeError, ValueError):
            raise IdempotencyValidationError(
                "replacement effective_at must be timezone-aware"
            ) from None
        if self.description_ref is not None and type(self.description_ref) is not UUID:
            raise IdempotencyValidationError(
                "replacement description_ref must be a UUID or null"
            )
        if type(self.external_references) is not tuple or any(
            type(reference) is not FinancialExternalReference
            for reference in self.external_references
        ):
            raise IdempotencyValidationError(
                "replacement external_references must be typed and immutable"
            )

    def canonical_value(self) -> dict[str, JSONValue]:
        return {
            "description_ref": (
                None if self.description_ref is None else str(self.description_ref)
            ),
            "effective_at": format_utc_microseconds(self.effective_at),
            "expected_stream_version": self.expected_stream_version,
            "external_references": [
                reference.model_dump(mode="json")
                for reference in self.external_references
            ],
            "kind": self.kind.value,
            "postings": [posting.canonical_value() for posting in self.postings],
            "transaction_id": str(self.transaction_id),
        }


@dataclass(frozen=True, slots=True)
class CorrectTransactionCommand:
    book_id: UUID
    command_id: UUID
    reverses_transaction_id: UUID
    reversal_transaction_id: UUID
    expected_reversal_stream_version: int
    reason_code: ReversalReasonCode
    reversal_effective_at: datetime
    replacement: CorrectionReplacement
    reversal_description_ref: UUID | None = None
    operation: str = field(default="journal.correct", init=False)

    def __post_init__(self) -> None:
        for name, value in (
            ("book_id", self.book_id),
            ("command_id", self.command_id),
            ("reverses_transaction_id", self.reverses_transaction_id),
            ("reversal_transaction_id", self.reversal_transaction_id),
        ):
            if type(value) is not UUID:
                raise IdempotencyValidationError(f"{name} must be a UUID")
        if (
            type(self.expected_reversal_stream_version) is not int
            or self.expected_reversal_stream_version != 0
        ):
            raise IdempotencyValidationError(
                "reversal transaction stream must start at version zero"
            )
        if type(self.reason_code) is not ReversalReasonCode:
            raise IdempotencyValidationError("reason_code must be a ReversalReasonCode")
        try:
            format_utc_microseconds(self.reversal_effective_at)
        except (TypeError, ValueError):
            raise IdempotencyValidationError(
                "reversal_effective_at must be timezone-aware"
            ) from None
        if type(self.replacement) is not CorrectionReplacement:
            raise IdempotencyValidationError(
                "replacement must be a CorrectionReplacement"
            )
        if (
            self.reversal_description_ref is not None
            and type(self.reversal_description_ref) is not UUID
        ):
            raise IdempotencyValidationError(
                "reversal_description_ref must be a UUID or null"
            )

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "expected_reversal_stream_version": (self.expected_reversal_stream_version),
            "reason_code": self.reason_code.value,
            "replacement": self.replacement.canonical_value(),
            "reversal_description_ref": (
                None
                if self.reversal_description_ref is None
                else str(self.reversal_description_ref)
            ),
            "reversal_effective_at": format_utc_microseconds(
                self.reversal_effective_at
            ),
            "reversal_transaction_id": str(self.reversal_transaction_id),
            "reverses_transaction_id": str(self.reverses_transaction_id),
        }


UnitOfWorkFactory = Callable[[], UnitOfWork]


def execute_correct_transaction(
    command: CorrectTransactionCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
    authorize: Authorize = authorize_journal_write,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(command) is not CorrectTransactionCommand:
        raise IdempotencyValidationError("command must be a CorrectTransactionCommand")
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError("unexpected correct transaction command")
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
    command: CorrectTransactionCommand,
    uow: UnitOfWork,
    locked_head: LockedBookHead,
    *,
    actor: CommandActor,
) -> LedgerWritePlan:
    if locked_head.book_id != command.book_id:
        raise IdempotencyValidationError("locked Book does not match command")
    if command.replacement.transaction_id in {
        command.reverses_transaction_id,
        command.reversal_transaction_id,
    }:
        raise TransactionIdAlreadyExists(
            "correction transaction ids must all be distinct"
        )
    source = _load_reversal_source(
        uow,
        book_id=command.book_id,
        reverses_transaction_id=command.reverses_transaction_id,
        reversal_transaction_id=command.reversal_transaction_id,
    )
    _ensure_transaction_id_available(
        uow,
        book_id=command.book_id,
        transaction_id=command.replacement.transaction_id,
    )
    replacement_command = PostTransactionCommand(
        book_id=command.book_id,
        command_id=command.command_id,
        transaction_id=command.replacement.transaction_id,
        expected_stream_version=command.replacement.expected_stream_version,
        kind=command.replacement.kind,
        postings=command.replacement.postings,
        effective_at=command.replacement.effective_at,
        description_ref=command.replacement.description_ref,
        external_references=command.replacement.external_references,
    )
    replacement_payload = _build_posted_payload(replacement_command, uow)
    reversal_event_id = uuid5(
        _CORRECTION_EVENT_NAMESPACE,
        f"{command.command_id}:reversal",
    )
    replacement_event_id = uuid5(
        _CORRECTION_EVENT_NAMESPACE,
        f"{command.command_id}:replacement",
    )
    reversal = _build_reversal_pending(
        command_id=command.command_id,
        reversal_transaction_id=command.reversal_transaction_id,
        reason_code=command.reason_code,
        effective_at=command.reversal_effective_at,
        description_ref=command.reversal_description_ref,
        expected_source=source,
        actor=actor,
        event_id=reversal_event_id,
    )
    reversal_posting_ids = {
        posting.posting_id for posting in reversal.payload.inverse_postings
    }
    replacement_posting_ids = {
        posting.posting_id for posting in replacement_payload.postings
    }
    if reversal_posting_ids & replacement_posting_ids:
        raise IdempotencyValidationError(
            "correction replacement posting identities must be distinct"
        )
    replacement = _build_posted_pending(
        replacement_command,
        replacement_payload,
        actor=actor,
        event_id=replacement_event_id,
        causation_event_id=reversal_event_id,
    )
    return LedgerWritePlan(
        expected_stream_versions={
            ("journal_transaction", command.reversal_transaction_id): (
                command.expected_reversal_stream_version
            ),
            ("journal_transaction", command.replacement.transaction_id): (
                command.replacement.expected_stream_version
            ),
        },
        events=(reversal, replacement),
        response_schema_version=1,
        status_code=201,
        body={
            "reversal_transaction_id": str(command.reversal_transaction_id),
            "replacement_transaction_id": str(command.replacement.transaction_id),
            "reverses_transaction_id": str(command.reverses_transaction_id),
            "as_of_book_position": locked_head.last_position + 2,
        },
    )


__all__ = [
    "CorrectTransactionCommand",
    "CorrectionReplacement",
    "execute_correct_transaction",
]
