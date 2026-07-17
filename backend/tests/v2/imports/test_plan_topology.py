from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.application.imports.event_compiler import (
    FROZEN_IMPORT_ACTOR_SUBJECT_ID,
    compile_current_v2_events,
)
from track_anywhere.domain.journal.events import (
    ExternalReferenceKind,
    FinancialExternalReference,
    JournalPostingFact,
    JournalTransactionPosted,
    JournalTransactionReversed,
    ReversalReasonCode,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.serialization.canonical_json import EventHashEnvelope, event_hash
from track_anywhere.serialization.event_registry import PRODUCTION_EVENT_REGISTRY


IDS = tuple(UUID(f"00000000-0000-4000-8000-{value:012x}") for value in range(1, 20))
WHEN = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC)


def _pending() -> PendingEvent:
    payload = JournalTransactionPosted(
        transaction_id=IDS[2],
        kind=TransactionKind.STANDARD,
        postings=(
            JournalPostingFact(
                posting_id=IDS[3],
                position=0,
                account_id=IDS[4],
                asset_code="TST",
                side=PostingSide.DEBIT,
                units="100",
            ),
            JournalPostingFact(
                posting_id=IDS[5],
                position=1,
                account_id=IDS[6],
                asset_code="TST",
                side=PostingSide.CREDIT,
                units="100",
            ),
        ),
        description_ref=IDS[7],
        external_references=(
            FinancialExternalReference(
                provider_code="v1_history",
                kind=ExternalReferenceKind.PROVIDER_TRANSACTION,
                reference="sha256:" + "a" * 64,
            ),
        ),
    )
    return PendingEvent(
        event_id=IDS[1],
        stream_type="journal_transaction",
        stream_id=IDS[2],
        payload=payload,
        command_id=IDS[8],
        actor_subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID,
        correlation_id=IDS[8],
        causation_event_id=None,
        effective_at=WHEN,
    )


def test_compiler_uses_the_exact_current_store_hash_contract_from_an_empty_head() -> (
    None
):
    pending = _pending()
    planned = compile_current_v2_events(target_book_id=IDS[0], events=(pending,))

    assert len(planned) == 1
    event = planned[0]
    assert (
        event.book_position,
        event.expected_stream_version,
        event.stream_version,
    ) == (
        1,
        0,
        1,
    )
    stored_payload = PRODUCTION_EVENT_REGISTRY.dump_registered(pending.payload)
    expected = event_hash(
        EventHashEnvelope(
            event_id=pending.event_id,
            book_id=IDS[0],
            book_position=1,
            global_sequence=1,
            recorded_at=datetime(1970, 1, 1, tzinfo=UTC),
            stream_type=pending.stream_type,
            stream_id=pending.stream_id,
            stream_version=1,
            event_type=type(pending.payload).event_type,
            event_schema_version=type(pending.payload).schema_version,
            command_id=pending.command_id,
            actor_subject_id=pending.actor_subject_id,
            correlation_id=pending.correlation_id,
            causation_event_id=None,
            effective_at=WHEN,
            previous_hash=bytes(32),
        ),
        stored_payload,
    ).hex()
    assert event.previous_hash == "0" * 64
    assert event.event_hash == expected
    assert "sha256:" not in repr(event)


def test_compiler_normalizes_equivalent_effective_instants_to_utc() -> None:
    pending = _pending()
    offset_pending = replace(
        pending,
        effective_at=pending.effective_at.astimezone(timezone(timedelta(hours=8))),
    )

    utc_event = compile_current_v2_events(target_book_id=IDS[0], events=(pending,))[0]
    offset_event = compile_current_v2_events(
        target_book_id=IDS[0], events=(offset_pending,)
    )[0]

    assert offset_event.effective_at.tzinfo is UTC
    assert offset_event.model_dump_json() == utc_event.model_dump_json()


def test_compiler_rejects_reversal_provenance_hash_drift() -> None:
    original = _pending()
    reversal_payload = JournalTransactionReversed(
        reversal_transaction_id=IDS[9],
        reverses_transaction_id=IDS[2],
        original_event_id=IDS[1],
        original_event_hash="b" * 64,
        reason_code=ReversalReasonCode.IMPORT_CORRECTION,
        inverse_postings=tuple(
            JournalPostingFact(
                posting_id=IDS[10 + posting.position],
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
            for posting in original.payload.postings  # type: ignore[union-attr]
        ),
        description_ref=IDS[12],
    )
    reversal = PendingEvent(
        event_id=IDS[13],
        stream_type="journal_transaction",
        stream_id=IDS[9],
        payload=reversal_payload,
        command_id=original.command_id,
        actor_subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID,
        correlation_id=original.command_id,
        causation_event_id=original.event_id,
        effective_at=WHEN,
    )

    with pytest.raises(ValueError, match="reversal provenance"):
        compile_current_v2_events(
            target_book_id=IDS[0],
            events=(original, reversal),
        )


def test_compiler_rejects_stream_identity_that_current_projectors_cannot_apply() -> (
    None
):
    pending = _pending()
    invalid = PendingEvent(
        event_id=pending.event_id,
        stream_type="wrong_stream",
        stream_id=pending.stream_id,
        payload=pending.payload,
        command_id=pending.command_id,
        actor_subject_id=pending.actor_subject_id,
        correlation_id=pending.correlation_id,
        causation_event_id=pending.causation_event_id,
        effective_at=pending.effective_at,
    )

    with pytest.raises(ValueError, match="stream identity"):
        compile_current_v2_events(target_book_id=IDS[0], events=(invalid,))
