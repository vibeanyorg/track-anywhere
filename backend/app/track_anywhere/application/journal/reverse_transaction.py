from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import and_, select

from ...domain.credit_cards.events import (
    CreditCardIntent,
    CreditCardTransactionRecorded,
)
from ...domain.journal.events import (
    JournalPostingFact,
    JournalTransactionPosted,
    JournalTransactionReversed,
    ReversalReasonCode,
)
from ...domain.journal.models import PostingSide
from ...infrastructure.db.models.event_store import (
    EventStreamHeadRecord,
    LedgerEventRecord,
)
from ...infrastructure.db.models.credit_cards import CreditCardTransactionRecord
from ...infrastructure.db.models.catalog import AccountRecord
from ...infrastructure.db.models.projections import (
    JournalTransactionRecord,
    TransactionReversalRecord,
)
from ...serialization.canonical_json import JSONValue, format_utc_microseconds
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from ..command_bus import execute_financial
from ..event_batch import PendingEvent
from ..idempotency import (
    CommandActor,
    CommandOutcome,
    IdempotencyValidationError,
)
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..unit_of_work import UnitOfWork
from .post_transaction import Authorize, authorize_journal_write


_REVERSE_EVENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/journal.reverse",
)


class TransactionNotFound(LookupError):
    pass


class TransactionAlreadyReversed(RuntimeError):
    pass


class TransactionIdAlreadyExists(RuntimeError):
    pass


class InvalidTransactionSource(RuntimeError):
    pass


class CreditCardChargeHasActiveRefunds(RuntimeError):
    pass


class CreditCardReversalChainForbidden(RuntimeError):
    pass


class CreditCardReversalPrecedesOriginal(RuntimeError):
    pass


class CreditCardReversalRequiresActiveAccount(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReverseTransactionCommand:
    book_id: UUID
    command_id: UUID
    reversal_transaction_id: UUID
    reverses_transaction_id: UUID
    expected_stream_version: int
    reason_code: ReversalReasonCode
    effective_at: datetime
    description_ref: UUID | None = None
    operation: str = field(default="journal.reverse", init=False)

    def __post_init__(self) -> None:
        for name, value in (
            ("book_id", self.book_id),
            ("command_id", self.command_id),
            ("reversal_transaction_id", self.reversal_transaction_id),
            ("reverses_transaction_id", self.reverses_transaction_id),
        ):
            if type(value) is not UUID:
                raise IdempotencyValidationError(f"{name} must be a UUID")
        if (
            type(self.expected_stream_version) is not int
            or self.expected_stream_version != 0
        ):
            raise IdempotencyValidationError(
                "a reversal transaction stream must start at version zero"
            )
        if type(self.reason_code) is not ReversalReasonCode:
            raise IdempotencyValidationError("reason_code must be a ReversalReasonCode")
        try:
            format_utc_microseconds(self.effective_at)
        except (TypeError, ValueError):
            raise IdempotencyValidationError(
                "effective_at must be a timezone-aware datetime"
            ) from None
        if self.description_ref is not None and type(self.description_ref) is not UUID:
            raise IdempotencyValidationError("description_ref must be a UUID or null")

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "description_ref": (
                None if self.description_ref is None else str(self.description_ref)
            ),
            "effective_at": format_utc_microseconds(self.effective_at),
            "expected_stream_version": self.expected_stream_version,
            "reason_code": self.reason_code.value,
            "reversal_transaction_id": str(self.reversal_transaction_id),
            "reverses_transaction_id": str(self.reverses_transaction_id),
        }


@dataclass(frozen=True, slots=True)
class _ReversalSource:
    transaction: JournalTransactionRecord
    event: LedgerEventRecord
    postings: tuple[JournalPostingFact, ...]


UnitOfWorkFactory = Callable[[], UnitOfWork]


