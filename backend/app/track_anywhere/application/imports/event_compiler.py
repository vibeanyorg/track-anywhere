from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from ...application.event_batch import PendingEvent
from ...domain.journal.events import (
    JournalPostingFact,
    JournalTransactionPosted,
    JournalTransactionReversed,
)
from ...domain.journal.models import PostingSide
from ...domain.reporting.events import ReportingLinesAssigned
from ...serialization.canonical_json import EventHashEnvelope, event_hash
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from .contracts import FROZEN_IMPORT_ACTOR_SUBJECT_ID, PlannedLedgerEvent


_HASH_PLACEHOLDER_RECORDED_AT = datetime(1970, 1, 1, tzinfo=UTC)
_ALLOWED_PAYLOAD_TYPES = (
    JournalTransactionPosted,
    JournalTransactionReversed,
    ReportingLinesAssigned,
)


def _transaction_id_and_postings(
    payload: JournalTransactionPosted | JournalTransactionReversed,
) -> tuple[UUID, tuple[JournalPostingFact, ...]]:
    if type(payload) is JournalTransactionPosted:
        return payload.transaction_id, payload.postings
    return payload.reversal_transaction_id, payload.inverse_postings


def _validate_reversal(
    pending: PendingEvent,
    payload: JournalTransactionReversed,
    planned_by_id: dict[UUID, PlannedLedgerEvent],
) -> None:
    source = planned_by_id.get(payload.original_event_id)
    if (
        source is None
        or type(source.payload)
        not in (JournalTransactionPosted, JournalTransactionReversed)
        or source.event_hash != payload.original_event_hash
        or pending.causation_event_id != source.event_id
    ):
        raise ValueError("reversal provenance does not match the canonical prefix")
    source_transaction_id, source_postings = _transaction_id_and_postings(
        source.payload
    )
    if source_transaction_id != payload.reverses_transaction_id:
        raise ValueError("reversal provenance transaction identity mismatch")
    if len(source_postings) != len(payload.inverse_postings):
        raise ValueError("reversal postings are not an exact inverse")
    source_posting_ids = {posting.posting_id for posting in source_postings}
    inverse_posting_ids = {posting.posting_id for posting in payload.inverse_postings}
    if source_posting_ids & inverse_posting_ids:
        raise ValueError("reversal postings are not an exact inverse")
    for source_posting, inverse in zip(
        source_postings, payload.inverse_postings, strict=True
    ):
        expected_side = (
            PostingSide.CREDIT
            if source_posting.side is PostingSide.DEBIT
            else PostingSide.DEBIT
        )
        if (
            inverse.position != source_posting.position
            or inverse.account_id != source_posting.account_id
            or inverse.asset_code != source_posting.asset_code
            or inverse.units != source_posting.units
            or inverse.side is not expected_side
        ):
            raise ValueError("reversal postings are not an exact inverse")


def _validate_stream_identity(
    pending: PendingEvent,
    *,
    journal_by_transaction: dict[UUID, PlannedLedgerEvent],
    reporting_started: bool,
) -> None:
    payload = pending.payload
    if type(payload) is JournalTransactionPosted:
        valid = (
            not reporting_started
            and pending.stream_type == "journal_transaction"
            and pending.stream_id == payload.transaction_id
            and pending.causation_event_id is None
            and payload.transaction_id not in journal_by_transaction
        )
    elif type(payload) is JournalTransactionReversed:
        valid = (
            not reporting_started
            and pending.stream_type == "journal_transaction"
            and pending.stream_id == payload.reversal_transaction_id
            and payload.reversal_transaction_id not in journal_by_transaction
        )
    else:
        source = journal_by_transaction.get(payload.transaction_id)
        valid = (
            pending.stream_type == "reporting_lines"
            and pending.stream_id == payload.transaction_id
            and payload.classification_revision == 1
            and source is not None
            and type(source.payload) is JournalTransactionPosted
            and pending.causation_event_id == source.event_id
        )
    if not valid:
        raise ValueError("planned event stream identity is invalid")


