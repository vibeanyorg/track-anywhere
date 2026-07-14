from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from backend.tools.backfill_v1.generate import GeneratedEvent, sort_generated_events


def _event(
    *,
    event_id: str,
    transaction_id: str,
    effective_at: datetime,
    event_kind: str,
) -> GeneratedEvent:
    return GeneratedEvent(
        event_id=UUID(event_id),
        transaction_id=UUID(transaction_id),
        effective_at=effective_at,
        event_kind=event_kind,
        payload={"kind": event_kind},
    )


def test_sort_is_utc_then_transaction_bytes_then_kind_ordinal() -> None:
    same_instant_local = datetime(
        2026,
        7,
        14,
        20,
        tzinfo=timezone(timedelta(hours=12)),
    )
    events = [
        _event(
            event_id="00000000-0000-4000-8000-000000000004",
            transaction_id="00000000-0000-4000-8000-000000000002",
            effective_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
            event_kind="reporting.assigned",
        ),
        _event(
            event_id="00000000-0000-4000-8000-000000000003",
            transaction_id="00000000-0000-4000-8000-000000000001",
            effective_at=same_instant_local,
            event_kind="journal.posted",
        ),
        _event(
            event_id="00000000-0000-4000-8000-000000000002",
            transaction_id="00000000-0000-4000-8000-000000000001",
            effective_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
            event_kind="reporting.assigned",
        ),
        _event(
            event_id="00000000-0000-4000-8000-000000000001",
            transaction_id="00000000-0000-4000-8000-000000000099",
            effective_at=datetime(2026, 1, 1, tzinfo=UTC),
            event_kind="journal.posted",
        ),
    ]

    ordered = sort_generated_events(reversed(events))

    assert [str(event.event_id) for event in ordered] == [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000003",
        "00000000-0000-4000-8000-000000000002",
        "00000000-0000-4000-8000-000000000004",
    ]


def test_naive_effective_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _event(
            event_id="00000000-0000-4000-8000-000000000001",
            transaction_id="00000000-0000-4000-8000-000000000001",
            effective_at=datetime(2026, 1, 1),
            event_kind="journal.posted",
        )
