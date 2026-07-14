from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import select

from ...domain.reporting.events import (
    ReportingDimension,
    ReportingLine,
    ReportingLineKind,
    ReportingLinesAssigned,
)
from ...infrastructure.db.models.projections import (
    JournalPostingRecord,
    JournalTransactionRecord,
)
from ...infrastructure.db.repositories.catalogs import CatalogRepository
from ...serialization.canonical_json import JSONValue, format_utc_microseconds
from ..command_bus import execute_financial
from ..event_batch import PendingEvent
from ..idempotency import CommandActor, CommandOutcome, IdempotencyValidationError
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..unit_of_work import UnitOfWork
from .post_transaction import Authorize, authorize_journal_write
from .reverse_transaction import TransactionNotFound


_CANONICAL_UNITS = re.compile(r"[1-9][0-9]{0,37}", flags=re.ASCII)
_ASSIGN_EVENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/reporting.assign",
)


class ReportingAllocationExceeded(ValueError):
    pass


class UnsupportedReportingDimension(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReportingLineInput:
    line_id: UUID
    line_version_id: UUID
    catalog_id: UUID
    asset_code: str
    units: str
    line_kind: ReportingLineKind
    dimension: ReportingDimension
    dimension_id: UUID | None = None
    description_ref: UUID | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("line_id", self.line_id),
            ("line_version_id", self.line_version_id),
            ("catalog_id", self.catalog_id),
        ):
            if type(value) is not UUID:
                raise IdempotencyValidationError(f"{name} must be a UUID")
        if (
            type(self.asset_code) is not str
            or not self.asset_code
            or len(self.asset_code) > 16
            or self.asset_code.upper() != self.asset_code
        ):
            raise IdempotencyValidationError("reporting asset_code is invalid")
        if (
            type(self.units) is not str
            or _CANONICAL_UNITS.fullmatch(self.units) is None
        ):
            raise IdempotencyValidationError(
                "reporting units must be a canonical positive integer string"
            )
        if type(self.line_kind) is not ReportingLineKind:
            raise IdempotencyValidationError("line_kind is invalid")
        if type(self.dimension) is not ReportingDimension:
            raise IdempotencyValidationError("dimension is invalid")
        if self.dimension_id is not None and type(self.dimension_id) is not UUID:
            raise IdempotencyValidationError("dimension_id must be a UUID or null")
        if self.description_ref is not None and type(self.description_ref) is not UUID:
            raise IdempotencyValidationError("description_ref must be a UUID or null")

    def canonical_value(self) -> dict[str, JSONValue]:
        return {
            "asset_code": self.asset_code,
            "catalog_id": str(self.catalog_id),
            "description_ref": (
                None if self.description_ref is None else str(self.description_ref)
            ),
            "dimension": self.dimension.value,
            "dimension_id": (
                None if self.dimension_id is None else str(self.dimension_id)
            ),
            "line_id": str(self.line_id),
            "line_kind": self.line_kind.value,
            "line_version_id": str(self.line_version_id),
            "units": self.units,
        }


@dataclass(frozen=True, slots=True)
class AssignReportingLinesCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    expected_revision: int
    lines: tuple[ReportingLineInput, ...]
    effective_at: datetime
    operation: str = field(default="reporting.assign", init=False)

    def __post_init__(self) -> None:
        for name, value in (
            ("book_id", self.book_id),
            ("command_id", self.command_id),
            ("transaction_id", self.transaction_id),
        ):
            if type(value) is not UUID:
                raise IdempotencyValidationError(f"{name} must be a UUID")
        if type(self.expected_revision) is not int or self.expected_revision < 0:
            raise IdempotencyValidationError(
                "expected_revision must be a non-negative integer"
            )
        if (
            type(self.lines) is not tuple
            or not self.lines
            or any(type(line) is not ReportingLineInput for line in self.lines)
        ):
            raise IdempotencyValidationError(
                "lines must be a non-empty immutable typed tuple"
            )
        try:
            format_utc_microseconds(self.effective_at)
        except (TypeError, ValueError):
            raise IdempotencyValidationError(
                "effective_at must be a timezone-aware datetime"
            ) from None

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "effective_at": format_utc_microseconds(self.effective_at),
            "expected_revision": self.expected_revision,
            "lines": [line.canonical_value() for line in self.lines],
            "transaction_id": str(self.transaction_id),
        }


UnitOfWorkFactory = Callable[[], UnitOfWork]


