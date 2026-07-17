from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
from types import MappingProxyType
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.application.privacy.service import (
    ProtectedContentConflict,
    ProtectedContentService,
)
from track_anywhere.infrastructure.crypto import ProtectedContentCipher
from track_anywhere.infrastructure.db.models.async_projection import (
    ProjectionCheckpointRecord,
    ProjectionDirtyPeriodRecord,
    ProjectionFailureRecord,
    ProjectionGenerationRecord,
)
from track_anywhere.infrastructure.db.models.credit_cards import (
    CreditCardTransactionRecord,
)
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    EventStreamHeadRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.monthly_summary import (
    MonthlyCategorySummaryRecord,
)
from track_anywhere.infrastructure.db.models.privacy import (
    ImportArchiveManifestRecord,
    ProtectedDescriptionSidecarRecord,
)
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
    ReportingLineRecord,
    SynchronousProjectionAppliedEventRecord,
    TransactionExternalReferenceRecord,
    TransactionReversalRecord,
)
from track_anywhere.infrastructure.db.repositories.privacy import (
    ProtectedContentRepository,
)
from track_anywhere.serialization.canonical_json import (
    EventHashEnvelope,
    JSONValue,
    event_hash,
)
from track_anywhere.serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from track_anywhere.verification import (
    LedgerReadbackFacts,
    hash_verification_rows,
    read_ledger_readback_facts,
)

from .reference_reducer import (
    ReferenceLedgerFacts,
    SourceLedgerFacts,
    reduce_frozen_source_rows,
)
from .credit_card_review import CreditCardSemanticReview
from .extract import FrozenSourceRows, FrozenTableRows


_DESCRIPTION_AGGREGATE_DOMAIN = (
    b"track-anywhere:frozen-v1:description-aggregate:sha256:v1\0"
)
_SOURCE_REFERENCE_TABLES = frozenset(
    {
        "accounts",
        "assets",
        "categories",
        "category_versions",
        "ledger_books",
        "postings",
        "transaction_lines",
        "transactions",
    }
)
_LEDGER_HASH_KEYS = frozenset(
    {
        "account_balances_semantic",
        "accounts",
        "assets",
        "async_projection",
        "balances",
        "cards",
        "categories",
        "event_order",
        "event_payloads",
        "events",
        "external_references",
        "journal",
        "journal_postings",
        "journal_transactions",
        "reporting",
        "reversal_semantic",
        "reversals",
        "synchronous_projection",
        "usdt_postings",
    }
)
_PROTECTED_HASH_KEYS = frozenset(
    {
        "archive_metadata",
        "archive_plaintext",
        "archive_seal",
        "description_aggregate",
        "terminal",
    }
)
_COUNT_KEYS = frozenset(
    {
        "accounts",
        "archives",
        "assets",
        "async_projection_rows",
        "categories",
        "category_versions",
        "credit_card_transactions",
        "descriptions",
        "journal_postings",
        "journal_transactions",
        "ledger_events",
        "quarantine",
        "reporting_lines",
        "reversals",
        "synchronous_projection_applied_events",
    }
)
_REPLAY_OCCUPANCY_MODELS = (
    LedgerEventRecord,
    EventStreamHeadRecord,
    JournalTransactionRecord,
    JournalPostingRecord,
    TransactionExternalReferenceRecord,
    AccountBalanceRecord,
    TransactionReversalRecord,
    ReportingLineRecord,
    CreditCardTransactionRecord,
    SynchronousProjectionAppliedEventRecord,
    ProjectionCheckpointRecord,
    ProjectionGenerationRecord,
    ProjectionDirtyPeriodRecord,
    ProjectionFailureRecord,
    MonthlyCategorySummaryRecord,
)


class FrozenHistoryVerificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FrozenHistoryReplayResult:
    event_count: int
    terminal_hash: str


@dataclass(frozen=True, slots=True)
class FrozenHistoryObservation:
    ledger: LedgerReadbackFacts
    additional_counts: Mapping[str, int]
    description_aggregate_sha256: str
    archive_plaintext_sha256: str
    archive_metadata_hash: str
    archive_seal: str
    archive_verified: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "additional_counts",
            MappingProxyType(dict(self.additional_counts)),
        )


