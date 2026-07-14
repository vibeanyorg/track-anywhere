from __future__ import annotations

from datetime import UTC, datetime
import time
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.monthly import (
    post_classified_expense,
    seed_monthly_scenario,
)
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.outbox import OutboxMessageRecord
from track_anywhere.outbox.worker import OutboxDeliveryWorker


def test_publish_effect_then_ack_crash_redelivers_with_stable_message_id(
    pg_engine,
) -> None:
    scenario = seed_monthly_scenario(pg_engine, actor_id="human:outbox")
    post_classified_expense(
        pg_engine,
        scenario,
        effective_at=datetime(2026, 7, 1, tzinfo=UTC),
        amount="1.00",
    )
    message_id = uuid4()
    with Session(pg_engine) as session:
        source_event_id = session.scalar(
            select(LedgerEventRecord.event_id)
            .where(LedgerEventRecord.book_id == scenario.journal.book_id)
            .order_by(LedgerEventRecord.book_position)
            .limit(1)
        )
        assert source_event_id is not None
        session.execute(
            OutboxMessageRecord.__table__.insert().values(
                message_id=message_id,
                book_id=scenario.journal.book_id,
                source_event_id=source_event_id,
                topic="ledger.events",
                message_type="journal.posted",
                dedupe_key=f"event:{source_event_id}",
                payload={"event_id": str(source_event_id)},
            )
        )
        session.commit()

    calls: list[object] = []
    consumer_effects: set[object] = set()

    def publish(message) -> None:
        calls.append(message.message_id)
        consumer_effects.add(message.message_id)
        if len(calls) == 1:
            raise RuntimeError("publisher outcome unknown after consumer effect")

    factory = sessionmaker(pg_engine, expire_on_commit=False)
    worker = OutboxDeliveryWorker(
        factory,
        publish=publish,
        worker_id="worker-a",
        lease_seconds=1,
    )
    with pytest.raises(RuntimeError, match="outcome unknown"):
        worker.run_once()
    time.sleep(1.05)
    delivered = worker.run_once()

    assert delivered is not None and delivered.message_id == message_id
    assert calls == [message_id, message_id]
    assert consumer_effects == {message_id}
    with Session(pg_engine) as session:
        stored = session.get(OutboxMessageRecord, message_id)
        assert stored is not None
        assert stored.delivered_at is not None
        assert stored.attempt_count == 2