def execute_assign_reporting_lines(
    command: AssignReportingLinesCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
    authorize: Authorize = authorize_journal_write,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(command) is not AssignReportingLinesCommand:
        raise IdempotencyValidationError(
            "command must be an AssignReportingLinesCommand"
        )
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError("unexpected reporting assignment command")
        return _build_assign_plan(command, uow, locked_head, actor=actor)

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


def _load_reporting_target(
    uow: UnitOfWork,
    *,
    book_id: UUID,
    transaction_id: UUID,
) -> JournalTransactionRecord:
    transaction = uow.session.execute(
        select(JournalTransactionRecord)
        .where(
            JournalTransactionRecord.book_id == book_id,
            JournalTransactionRecord.transaction_id == transaction_id,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if transaction is None:
        raise TransactionNotFound("journal transaction not found in requested Book")
    return transaction


def _build_assign_plan(
    command: AssignReportingLinesCommand,
    uow: UnitOfWork,
    locked_head: LockedBookHead,
    *,
    actor: CommandActor,
) -> LedgerWritePlan:
    if locked_head.book_id != command.book_id:
        raise IdempotencyValidationError("locked Book does not match command")
    transaction = _load_reporting_target(
        uow,
        book_id=command.book_id,
        transaction_id=command.transaction_id,
    )
    _validate_lines(command, uow)
    revision = command.expected_revision + 1
    payload = ReportingLinesAssigned(
        transaction_id=command.transaction_id,
        classification_revision=revision,
        lines=tuple(
            ReportingLine(
                line_id=line.line_id,
                line_version_id=line.line_version_id,
                catalog_id=line.catalog_id,
                position=position,
                asset_code=line.asset_code,
                units=line.units,
                line_kind=line.line_kind,
                dimension=line.dimension,
                dimension_id=line.dimension_id,
                description_ref=line.description_ref,
            )
            for position, line in enumerate(command.lines)
        ),
    )
    pending = PendingEvent(
        event_id=uuid5(_ASSIGN_EVENT_NAMESPACE, str(command.command_id)),
        stream_type="reporting_lines",
        stream_id=command.transaction_id,
        payload=payload,
        command_id=command.command_id,
        actor_subject_id=actor.subject_id,
        correlation_id=command.command_id,
        causation_event_id=transaction.source_event_id,
        effective_at=command.effective_at,
    )
    return LedgerWritePlan(
        expected_stream_versions={
            ("reporting_lines", command.transaction_id): command.expected_revision
        },
        events=(pending,),
        response_schema_version=1,
        status_code=201,
        body={
            "transaction_id": str(command.transaction_id),
            "classification_revision": revision,
            "as_of_book_position": locked_head.last_position + 1,
        },
    )


def _validate_lines(command: AssignReportingLinesCommand, uow: UnitOfWork) -> None:
    catalogs = CatalogRepository(uow.session)
    for line in command.lines:
        if (
            line.dimension is not ReportingDimension.CATEGORY
            or line.dimension_id is None
        ):
            raise UnsupportedReportingDimension(
                "reporting dimension has no immutable V2 catalog contract"
            )
        catalogs.get_category_version(
            command.book_id,
            line.dimension_id,
            line.catalog_id,
        )

    postings = tuple(
        uow.session.scalars(
            select(JournalPostingRecord).where(
                JournalPostingRecord.book_id == command.book_id,
                JournalPostingRecord.transaction_id == command.transaction_id,
            )
        )
    )
    debit_by_asset: dict[str, int] = {}
    credit_by_asset: dict[str, int] = {}
    for posting in postings:
        target = debit_by_asset if posting.side == "debit" else credit_by_asset
        target[posting.asset_code] = target.get(posting.asset_code, 0) + int(
            posting.units
        )
    if not postings or debit_by_asset != credit_by_asset:
        raise ReportingAllocationExceeded(
            "reporting target postings are unavailable or unbalanced"
        )
    allocated_by_asset: dict[str, int] = {}
    for line in command.lines:
        allocated_by_asset[line.asset_code] = allocated_by_asset.get(
            line.asset_code, 0
        ) + int(line.units)
    if any(
        allocated > debit_by_asset.get(asset_code, 0)
        for asset_code, allocated in allocated_by_asset.items()
    ):
        raise ReportingAllocationExceeded(
            "reporting allocation exceeds the transaction amount"
        )


__all__ = [
    "AssignReportingLinesCommand",
    "ReportingAllocationExceeded",
    "ReportingLineInput",
    "UnsupportedReportingDimension",
    "execute_assign_reporting_lines",
]