@dataclass(frozen=True, slots=True)
class FrozenHistoryVerificationReport:
    status: str
    issues: tuple[str, ...]
    counts: Mapping[str, int]
    hashes: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))
        object.__setattr__(self, "hashes", MappingProxyType(dict(self.hashes)))

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "issues": list(self.issues),
            "counts": dict(sorted(self.counts.items())),
            "hashes": dict(sorted(self.hashes.items())),
        }


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _primitive_source_value(value: object) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("frozen source primitive keys are invalid")
        return {
            key: _primitive_source_value(item) for key, item in sorted(value.items())
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_primitive_source_value(item) for item in value]
    raise TypeError("frozen source primitive value is invalid")


def build_source_reference_input(
    *,
    source: FrozenSourceRows,
    review: CreditCardSemanticReview,
    target_book_id: UUID,
) -> dict[str, object]:
    """Build a privacy-bearing, process-local reducer input.

    The returned detached mapping contains raw V1 facts. It must be reduced in the
    same call frame, must never be represented, logged, reported, serialized, or
    sent across a process boundary, and should be cleared immediately afterward.
    Prefer :func:`reduce_approved_source_reference` outside focused unit tests.
    """

    if (
        type(source) is not FrozenSourceRows
        or type(review) is not CreditCardSemanticReview
        or type(target_book_id) is not UUID
        or source.manifest.snapshot_id != review.snapshot_id
    ):
        raise TypeError("source reference input arguments are invalid")
    if not _SOURCE_REFERENCE_TABLES.issubset(source.tables):
        raise TypeError("source reference input table coverage is invalid")

    tables: dict[str, object] = {}
    for table_name in sorted(_SOURCE_REFERENCE_TABLES):
        table = source.tables[table_name]
        if type(table) is not FrozenTableRows or table.table != table_name:
            raise TypeError("source reference input table is invalid")
        tables[table_name] = [_primitive_source_value(dict(row)) for row in table.rows]

    posting_decisions = [
        {
            "source_posting_id": posting.source_posting_id,
            "target_account_id": posting.target_account_id,
            "target_side": posting.target_side,
        }
        for transaction in review.transactions
        for posting in transaction.postings
    ]
    primitive = {
        "snapshot_id": source.manifest.snapshot_id,
        "target_book_id": str(target_book_id),
        "tables": tables,
        "review": {
            "exact_reversal_transaction_ids": [
                transaction.source_transaction_id
                for transaction in review.transactions
                if transaction.post_import_action == "exact_reversal"
            ],
            "expected_card_balances": [
                {
                    "source_account_id": balance.source_account_id,
                    "asset_code": balance.asset_code,
                    "natural_units": balance.natural_units,
                }
                for balance in review.expected_balances
            ],
            "posting_decisions": posting_decisions,
            "retired_alias_account_ids": [
                account.source_account_id for account in review.accounts
            ],
        },
    }
    detached = _primitive_source_value(primitive)
    if type(detached) is not dict:
        raise TypeError("source reference input is invalid")
    return detached


def reduce_approved_source_reference(
    *,
    source: FrozenSourceRows,
    review: CreditCardSemanticReview,
    target_book_id: UUID,
) -> SourceLedgerFacts:
    """Immediately reduce approved privacy-bearing source data to secret-free facts."""

    primitive = build_source_reference_input(
        source=source,
        review=review,
        target_book_id=target_book_id,
    )
    try:
        return reduce_frozen_source_rows(primitive)
    finally:
        primitive.clear()


def _authorized_description_aggregate(
    session: Session,
    *,
    reference: ReferenceLedgerFacts,
    service: ProtectedContentService,
    repository: ProtectedContentRepository,
) -> str:
    try:
        identities = tuple(UUID(value) for value in reference.description_ids)
        snapshots = repository.get_active_batch(
            session,
            book_id=UUID(reference.book_id),
            sidecar_ids=identities,
        )
        if len(snapshots) != len(identities):
            raise FrozenHistoryVerificationError("description_readback_failed")
        digest = hashlib.sha256()
        digest.update(_DESCRIPTION_AGGREGATE_DOMAIN)
        for sidecar_id in identities:
            plaintext = service.decrypt_active(
                snapshots[sidecar_id],
                expected_kind="transaction_description",
            )
            encoded_identity = str(sidecar_id).encode("ascii")
            digest.update(len(encoded_identity).to_bytes(2, "big"))
            digest.update(encoded_identity)
            digest.update(len(plaintext).to_bytes(8, "big"))
            digest.update(plaintext)
            plaintext = b""
        return digest.hexdigest()
    except (KeyError, TypeError, ValueError, ProtectedContentConflict):
        raise FrozenHistoryVerificationError("description_readback_failed") from None


