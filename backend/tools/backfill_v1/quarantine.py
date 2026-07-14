from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import json
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from track_anywhere.infrastructure.db.models.backfill import BackfillQuarantineRecord
from track_anywhere.observability.audit import redact_sensitive

from .namespaces import BACKFILL_V1_NAMESPACE


def record_quarantine(
    session_factory: Callable[[], Session],
    *,
    snapshot_id: str,
    source_table: str,
    source_primary_key: str,
    reason_code: str,
    details: Mapping[str, object],
) -> UUID:
    identity = json.dumps(
        [snapshot_id, source_table, source_primary_key, reason_code],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    quarantine_id = uuid5(BACKFILL_V1_NAMESPACE, f"quarantine:{identity}")
    safe_details = redact_sensitive(dict(details))
    if not isinstance(safe_details, dict):
        raise AssertionError("quarantine details must remain an object")
    with session_factory() as session, session.begin():
        existing = session.execute(
            select(BackfillQuarantineRecord).where(
                BackfillQuarantineRecord.snapshot_id == snapshot_id,
                BackfillQuarantineRecord.source_table == source_table,
                BackfillQuarantineRecord.source_primary_key == source_primary_key,
                BackfillQuarantineRecord.reason_code == reason_code,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.details != safe_details:
                raise RuntimeError(
                    "quarantine identity was reused with different details"
                )
            return existing.quarantine_id
        session.add(
            BackfillQuarantineRecord(
                quarantine_id=quarantine_id,
                snapshot_id=snapshot_id,
                source_table=source_table,
                source_primary_key=source_primary_key,
                reason_code=reason_code,
                details=safe_details,
                decision="pending",
                decided_by=None,
                decided_at=None,
            )
        )
    return quarantine_id


def decide_quarantine(
    session_factory: Callable[[], Session],
    quarantine_id: UUID,
    *,
    decision: str,
    actor_subject_id: str,
) -> None:
    if decision not in {"accepted", "skipped"}:
        raise ValueError("quarantine decision must be accepted or skipped")
    if not actor_subject_id:
        raise ValueError("quarantine decision actor must be nonblank")
    with session_factory() as session, session.begin():
        record = session.execute(
            select(BackfillQuarantineRecord)
            .where(BackfillQuarantineRecord.quarantine_id == quarantine_id)
            .with_for_update()
        ).scalar_one_or_none()
        if record is None:
            raise LookupError("quarantine record not found")
        if record.decision != "pending":
            if (record.decision, record.decided_by) != (decision, actor_subject_id):
                raise RuntimeError("quarantine decision is immutable")
            return
        record.decision = decision
        record.decided_by = actor_subject_id
        record.decided_at = datetime.now(UTC)
        session.flush([record])


__all__ = ["decide_quarantine", "record_quarantine"]
