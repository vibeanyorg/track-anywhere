from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeAlias
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..infrastructure.db.models.catalog import BookRecord
from ..infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    LedgerEventRecord,
)
from ..infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
)
from ..serialization.canonical_json import EventHashEnvelope, event_hash
from .metrics import LedgerMetrics


_REDACTED = "[REDACTED]"
_SENSITIVE_PARTS = (
    "api_key",
    "attachment",
    "authorization",
    "credential",
    "csrf",
    "dsn",
    "expected_hash",
    "actual_hash",
    "memo",
    "password",
    "payload",
    "secret",
    "setup_key",
    "token",
)

SafeValue: TypeAlias = (
    str | int | float | bool | None | list["SafeValue"] | dict[str, "SafeValue"]
)


def redact_sensitive(value: object, *, _key: str = "") -> SafeValue:
    if _is_sensitive_key(_key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(key): redact_sensitive(item, _key=str(key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        rendered = value
        if type(rendered) is str and len(rendered) > 256:
            return rendered[:253] + "..."
        return rendered
    return str(value)[:256]


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_PARTS)


@dataclass(frozen=True, slots=True)
class AuditSignal:
    severity: str
    code: str
    book_id: str
    fields: dict[str, SafeValue]
    occurred_at: datetime

    @classmethod
    def p0(
        cls,
        *,
        code: str,
        book_id: str | UUID,
        fields: Mapping[str, object] | None = None,
    ) -> AuditSignal:
        safe = redact_sensitive(dict(fields or {}))
        if not isinstance(safe, dict):
            raise AssertionError("audit fields must remain an object")
        return cls(
            severity="P0",
            code=code,
            book_id=str(book_id),
            fields=safe,
            occurred_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class IntegrityAuditResult:
    book_id: UUID
    terminal_hash_ok: bool
    balance_parity_ok: bool
    paused: bool
    signals: tuple[AuditSignal, ...]


class LedgerIntegrityAuditor:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        emit: Callable[[AuditSignal], None] | None = None,
        metrics: LedgerMetrics | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._emit = emit or (lambda _signal: None)
        self._metrics = metrics

    def audit_book(
        self,
        book_id: UUID,
        *,
        trusted_terminal_hash: bytes | None = None,
    ) -> IntegrityAuditResult:
        signals: list[AuditSignal] = []
        with self._session_factory() as session, session.begin():
            book = session.execute(
                select(BookRecord)
                .where(BookRecord.book_id == book_id)
                .with_for_update()
            ).scalar_one_or_none()
            if book is None:
                raise LookupError("Book not found")
            terminal_ok = self._terminal_hash_ok(
                session,
                book_id,
                trusted_terminal_hash=trusted_terminal_hash,
            )
            balance_ok = self._balance_parity_ok(session, book_id)
            if not terminal_ok:
                signals.append(
                    AuditSignal.p0(
                        code="terminal_hash_mismatch",
                        book_id=book_id,
                        fields={"expected_hash": trusted_terminal_hash},
                    )
                )
            if not balance_ok:
                signals.append(
                    AuditSignal.p0(
                        code="balance_projection_mismatch",
                        book_id=book_id,
                    )
                )
            if signals:
                book.write_state = "paused_integrity"
                session.flush([book])

        for signal in signals:
            self._emit(signal)
            if self._metrics is not None:
                self._metrics.increment(
                    "integrity.p0",
                    labels={"book_id": signal.book_id, "code": signal.code},
                )
        return IntegrityAuditResult(
            book_id=book_id,
            terminal_hash_ok=terminal_ok,
            balance_parity_ok=balance_ok,
            paused=bool(signals),
            signals=tuple(signals),
        )

    @staticmethod
    def _terminal_hash_ok(
        session: Session,
        book_id: UUID,
        *,
        trusted_terminal_hash: bytes | None,
    ) -> bool:
        head = session.get(BookEventHeadRecord, book_id)
        if head is None:
            return False
        previous = bytes(32)
        position = 0
        for record in session.scalars(
            select(LedgerEventRecord)
            .where(LedgerEventRecord.book_id == book_id)
            .order_by(LedgerEventRecord.book_position)
        ):
            position += 1
            if record.book_position != position or record.previous_hash != previous:
                return False
            computed = event_hash(
                EventHashEnvelope(
                    event_id=record.event_id,
                    book_id=record.book_id,
                    book_position=record.book_position,
                    global_sequence=record.global_sequence,
                    recorded_at=record.recorded_at,
                    stream_type=record.stream_type,
                    stream_id=record.stream_id,
                    stream_version=record.stream_version,
                    event_type=record.event_type,
                    event_schema_version=record.event_schema_version,
                    command_id=record.command_id,
                    actor_subject_id=record.actor_subject_id,
                    correlation_id=record.correlation_id,
                    causation_event_id=record.causation_event_id,
                    effective_at=record.effective_at,
                    previous_hash=record.previous_hash,
                ),
                record.payload,  # type: ignore[arg-type]
            )
            if computed != record.event_hash:
                return False
            previous = computed
        if (head.last_position, head.last_hash) != (position, previous):
            return False
        return trusted_terminal_hash is None or head.last_hash == trusted_terminal_hash

    @staticmethod
    def _balance_parity_ok(session: Session, book_id: UUID) -> bool:
        signed_units = case(
            (JournalPostingRecord.side == "debit", JournalPostingRecord.units),
            else_=-JournalPostingRecord.units,
        )
        posting_rows = session.execute(
            select(
                JournalPostingRecord.account_id,
                JournalPostingRecord.asset_code,
                func.sum(signed_units),
            )
            .where(JournalPostingRecord.book_id == book_id)
            .group_by(
                JournalPostingRecord.account_id,
                JournalPostingRecord.asset_code,
            )
        ).all()
        expected = {
            (account_id, asset_code): int(units)
            for account_id, asset_code, units in posting_rows
        }
        actual = {
            (row.account_id, row.asset_code): int(row.balance_units)
            for row in session.scalars(
                select(AccountBalanceRecord).where(
                    AccountBalanceRecord.book_id == book_id
                )
            )
        }
        return expected == actual


__all__ = [
    "AuditSignal",
    "IntegrityAuditResult",
    "LedgerIntegrityAuditor",
    "redact_sensitive",
]
