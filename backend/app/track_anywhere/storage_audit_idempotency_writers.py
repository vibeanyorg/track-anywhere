from __future__ import annotations

from sqlalchemy.orm import Session

from .audit import AuditEvent
from .storage_json import to_jsonable
from .storage_models import AuditEventRecord, IdempotencyReceiptRecord
from .storage_redaction import redact_idempotency_result
from .storage_upsert_writers import upsert_record


def save_audit_events(session: Session, events: list[AuditEvent]) -> None:
    for event in events:
        upsert_record(
            session,
            AuditEventRecord,
            {
                "event_id": event.event_id,
                "operation": event.operation,
                "actor_id": event.actor_id,
                "actor_type": event.actor_type,
                "entity_ref": event.entity_ref,
                "details": to_jsonable(event.details),
                "created_at": event.created_at,
            },
            ["event_id"],
        )


def save_idempotency_receipts(session: Session, receipts) -> None:
    for receipt in receipts:
        upsert_record(
            session,
            IdempotencyReceiptRecord,
            {
                "key_hash": receipt.key_hash,
                "actor_id": receipt.actor_id,
                "operation": receipt.operation,
                "request_hash": receipt.request_hash,
                "result": redact_idempotency_result(to_jsonable(receipt.stored_result)),
                "replay_count": receipt.replay_count,
            },
            ["key_hash", "actor_id", "operation"],
        )