def compile_current_v2_events(
    *, target_book_id: UUID, events: Sequence[PendingEvent]
) -> tuple[PlannedLedgerEvent, ...]:
    if type(target_book_id) is not UUID:
        raise ValueError("target Book identity is invalid")
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise ValueError("planned events must be a sequence")
    pending_events = tuple(events)
    if not pending_events or any(
        type(item) is not PendingEvent for item in pending_events
    ):
        raise ValueError("planned event sequence is invalid")
    stream_keys = tuple(item.stream_key for item in pending_events)
    if len(stream_keys) != len(set(stream_keys)):
        raise ValueError("frozen import requires one event per new stream")
    command_ids = {item.command_id for item in pending_events}
    if len(command_ids) != 1:
        raise ValueError("frozen import events must share one command")
    if any(
        item.actor_subject_id != FROZEN_IMPORT_ACTOR_SUBJECT_ID
        or item.correlation_id != item.command_id
        or type(item.payload) not in _ALLOWED_PAYLOAD_TYPES
        for item in pending_events
    ):
        raise ValueError("frozen import event envelope is invalid")

    previous_hash = bytes(32)
    planned: list[PlannedLedgerEvent] = []
    planned_by_id: dict[UUID, PlannedLedgerEvent] = {}
    journal_by_transaction: dict[UUID, PlannedLedgerEvent] = {}
    reporting_started = False
    for book_position, pending in enumerate(pending_events, start=1):
        if pending.event_id in planned_by_id:
            raise ValueError("planned event identity is duplicated")
        _validate_stream_identity(
            pending,
            journal_by_transaction=journal_by_transaction,
            reporting_started=reporting_started,
        )
        if type(pending.payload) is JournalTransactionReversed:
            _validate_reversal(pending, pending.payload, planned_by_id)
        stored_payload = PRODUCTION_EVENT_REGISTRY.dump_registered(pending.payload)
        event_type = type(pending.payload).event_type
        schema_version = type(pending.payload).schema_version
        hashed = event_hash(
            EventHashEnvelope(
                event_id=pending.event_id,
                book_id=target_book_id,
                book_position=book_position,
                global_sequence=1,
                recorded_at=_HASH_PLACEHOLDER_RECORDED_AT,
                stream_type=pending.stream_type,
                stream_id=pending.stream_id,
                stream_version=1,
                event_type=event_type,
                event_schema_version=schema_version,
                command_id=pending.command_id,
                actor_subject_id=pending.actor_subject_id,
                correlation_id=pending.correlation_id,
                causation_event_id=pending.causation_event_id,
                effective_at=pending.effective_at,
                previous_hash=previous_hash,
            ),
            stored_payload,
        )
        planned.append(
            PlannedLedgerEvent(
                event_id=pending.event_id,
                book_position=book_position,
                stream_type=pending.stream_type,
                stream_id=pending.stream_id,
                expected_stream_version=0,
                stream_version=1,
                event_type=event_type,
                event_schema_version=schema_version,
                payload=pending.payload,
                command_id=pending.command_id,
                actor_subject_id=pending.actor_subject_id,
                correlation_id=pending.correlation_id,
                causation_event_id=pending.causation_event_id,
                effective_at=pending.effective_at,
                previous_hash=previous_hash.hex(),
                event_hash=hashed.hex(),
            )
        )
        planned_by_id[pending.event_id] = planned[-1]
        if type(pending.payload) is JournalTransactionPosted:
            journal_by_transaction[pending.payload.transaction_id] = planned[-1]
        elif type(pending.payload) is JournalTransactionReversed:
            journal_by_transaction[pending.payload.reversal_transaction_id] = planned[
                -1
            ]
        else:
            reporting_started = True
        previous_hash = hashed
    return tuple(planned)


__all__ = ["FROZEN_IMPORT_ACTOR_SUBJECT_ID", "compile_current_v2_events"]
