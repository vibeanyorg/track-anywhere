from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import hmac
import re
from typing import Final
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from ...infrastructure.crypto import ProtectedContentCipher
from ...infrastructure.db.models.catalog import AccountRecord, BookRecord
from ...infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    LedgerEventRecord,
)
from ...infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
)
from ...infrastructure.db.repositories.frozen_import import (
    FrozenImportCatalogApplyResult,
    FrozenImportCatalogRepository,
    ProcessingReceiptIdentity,
)
from ...infrastructure.db.repositories.privacy import ProtectedContentRepository
from ...serialization.canonical_json import JSONValue
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from ..command_bus import execute_financial
from ..event_batch import AppendBatchResult, PendingEvent
from ..idempotency import AuthorizationScope, CommandActor, CommandOutcome
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..privacy.service import (
    ImportArchiveProposal,
    ProtectedContentService,
)
from ..unit_of_work import UnitOfWork
from .contracts import (
    FROZEN_IMPORT_ACTOR_SUBJECT_ID,
    FrozenFinancialHistoryPlan,
    canonical_plan_bytes,
    plan_summary,
)


FROZEN_IMPORT_OPERATION: Final = "imports.frozen-v1-financial-history"
FROZEN_IMPORT_TARGET_BOOK_ID: Final = UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")
FROZEN_IMPORT_SOURCE_DUMP_HASH: Final = (
    "a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e"
)
FROZEN_IMPORT_MANIFEST_HASH: Final = (
    "f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f"
)
FROZEN_IMPORT_CARD_REVIEW_HASH: Final = (
    "237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430"
)

_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_COUNTS: Final[dict[str, int]] = {
    "accounts": 121,
    "archives": 1,
    "assets": 20,
    "categories": 37,
    "category_versions": 37,
    "descriptions": 138,
    "events": 176,
    "journal_transactions": 138,
    "postings": 290,
    "quarantine": 0,
    "reporting_assignments": 38,
    "reporting_lines": 38,
    "reversals": 8,
}


class FrozenFinancialHistoryImportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ImportFrozenFinancialHistoryCommand:
    book_id: UUID
    command_id: UUID
    source_dump_hash: str
    manifest_hash: str
    card_review_hash: str
    plan_hash: str
    expected_terminal_hash: str
    counts: tuple[tuple[str, int], ...]
    operation: str = FROZEN_IMPORT_OPERATION

    def __post_init__(self) -> None:
        if (
            type(self.book_id) is not UUID
            or self.book_id != FROZEN_IMPORT_TARGET_BOOK_ID
            or type(self.command_id) is not UUID
            or self.operation != FROZEN_IMPORT_OPERATION
            or self.source_dump_hash != FROZEN_IMPORT_SOURCE_DUMP_HASH
            or self.manifest_hash != FROZEN_IMPORT_MANIFEST_HASH
            or self.card_review_hash != FROZEN_IMPORT_CARD_REVIEW_HASH
            or any(
                type(value) is not str or _HEX_SHA256.fullmatch(value) is None
                for value in (self.plan_hash, self.expected_terminal_hash)
            )
            or dict(self.counts) != _EXPECTED_COUNTS
            or tuple(key for key, _value in self.counts)
            != tuple(sorted(_EXPECTED_COUNTS))
        ):
            raise FrozenFinancialHistoryImportError(
                "frozen financial history import fixed contract is invalid"
            )

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "source_dump_hash": self.source_dump_hash,
            "manifest_hash": self.manifest_hash,
            "card_review_hash": self.card_review_hash,
            "plan_hash": self.plan_hash,
            "expected_terminal_hash": self.expected_terminal_hash,
            "counts": dict(self.counts),
        }


def build_frozen_financial_history_command(
    plan: FrozenFinancialHistoryPlan,
    *,
    expected_plan_hash: str,
) -> ImportFrozenFinancialHistoryCommand:
    _validated, command = _validate_and_build(
        plan,
        expected_plan_hash=expected_plan_hash,
    )
    return command