def read_frozen_history_observation(
    session: Session,
    *,
    reference: ReferenceLedgerFacts,
    cipher: ProtectedContentCipher,
) -> FrozenHistoryObservation:
    """Perform authorized ephemeral protected-content and ledger read-back."""

    if (
        not isinstance(session, Session)
        or type(reference) is not ReferenceLedgerFacts
        or not isinstance(cipher, ProtectedContentCipher)
    ):
        raise TypeError("frozen-history read-back arguments are invalid")
    book_id = UUID(reference.book_id)
    ledger = read_ledger_readback_facts(session, book_id)
    repository = ProtectedContentRepository()
    service = ProtectedContentService(cipher=cipher, repository=repository)
    description_aggregate = _authorized_description_aggregate(
        session,
        reference=reference,
        service=service,
        repository=repository,
    )
    manifest = repository.get_archive_manifest(
        session,
        book_id=book_id,
        archive_id=UUID(reference.archive_id),
    )
    if manifest is None:
        raise FrozenHistoryVerificationError("archive_readback_failed")
    try:
        archive_plaintext = service.verify_archive_manifest(
            manifest,
            include_content=True,
        )
        if type(archive_plaintext) is not bytes:
            raise FrozenHistoryVerificationError("archive_readback_failed")
        archive_plaintext_sha256 = hashlib.sha256(archive_plaintext).hexdigest()
        archive_plaintext = b""
    except (TypeError, ValueError, ProtectedContentConflict):
        raise FrozenHistoryVerificationError("archive_readback_failed") from None

    archive_metadata_row: dict[str, JSONValue] = {
        "archive_id": str(manifest.archive_id),
        "archive_plaintext_sha256": archive_plaintext_sha256,
        "card_review_hash": manifest.card_review_hash.hex(),
        "plan_hash": manifest.plan_hash.hex(),
        "record_counts": dict(manifest.record_counts),
        "source_dump_hash": manifest.source_dump_hash.hex(),
        "source_manifest_hash": manifest.source_manifest_hash.hex(),
    }
    description_count = int(
        session.scalar(
            select(func.count())
            .select_from(ProtectedDescriptionSidecarRecord)
            .where(
                ProtectedDescriptionSidecarRecord.book_id == book_id,
                ProtectedDescriptionSidecarRecord.kind == "transaction_description",
            )
        )
        or 0
    )
    archive_count = int(
        session.scalar(
            select(func.count())
            .select_from(ImportArchiveManifestRecord)
            .where(ImportArchiveManifestRecord.book_id == book_id)
        )
        or 0
    )
    return FrozenHistoryObservation(
        ledger=ledger,
        additional_counts={
            "archives": archive_count,
            "descriptions": description_count,
            "quarantine": 0,
        },
        description_aggregate_sha256=description_aggregate,
        archive_plaintext_sha256=archive_plaintext_sha256,
        archive_metadata_hash=hash_verification_rows([archive_metadata_row]),
        archive_seal=manifest.seal.hex(),
        archive_verified=True,
    )


def _require_empty_cold_replay_target(target: Session, book_id: UUID) -> None:
    head = target.get(BookEventHeadRecord, book_id)
    if head is None or head.last_position != 0 or head.last_hash != bytes(32):
        raise FrozenHistoryVerificationError("cold_replay_target_not_empty")
    for model in _REPLAY_OCCUPANCY_MODELS:
        occupied = target.scalar(
            select(func.count()).select_from(model).where(model.book_id == book_id)
        )
        if int(occupied or 0) != 0:
            raise FrozenHistoryVerificationError("cold_replay_target_not_empty")


