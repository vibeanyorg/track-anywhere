from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hmac import compare_digest
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...application.idempotency import CommandResult, IdempotencyConflict
from .models.event_store import CommandReceiptRecord


class ReceiptStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReceiptScope:
    actor_subject_id: str
    book_id: UUID
    operation: str
    idempotency_key_hash: bytes


@dataclass(frozen=True, slots=True)
class ReceiptSnapshot:
    scope: ReceiptScope
    request_hash: bytes
    command_id: UUID
    status: str
    response_schema_version: int | None
    result_status: int | None
    result_body: dict[str, object] | list[object] | None
    first_book_position: int | None
    last_book_position: int | None
    created_at: datetime
    completed_at: datetime | None

    def replay_result(self) -> CommandResult:
        if (
            self.status != "completed"
            or self.response_schema_version is None
            or self.result_status is None
            or self.result_body is None
        ):
            raise ReceiptStateError("command receipt is not replayable")
        return CommandResult(
            response_schema_version=self.response_schema_version,
            status_code=self.result_status,
            body=deepcopy(self.result_body),
            first_book_position=self.first_book_position,
            last_book_position=self.last_book_position,
        )


@dataclass(frozen=True, slots=True)
class ReceiptReservation:
    created: bool
    receipt: ReceiptSnapshot

    def replay_or_conflict(self, request_hash: bytes) -> CommandResult:
        if self.created:
            raise ReceiptStateError("new command receipt cannot be replayed")
        if not compare_digest(self.receipt.request_hash, request_hash):
            raise IdempotencyConflict
        return self.receipt.replay_result()


class CommandReceiptRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def reserve_or_lock(
        self,
        scope: ReceiptScope,
        *,
        request_hash: bytes,
        command_id: UUID,
    ) -> ReceiptReservation:
        inserted = self._session.execute(
            insert(CommandReceiptRecord)
            .values(
                actor_subject_id=scope.actor_subject_id,
                book_id=scope.book_id,
                operation=scope.operation,
                idempotency_key_hash=scope.idempotency_key_hash,
                request_hash=request_hash,
                command_id=command_id,
                status="processing",
            )
            .on_conflict_do_nothing()
            .returning(CommandReceiptRecord.command_id)
        ).scalar_one_or_none()
        record = self._lock(scope)
        if record is None:
            raise ReceiptStateError("command receipt conflict has no matching scope")
        created = inserted is not None
        if created and record.status != "processing":
            raise ReceiptStateError("new command receipt has an invalid state")
        if not created and record.status != "completed":
            raise ReceiptStateError("existing command receipt is not completed")
        return ReceiptReservation(created=created, receipt=self._snapshot(record))

    def complete(self, scope: ReceiptScope, result: CommandResult) -> ReceiptSnapshot:
        record = self._lock(scope)
        if record is None or record.status != "processing":
            raise ReceiptStateError("command receipt cannot be completed")
        record.status = "completed"
        record.response_schema_version = result.response_schema_version
        record.result_status = result.status_code
        record.result_body = deepcopy(result.body)
        record.first_book_position = result.first_book_position
        record.last_book_position = result.last_book_position
        record.completed_at = self._session.scalar(select(func.clock_timestamp()))
        self._session.flush([record])
        return self._snapshot(record)

    def _lock(self, scope: ReceiptScope) -> CommandReceiptRecord | None:
        return self._session.execute(
            select(CommandReceiptRecord)
            .where(
                CommandReceiptRecord.actor_subject_id == scope.actor_subject_id,
                CommandReceiptRecord.book_id == scope.book_id,
                CommandReceiptRecord.operation == scope.operation,
                CommandReceiptRecord.idempotency_key_hash == scope.idempotency_key_hash,
            )
            .with_for_update()
        ).scalar_one_or_none()

    @staticmethod
    def _snapshot(record: CommandReceiptRecord) -> ReceiptSnapshot:
        return ReceiptSnapshot(
            scope=ReceiptScope(
                actor_subject_id=record.actor_subject_id,
                book_id=record.book_id,
                operation=record.operation,
                idempotency_key_hash=record.idempotency_key_hash,
            ),
            request_hash=record.request_hash,
            command_id=record.command_id,
            status=record.status,
            response_schema_version=record.response_schema_version,
            result_status=record.result_status,
            result_body=deepcopy(record.result_body),
            first_book_position=record.first_book_position,
            last_book_position=record.last_book_position,
            created_at=record.created_at,
            completed_at=record.completed_at,
        )


__all__ = [
    "CommandReceiptRepository",
    "ReceiptReservation",
    "ReceiptScope",
    "ReceiptSnapshot",
    "ReceiptStateError",
]