def _validate_and_build(
    plan: FrozenFinancialHistoryPlan,
    *,
    expected_plan_hash: str,
) -> tuple[FrozenFinancialHistoryPlan, ImportFrozenFinancialHistoryCommand]:
    try:
        if type(plan) is not FrozenFinancialHistoryPlan:
            raise ValueError
        validated = FrozenFinancialHistoryPlan.model_validate(
            plan.model_dump(mode="python", round_trip=True),
            strict=True,
        )
        actual_plan_hash = hashlib.sha256(canonical_plan_bytes(validated)).hexdigest()
        summary = plan_summary(validated)
        counts = summary.get("counts")
        command_ids = {event.command_id for event in validated.events}
        actor_ids = {event.actor_subject_id for event in validated.events}
        if (
            type(expected_plan_hash) is not str
            or _HEX_SHA256.fullmatch(expected_plan_hash) is None
            or not hmac.compare_digest(actual_plan_hash, expected_plan_hash)
            or validated.target_book_id != FROZEN_IMPORT_TARGET_BOOK_ID
            or validated.source_dump_hash != FROZEN_IMPORT_SOURCE_DUMP_HASH
            or validated.manifest_hash != FROZEN_IMPORT_MANIFEST_HASH
            or validated.card_review_hash != FROZEN_IMPORT_CARD_REVIEW_HASH
            or validated.quarantine_count != 0
            or type(counts) is not dict
            or counts != _EXPECTED_COUNTS
            or len(command_ids) != 1
            or actor_ids != {FROZEN_IMPORT_ACTOR_SUBJECT_ID}
            or validated.expected_terminal_hash != validated.events[-1].event_hash
        ):
            raise ValueError
        command_id = next(iter(command_ids))
        command = ImportFrozenFinancialHistoryCommand(
            book_id=validated.target_book_id,
            command_id=command_id,
            source_dump_hash=validated.source_dump_hash,
            manifest_hash=validated.manifest_hash,
            card_review_hash=validated.card_review_hash,
            plan_hash=actual_plan_hash,
            expected_terminal_hash=validated.expected_terminal_hash,
            counts=tuple(sorted(counts.items())),
        )
        return validated, command
    except (TypeError, ValueError):
        raise FrozenFinancialHistoryImportError(
            "frozen financial history import fixed contract is invalid"
        ) from None