def execute_reverse_transaction(
    command: ReverseTransactionCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
    authorize: Authorize = authorize_journal_write,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(command) is not ReverseTransactionCommand:
        raise IdempotencyValidationError("command must be a ReverseTransactionCommand")
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError("unexpected reverse transaction command")
        return _build_reverse_plan(command, uow, locked_head, actor=actor)

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


def _build_reverse_plan(
    command: ReverseTransactionCommand,
    uow: UnitOfWork,
    locked_head: LockedBookHead,
    *,
    actor: CommandActor,
) -> LedgerWritePlan:
    if locked_head.book_id != command.book_id:
        raise IdempotencyValidationError("locked Book does not match command")
    source = _load_reversal_source(
        uow,
        book_id=command.book_id,
        reverses_transaction_id=command.reverses_transaction_id,
        reversal_transaction_id=command.reversal_transaction_id,
    )
    touches_credit_card = _source_touches_credit_card_account(
        uow,
        book_id=command.book_id,
        postings=source.postings,
    )
    if touches_credit_card and _closed_credit_card_account_id(
        uow,
        book_id=command.book_id,
        postings=source.postings,
    ) is not None:
        raise CreditCardReversalRequiresActiveAccount(
            "reopen the credit-card account before reversing its transaction"
        )
    if touches_credit_card and command.effective_at < source.event.effective_at:
        raise CreditCardReversalPrecedesOriginal(
            "credit-card reversal cannot precede its source transaction"
        )
    pending = _build_reversal_pending(
        command_id=command.command_id,
        reversal_transaction_id=command.reversal_transaction_id,
        reason_code=command.reason_code,
        effective_at=command.effective_at,
        description_ref=command.description_ref,
        expected_source=source,
        actor=actor,
    )
    return LedgerWritePlan(
        expected_stream_versions={
            ("journal_transaction", command.reversal_transaction_id): (
                command.expected_stream_version
            )
        },
        events=(pending,),
        response_schema_version=1,
        status_code=201,
        body={
            "reversal_transaction_id": str(command.reversal_transaction_id),
            "reverses_transaction_id": str(command.reverses_transaction_id),
            "as_of_book_position": locked_head.last_position + 1,
        },
    )


def _ensure_transaction_id_available(
    uow: UnitOfWork,
    *,
    book_id: UUID,
    transaction_id: UUID,
) -> None:
    existing_projection = uow.session.execute(
        select(JournalTransactionRecord.transaction_id).where(
            JournalTransactionRecord.book_id == book_id,
            JournalTransactionRecord.transaction_id == transaction_id,
        )
    ).scalar_one_or_none()
    existing_stream = uow.session.execute(
        select(EventStreamHeadRecord.stream_id).where(
            EventStreamHeadRecord.book_id == book_id,
            EventStreamHeadRecord.stream_type == "journal_transaction",
            EventStreamHeadRecord.stream_id == transaction_id,
        )
    ).scalar_one_or_none()
    if existing_projection is not None or existing_stream is not None:
        raise TransactionIdAlreadyExists("journal transaction id already exists")


def _load_reversal_source(
    uow: UnitOfWork,
    *,
    book_id: UUID,
    reverses_transaction_id: UUID,
    reversal_transaction_id: UUID,
) -> _ReversalSource:
    if reversal_transaction_id == reverses_transaction_id:
        raise TransactionIdAlreadyExists("a reversal requires a new transaction id")
    _ensure_transaction_id_available(
        uow,
        book_id=book_id,
        transaction_id=reversal_transaction_id,
    )
    already_reversed = uow.session.execute(
        select(TransactionReversalRecord.reversal_transaction_id).where(
            TransactionReversalRecord.book_id == book_id,
            TransactionReversalRecord.original_transaction_id
            == reverses_transaction_id,
        )
    ).scalar_one_or_none()
    if already_reversed is not None:
        raise TransactionAlreadyReversed("journal transaction is already reversed")

    transaction = uow.session.execute(
        select(JournalTransactionRecord)
        .where(
            JournalTransactionRecord.book_id == book_id,
            JournalTransactionRecord.transaction_id == reverses_transaction_id,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if transaction is None:
        raise TransactionNotFound("journal transaction not found in requested Book")
    event = uow.session.execute(
        select(LedgerEventRecord).where(
            LedgerEventRecord.book_id == book_id,
            LedgerEventRecord.event_id == transaction.source_event_id,
        )
    ).scalar_one_or_none()
    if event is None:
        raise InvalidTransactionSource("journal transaction source event is missing")
    payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
        event.event_type,
        event.event_schema_version,
        event.payload,
    )
    if type(payload) is CreditCardTransactionRecorded:
        if payload.transaction_id != reverses_transaction_id:
            raise InvalidTransactionSource(
                "credit-card event transaction identity mismatch"
            )
        if (
            payload.intent is CreditCardIntent.CHARGE
            and _has_active_credit_card_refunds(
                uow,
                book_id=book_id,
                charge_transaction_id=reverses_transaction_id,
            )
        ):
            raise CreditCardChargeHasActiveRefunds(
                "credit-card charge refunds must be reversed first"
            )
        postings = payload.postings
    elif type(payload) is JournalTransactionPosted:
        if payload.transaction_id != reverses_transaction_id:
            raise InvalidTransactionSource("posted event transaction identity mismatch")
        postings = payload.postings
    elif type(payload) is JournalTransactionReversed:
        if payload.reversal_transaction_id != reverses_transaction_id:
            raise InvalidTransactionSource(
                "reversal event transaction identity mismatch"
            )
        postings = payload.inverse_postings
    else:
        raise InvalidTransactionSource(
            "journal transaction source event cannot be reversed"
        )
    if type(payload) is JournalTransactionReversed and _source_touches_credit_card_account(
        uow,
        book_id=book_id,
        postings=postings,
    ):
        raise CreditCardReversalChainForbidden(
            "a credit-card reversal cannot itself be reversed"
        )
    return _ReversalSource(
        transaction=transaction,
        event=event,
        postings=postings,
    )


def _source_touches_credit_card_account(
    uow: UnitOfWork,
    *,
    book_id: UUID,
    postings: tuple[JournalPostingFact, ...],
) -> bool:
    account_ids = tuple(sorted({posting.account_id for posting in postings}, key=str))
    if not account_ids:
        return False
    return (
        uow.session.scalar(
            select(AccountRecord.account_id)
            .where(
                AccountRecord.book_id == book_id,
                AccountRecord.account_id.in_(account_ids),
                AccountRecord.account_subtype == "credit_card",
            )
            .limit(1)
        )
        is not None
    )


def _closed_credit_card_account_id(
    uow: UnitOfWork,
    *,
    book_id: UUID,
    postings: tuple[JournalPostingFact, ...],
) -> UUID | None:
    account_ids = tuple(sorted({posting.account_id for posting in postings}, key=str))
    if not account_ids:
        return None
    return uow.session.scalar(
        select(AccountRecord.account_id)
        .where(
            AccountRecord.book_id == book_id,
            AccountRecord.account_id.in_(account_ids),
            AccountRecord.account_subtype == "credit_card",
            AccountRecord.status == "closed",
        )
        .limit(1)
    )


def _has_active_credit_card_refunds(
    uow: UnitOfWork,
    *,
    book_id: UUID,
    charge_transaction_id: UUID,
) -> bool:
    return (
        uow.session.execute(
            select(CreditCardTransactionRecord.transaction_id)
            .outerjoin(
                TransactionReversalRecord,
                and_(
                    TransactionReversalRecord.book_id
                    == CreditCardTransactionRecord.book_id,
                    TransactionReversalRecord.original_transaction_id
                    == CreditCardTransactionRecord.transaction_id,
                ),
            )
            .where(
                CreditCardTransactionRecord.book_id == book_id,
                CreditCardTransactionRecord.intent == CreditCardIntent.REFUND.value,
                CreditCardTransactionRecord.original_transaction_id
                == charge_transaction_id,
                TransactionReversalRecord.reversal_transaction_id.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _build_reversal_pending(
    *,
    command_id: UUID,
    reversal_transaction_id: UUID,
    reason_code: ReversalReasonCode,
    effective_at: datetime,
    description_ref: UUID | None,
    expected_source: _ReversalSource,
    actor: CommandActor,
    event_id: UUID | None = None,
) -> PendingEvent:
    reversal_event_id = event_id or uuid5(
        _REVERSE_EVENT_NAMESPACE,
        str(command_id),
    )
    inverse_postings = tuple(
        JournalPostingFact(
            posting_id=uuid5(
                _REVERSE_EVENT_NAMESPACE,
                f"{reversal_transaction_id}:posting:{posting.posting_id}",
            ),
            position=posting.position,
            account_id=posting.account_id,
            asset_code=posting.asset_code,
            side=(
                PostingSide.CREDIT
                if posting.side is PostingSide.DEBIT
                else PostingSide.DEBIT
            ),
            units=posting.units,
        )
        for posting in expected_source.postings
    )
    return PendingEvent(
        event_id=reversal_event_id,
        stream_type="journal_transaction",
        stream_id=reversal_transaction_id,
        payload=JournalTransactionReversed(
            reversal_transaction_id=reversal_transaction_id,
            reverses_transaction_id=expected_source.transaction.transaction_id,
            original_event_id=expected_source.event.event_id,
            original_event_hash=expected_source.event.event_hash.hex(),
            reason_code=reason_code,
            inverse_postings=inverse_postings,
            description_ref=description_ref,
        ),
        command_id=command_id,
        actor_subject_id=actor.subject_id,
        correlation_id=command_id,
        causation_event_id=expected_source.event.event_id,
        effective_at=effective_at,
    )


__all__ = [
    "CreditCardChargeHasActiveRefunds",
    "CreditCardReversalChainForbidden",
    "CreditCardReversalPrecedesOriginal",
    "CreditCardReversalRequiresActiveAccount",
    "InvalidTransactionSource",
    "ReverseTransactionCommand",
    "TransactionAlreadyReversed",
    "TransactionIdAlreadyExists",
    "TransactionNotFound",
    "execute_reverse_transaction",
]