def replay_frozen_history_events(
    source: Session,
    target: Session,
    *,
    book_id: UUID,
) -> FrozenHistoryReplayResult:
    """Replay immutable stored events through the supported ledger write boundary."""

    if (
        not isinstance(source, Session)
        or not isinstance(target, Session)
        or type(book_id) is not UUID
        or target.get_transaction() is None
    ):
        raise TypeError("cold replay arguments are invalid")
    source_head = source.get(BookEventHeadRecord, book_id)
    stored_events = tuple(
        source.scalars(
            select(LedgerEventRecord)
            .where(LedgerEventRecord.book_id == book_id)
            .order_by(LedgerEventRecord.book_position)
        )
    )
    if (
        source_head is None
        or not stored_events
        or source_head.last_position != len(stored_events)
        or source_head.last_hash != stored_events[-1].event_hash
    ):
        raise FrozenHistoryVerificationError("cold_replay_source_invalid")

    _require_empty_cold_replay_target(target, book_id)

    versions: dict[tuple[str, UUID], int] = {}
    pending: list[PendingEvent] = []
    previous_hash = bytes(32)
    try:
        for position, stored in enumerate(stored_events, start=1):
            stream_key = (stored.stream_type, stored.stream_id)
            next_version = versions.get(stream_key, 0) + 1
            if (
                stored.book_position != position
                or stored.stream_version != next_version
                or stored.previous_hash != previous_hash
            ):
                raise FrozenHistoryVerificationError("cold_replay_source_invalid")
            payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
                stored.event_type,
                stored.event_schema_version,
                stored.payload,
            )
            computed_hash = event_hash(
                EventHashEnvelope(
                    event_id=stored.event_id,
                    book_id=stored.book_id,
                    book_position=stored.book_position,
                    global_sequence=stored.global_sequence,
                    recorded_at=stored.recorded_at,
                    stream_type=stored.stream_type,
                    stream_id=stored.stream_id,
                    stream_version=stored.stream_version,
                    event_type=stored.event_type,
                    event_schema_version=stored.event_schema_version,
                    command_id=stored.command_id,
                    actor_subject_id=stored.actor_subject_id,
                    correlation_id=stored.correlation_id,
                    causation_event_id=stored.causation_event_id,
                    effective_at=stored.effective_at,
                    previous_hash=previous_hash,
                ),
                stored.payload,
            )
            if computed_hash != stored.event_hash:
                raise FrozenHistoryVerificationError("cold_replay_source_invalid")
            versions[stream_key] = next_version
            pending.append(
                PendingEvent(
                    event_id=stored.event_id,
                    stream_type=stored.stream_type,
                    stream_id=stored.stream_id,
                    payload=payload,
                    command_id=stored.command_id,
                    actor_subject_id=stored.actor_subject_id,
                    correlation_id=stored.correlation_id,
                    causation_event_id=stored.causation_event_id,
                    effective_at=stored.effective_at,
                )
            )
            previous_hash = computed_hash
    except FrozenHistoryVerificationError:
        raise
    except (KeyError, TypeError, ValueError):
        raise FrozenHistoryVerificationError("cold_replay_source_invalid") from None
    if source_head.last_hash != previous_hash:
        raise FrozenHistoryVerificationError("cold_replay_source_invalid")

    try:
        with target.begin_nested():
            committer = LedgerCommitter()
            locked = committer.execute_under_book_lock(target, book_id)
            if locked.last_position != 0 or locked.last_hash != bytes(32):
                raise FrozenHistoryVerificationError("cold_replay_target_not_empty")
            appended = committer.append_and_project(
                target,
                locked_head=locked,
                expected_stream_versions={stream_key: 0 for stream_key in versions},
                events=tuple(pending),
            )
            if (
                appended.event_ids != tuple(stored.event_id for stored in stored_events)
                or appended.terminal_hash != source_head.last_hash
            ):
                raise FrozenHistoryVerificationError("cold_replay_target_mismatch")
    finally:
        capabilities = target.info.get("track_anywhere_v2_book_lock_capabilities")
        if isinstance(capabilities, dict):
            capabilities.pop(book_id, None)
    return FrozenHistoryReplayResult(
        event_count=len(appended.event_ids),
        terminal_hash=appended.terminal_hash.hex(),
    )