def import_frozen_financial_history(
    plan: FrozenFinancialHistoryPlan,
    *,
    expected_plan_hash: str,
    raw_key: str,
    actor: CommandActor,
    uow_factory: Callable[[], UnitOfWork],
    protected_content_cipher: ProtectedContentCipher,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    validated_plan, command = _validate_and_build(
        plan,
        expected_plan_hash=expected_plan_hash,
    )
    if (
        type(actor) is not CommandActor
        or actor.subject_id != FROZEN_IMPORT_ACTOR_SUBJECT_ID
        or not callable(uow_factory)
        or not isinstance(protected_content_cipher, ProtectedContentCipher)
    ):
        raise FrozenFinancialHistoryImportError(
            "frozen financial history import fixed contract is invalid"
        )
    content_service = ProtectedContentService(
        cipher=protected_content_cipher,
        repository=ProtectedContentRepository(),
    )
    handler = _build_handler(
        validated_plan,
        command=command,
        content_service=content_service,
    )

    def authorize(
        session: Session,
        requested_actor: CommandActor,
        book_id: UUID,
        *,
        lock_membership: bool,
    ) -> AuthorizationScope:
        del lock_membership
        if (
            requested_actor != actor
            or requested_actor.subject_id != FROZEN_IMPORT_ACTOR_SUBJECT_ID
            or book_id != FROZEN_IMPORT_TARGET_BOOK_ID
            or session.scalar(
                select(BookRecord.book_id).where(BookRecord.book_id == book_id)
            )
            != book_id
        ):
            raise FrozenFinancialHistoryImportError(
                "frozen financial history import fixed contract is invalid"
            )
        return AuthorizationScope(
            book_id=book_id,
            actor_subject_id=requested_actor.subject_id,
            role="offline_import",
            scopes=("ledger:write",),
        )

    return execute_financial(
        command,
        raw_key=raw_key,
        actor=actor,
        authorize=authorize,
        handler=handler,
        uow_factory=uow_factory,
        ledger_committer=(
            LedgerCommitter() if ledger_committer is None else ledger_committer
        ),
        max_attempts=max_attempts,
    )


def _build_handler(
    plan: FrozenFinancialHistoryPlan,
    *,
    command: ImportFrozenFinancialHistoryCommand,
    content_service: ProtectedContentService,
):
    expected_command = command

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if (
            type(received) is not ImportFrozenFinancialHistoryCommand
            or received != expected_command
            or type(locked_head) is not LockedBookHead
            or locked_head.book_id != plan.target_book_id
            or locked_head.last_position != 0
            or not hmac.compare_digest(locked_head.last_hash, bytes(32))
        ):
            raise FrozenFinancialHistoryImportError(
                "frozen financial history import fixed contract is invalid"
            )

        catalog_result = FrozenImportCatalogRepository(uow.session).apply_exact_catalog(
            plan,
            current_receipt=ProcessingReceiptIdentity(
                actor_subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID,
                operation=expected_command.operation,
                command_id=expected_command.command_id,
            ),
        )
        for content in plan.descriptions:
            content_service.create_or_exact_verify(
                uow.session,
                book_id=plan.target_book_id,
                sidecar_id=content.sidecar_id,
                kind="transaction_description",
                canonical_plaintext=content.canonical_plaintext,
            )
        content_service.create_or_exact_verify_archive(
            uow.session,
            archive=ImportArchiveProposal(
                contract_version=1,
                book_id=plan.target_book_id,
                archive_id=plan.archive.sidecar_id,
                source_dump_hash=bytes.fromhex(plan.source_dump_hash),
                source_manifest_hash=bytes.fromhex(plan.manifest_hash),
                card_review_hash=bytes.fromhex(plan.card_review_hash),
                plan_hash=bytes.fromhex(expected_command.plan_hash),
                record_counts=plan.archive.record_counts,
                canonical_ndjson=plan.archive.canonical_plaintext,
            ),
        )

        events = tuple(_pending_event(event) for event in plan.events)
        expected_stream_versions = {
            (event.stream_type, event.stream_id): event.expected_stream_version
            for event in plan.events
        }
        return LedgerWritePlan(
            expected_stream_versions=expected_stream_versions,
            events=events,
            response_schema_version=1,
            status_code=201,
            body=_result_body(expected_command, catalog_result=catalog_result),
            post_projection_finalizer=_build_finalizer(
                plan,
                command=expected_command,
                content_service=content_service,
            ),
        )

    return handler


def _pending_event(planned) -> PendingEvent:
    return PendingEvent(
        event_id=planned.event_id,
        stream_type=planned.stream_type,
        stream_id=planned.stream_id,
        payload=planned.payload,
        command_id=planned.command_id,
        actor_subject_id=planned.actor_subject_id,
        correlation_id=planned.correlation_id,
        causation_event_id=planned.causation_event_id,
        effective_at=planned.effective_at,
    )


def _result_body(
    command: ImportFrozenFinancialHistoryCommand,
    *,
    catalog_result: FrozenImportCatalogApplyResult,
) -> dict[str, JSONValue]:
    return {
        "book_id": str(command.book_id),
        "plan_hash": command.plan_hash,
        "expected_terminal_hash": command.expected_terminal_hash,
        "counts": dict(command.counts),
        "inserted_counts": {
            "accounts": catalog_result.accounts_created,
            "archives": 1,
            "assets": catalog_result.assets_created,
            "categories": catalog_result.categories_created,
            "category_versions": catalog_result.category_versions_created,
            "credit_card_transactions": 0,
            "descriptions": 138,
            "events": 176,
            "journal_transactions": 138,
            "postings": 290,
            "quarantine": 0,
            "reporting_lines": 38,
            "reversals": 8,
        },
    }


def _build_finalizer(
    plan: FrozenFinancialHistoryPlan,
    *,
    command: ImportFrozenFinancialHistoryCommand,
    content_service: ProtectedContentService,
):
    planned_event_ids = tuple(event.event_id for event in plan.events)

    def finalize(session: Session, appended: AppendBatchResult) -> None:
        _verify_appended_batch(
            session,
            plan=plan,
            command=command,
            appended=appended,
            planned_event_ids=planned_event_ids,
        )
        alias = _verify_card_balances(session, plan=plan)
        alias.status = "closed"
        session.flush([alias])
        manifest = ProtectedContentRepository().get_archive_manifest(
            session,
            book_id=plan.target_book_id,
            archive_id=plan.archive.sidecar_id,
        )
        if (
            manifest is None
            or not hmac.compare_digest(
                manifest.source_dump_hash,
                bytes.fromhex(command.source_dump_hash),
            )
            or not hmac.compare_digest(
                manifest.source_manifest_hash,
                bytes.fromhex(command.manifest_hash),
            )
            or not hmac.compare_digest(
                manifest.card_review_hash,
                bytes.fromhex(command.card_review_hash),
            )
            or not hmac.compare_digest(
                manifest.plan_hash,
                bytes.fromhex(command.plan_hash),
            )
            or content_service.verify_archive_manifest(
                manifest,
                include_content=False,
            )
            is not None
        ):
            raise FrozenFinancialHistoryImportError(
                "frozen financial history import finalization failed"
            )

    return finalize


def _verify_appended_batch(
    session: Session,
    *,
    plan: FrozenFinancialHistoryPlan,
    command: ImportFrozenFinancialHistoryCommand,
    appended: AppendBatchResult,
    planned_event_ids: tuple[UUID, ...],
) -> None:
    expected_hash = bytes.fromhex(command.expected_terminal_hash)
    if (
        type(appended) is not AppendBatchResult
        or appended.positions != range(1, 177)
        or appended.event_ids != planned_event_ids
        or not hmac.compare_digest(appended.terminal_hash, expected_hash)
    ):
        raise FrozenFinancialHistoryImportError(
            "frozen financial history import finalization failed"
        )
    head = session.get(BookEventHeadRecord, plan.target_book_id)
    stored_events = tuple(
        session.scalars(
            select(LedgerEventRecord)
            .where(LedgerEventRecord.book_id == plan.target_book_id)
            .order_by(LedgerEventRecord.book_position)
        )
    )
    if (
        head is None
        or head.last_position != 176
        or not hmac.compare_digest(head.last_hash, expected_hash)
        or len(stored_events) != 176
    ):
        raise FrozenFinancialHistoryImportError(
            "frozen financial history import finalization failed"
        )
    for planned, stored in zip(plan.events, stored_events, strict=True):
        if (
            stored.event_id != planned.event_id
            or stored.book_id != plan.target_book_id
            or stored.book_position != planned.book_position
            or stored.stream_type != planned.stream_type
            or stored.stream_id != planned.stream_id
            or stored.stream_version != planned.stream_version
            or stored.event_type != planned.event_type
            or stored.event_schema_version != planned.event_schema_version
            or stored.command_id != planned.command_id
            or stored.actor_subject_id != planned.actor_subject_id
            or stored.correlation_id != planned.correlation_id
            or stored.causation_event_id != planned.causation_event_id
            or stored.effective_at != planned.effective_at
            or stored.payload
            != PRODUCTION_EVENT_REGISTRY.dump_registered(planned.payload)
            or not hmac.compare_digest(
                stored.previous_hash,
                bytes.fromhex(planned.previous_hash),
            )
            or not hmac.compare_digest(
                stored.event_hash,
                bytes.fromhex(planned.event_hash),
            )
        ):
            raise FrozenFinancialHistoryImportError(
                "frozen financial history import finalization failed"
            )


def _verify_card_balances(
    session: Session,
    *,
    plan: FrozenFinancialHistoryPlan,
) -> AccountRecord:
    cards = tuple(
        account for account in plan.accounts if account.account_subtype == "credit_card"
    )
    alias_plan = next(
        (account for account in cards if account.close_after_import),
        None,
    )
    if len(cards) != 5 or alias_plan is None:
        raise FrozenFinancialHistoryImportError(
            "frozen financial history import finalization failed"
        )
    alias_record: AccountRecord | None = None
    for card in cards:
        account = session.execute(
            select(AccountRecord)
            .where(
                AccountRecord.book_id == plan.target_book_id,
                AccountRecord.account_id == card.account_id,
                AccountRecord.asset_code == card.asset_code,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        projection = session.get(
            AccountBalanceRecord,
            (plan.target_book_id, card.account_id, card.asset_code),
        )
        reference_units, latest_position = session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                JournalPostingRecord.side == "debit",
                                JournalPostingRecord.units,
                            ),
                            else_=-JournalPostingRecord.units,
                        )
                    ),
                    0,
                ),
                func.max(JournalTransactionRecord.source_position),
            )
            .join(
                JournalTransactionRecord,
                and_(
                    JournalTransactionRecord.book_id == JournalPostingRecord.book_id,
                    JournalTransactionRecord.transaction_id
                    == JournalPostingRecord.transaction_id,
                ),
            )
            .where(
                JournalPostingRecord.book_id == plan.target_book_id,
                JournalPostingRecord.account_id == card.account_id,
                JournalPostingRecord.asset_code == card.asset_code,
            )
        ).one()
        reference = int(reference_units or 0)
        projected = 0 if projection is None else int(projection.balance_units)
        expected_raw = -card.expected_natural_units
        if (
            account is None
            or account.status != "active"
            or account.account_type != "liability"
            or (latest_position is not None and projection is None)
            or (
                projection is not None
                and latest_position is not None
                and projection.as_of_position < int(latest_position)
            )
            or reference != expected_raw
            or projected != expected_raw
        ):
            raise FrozenFinancialHistoryImportError(
                "frozen financial history import finalization failed"
            )
        if card.account_id == alias_plan.account_id:
            if reference != 0 or projected != 0:
                raise FrozenFinancialHistoryImportError(
                    "frozen financial history import finalization failed"
                )
            alias_record = account
    if alias_record is None:
        raise FrozenFinancialHistoryImportError(
            "frozen financial history import finalization failed"
        )
    return alias_record


__all__ = [
    "FROZEN_IMPORT_CARD_REVIEW_HASH",
    "FROZEN_IMPORT_MANIFEST_HASH",
    "FROZEN_IMPORT_OPERATION",
    "FROZEN_IMPORT_SOURCE_DUMP_HASH",
    "FROZEN_IMPORT_TARGET_BOOK_ID",
    "FrozenFinancialHistoryImportError",
    "ImportFrozenFinancialHistoryCommand",
    "build_frozen_financial_history_command",
    "import_frozen_financial_history",
]
