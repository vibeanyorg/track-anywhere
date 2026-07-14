from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..infrastructure.db.models.outbox import OutboxMessageRecord
from ..observability.metrics import LedgerMetrics


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    message_id: UUID
    book_id: UUID
    source_event_id: UUID
    topic: str
    message_type: str
    dedupe_key: str
    payload: dict[str, object]
    attempt_count: int


class OutboxDeliveryWorker:
    """Claim/commit, publish, then ack in a separate transaction.

    A crash after the external publish and before ack intentionally redelivers
    the same stable message ID. Consumers therefore deduplicate on message_id.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        publish: Callable[[OutboxMessage], None],
        worker_id: str,
        lease_seconds: int = 30,
        metrics: LedgerMetrics | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("worker_id is outside its allowed bound")
        if type(lease_seconds) is not int or not 0 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds is outside its allowed range")
        self._session_factory = session_factory
        self._publish = publish
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._metrics = metrics
        self._now = now or (lambda: datetime.now(UTC))

    def run_once(self) -> OutboxMessage | None:
        message = self._claim()
        if message is None:
            return None
        try:
            self._publish(message)
        except Exception:
            if self._metrics is not None:
                self._metrics.increment("outbox.publish_outcome_unknown")
            raise
        self._ack(message.message_id)
        if self._metrics is not None:
            self._metrics.increment("outbox.delivered")
        return message

    def _claim(self) -> OutboxMessage | None:
        claimed_at = self._now()
        locked_until = claimed_at + timedelta(seconds=self._lease_seconds)
        with self._session_factory() as session, session.begin():
            record = session.execute(
                select(OutboxMessageRecord)
                .where(
                    OutboxMessageRecord.delivered_at.is_(None),
                    OutboxMessageRecord.available_at <= claimed_at,
                    or_(
                        OutboxMessageRecord.locked_until.is_(None),
                        OutboxMessageRecord.locked_until <= claimed_at,
                    ),
                )
                .order_by(
                    OutboxMessageRecord.available_at,
                    OutboxMessageRecord.message_id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            ).scalar_one_or_none()
            if record is None:
                return None
            record.attempt_count += 1
            record.locked_by = self._worker_id
            record.locked_until = locked_until
            record.last_error_code = None
            session.flush([record])
            return OutboxMessage(
                message_id=record.message_id,
                book_id=record.book_id,
                source_event_id=record.source_event_id,
                topic=record.topic,
                message_type=record.message_type,
                dedupe_key=record.dedupe_key,
                payload=deepcopy(record.payload),
                attempt_count=record.attempt_count,
            )

    def _ack(self, message_id: UUID) -> None:
        delivered_at = self._now()
        with self._session_factory() as session, session.begin():
            result = session.execute(
                update(OutboxMessageRecord)
                .where(
                    OutboxMessageRecord.message_id == message_id,
                    OutboxMessageRecord.delivered_at.is_(None),
                    OutboxMessageRecord.locked_by == self._worker_id,
                )
                .values(
                    delivered_at=delivered_at,
                    locked_by=None,
                    locked_until=None,
                    last_error_code=None,
                )
            )
            if result.rowcount != 1:
                raise RuntimeError("outbox claim was lost before acknowledgement")


__all__ = ["OutboxDeliveryWorker", "OutboxMessage"]