def verify_frozen_history(
    reference: ReferenceLedgerFacts,
    observation: FrozenHistoryObservation,
) -> FrozenHistoryVerificationReport:
    if type(reference) is not ReferenceLedgerFacts:
        raise TypeError("reference facts have an invalid runtime type")
    if type(observation) is not FrozenHistoryObservation:
        raise TypeError("observed facts have an invalid runtime type")
    if set(reference.hashes) - _LEDGER_HASH_KEYS or any(
        not _is_sha256(value) for value in reference.hashes.values()
    ):
        raise FrozenHistoryVerificationError("reference_digest_invalid")

    issues: list[str] = []
    observed_counts = dict(observation.ledger.counts)
    observed_counts.update(observation.additional_counts)
    if set(reference.counts) - _COUNT_KEYS or set(observed_counts) - _COUNT_KEYS:
        issues.append("unexpected_count")
    counts: dict[str, int] = {}
    for key in sorted(_COUNT_KEYS):
        expected = reference.counts.get(key)
        observed = observed_counts.get(key)
        expected_valid = type(expected) is int and expected >= 0
        observed_valid = type(observed) is int and observed >= 0
        if not expected_valid or not observed_valid:
            issues.append("invalid_count")
            if observed_valid:
                counts[key] = observed
            continue
        counts[key] = observed
        if observed != expected:
            issues.append(f"count_mismatch:{key}")

    if observation.ledger.book_id != reference.book_id:
        issues.append("book_id_mismatch")
    if observation.ledger.terminal_position != reference.terminal_position:
        issues.append("terminal_position_mismatch")
    if observation.ledger.terminal_hash != reference.terminal_hash:
        issues.append("terminal_hash_mismatch")
    if observation.ledger.async_checkpoint_position != reference.terminal_position:
        issues.append("async_checkpoint_mismatch")
    if observation.ledger.unresolved_projection_failures != 0:
        issues.append("unresolved_projection_failures")

    observed_ledger_hashes = observation.ledger.hashes
    if set(observed_ledger_hashes) - set(reference.hashes):
        issues.append("unexpected_digest")
    for key in sorted(reference.hashes):
        observed = observed_ledger_hashes.get(key)
        if not _is_sha256(observed):
            issues.append("invalid_digest")
        if observed != reference.hashes[key]:
            issues.append(f"{key}_digest_mismatch")

    if (
        observation.description_aggregate_sha256
        != reference.description_aggregate_sha256
    ):
        issues.append("description_aggregate_mismatch")
    if observation.archive_plaintext_sha256 != reference.archive_plaintext_sha256:
        issues.append("archive_plaintext_digest_mismatch")
    if observation.archive_metadata_hash != reference.archive_metadata_hash:
        issues.append("archive_metadata_mismatch")
    if not observation.archive_verified or not _is_sha256(observation.archive_seal):
        issues.append("archive_seal_invalid")
    protected_values = {
        "archive_metadata": observation.archive_metadata_hash,
        "archive_plaintext": observation.archive_plaintext_sha256,
        "archive_seal": observation.archive_seal,
        "description_aggregate": observation.description_aggregate_sha256,
        "terminal": observation.ledger.terminal_hash,
    }
    if any(not _is_sha256(value) for value in protected_values.values()):
        issues.append("invalid_digest")

    hashes = {
        key: value
        for key, value in observed_ledger_hashes.items()
        if key in reference.hashes and key in _LEDGER_HASH_KEYS and _is_sha256(value)
    }
    hashes.update(
        {
            key: value
            for key, value in protected_values.items()
            if key in _PROTECTED_HASH_KEYS and _is_sha256(value)
        }
    )
    return FrozenHistoryVerificationReport(
        status="PASS" if not issues else "FAIL",
        issues=tuple(sorted(set(issues))),
        counts=counts,
        hashes=hashes,
    )


__all__ = [
    "FrozenHistoryObservation",
    "FrozenHistoryReplayResult",
    "FrozenHistoryVerificationError",
    "FrozenHistoryVerificationReport",
    "build_source_reference_input",
    "read_frozen_history_observation",
    "replay_frozen_history_events",
    "reduce_approved_source_reference",
    "verify_frozen_history",
]
