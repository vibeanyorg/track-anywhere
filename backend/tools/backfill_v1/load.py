from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from track_anywhere.infrastructure.db.models.backfill import (
    BackfillCheckpointRecord,
    BackfillQuarantineRecord,
    BackfillSealRecord,
    BackfillSourceReceiptRecord,
)

from .manifest import canonical_json_bytes


class BackfillChangedSourceError(RuntimeError):
    pass


class BackfillSealBlocked(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceLoadItem:
    source_table: str
    source_primary_key: str
    canonical_source_key: str
    source_hash: bytes
    book_id: UUID | None
    target_entity_id: UUID | None
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        for field_name in (
            "source_table",
            "source_primary_key",
            "canonical_source_key",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not value:
                raise ValueError(f"{field_name} must be nonblank")
        if type(self.source_hash) is not bytes or len(self.source_hash) != 32:
            raise ValueError("source_hash must contain exactly 32 bytes")
        if self.book_id is not None and type(self.book_id) is not UUID:
            raise TypeError("book_id must be a UUID")
        if (
            self.target_entity_id is not None
            and type(self.target_entity_id) is not UUID
        ):
            raise TypeError("target_entity_id must be a UUID")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")


@dataclass(frozen=True, slots=True)
class LoadResult:
    attempted: int
    applied: int
    replayed: int
    last_keys: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class BackfillSeal:
    snapshot_id: str
    manifest_hash: bytes
    source_counts: Mapping[str, int]
    terminal_book_hashes: Mapping[str, str]
    quarantine_count: int
    receipt_count: int

    def verification_payload(self) -> dict[str, object]:
        return {
            "manifest_hash": self.manifest_hash.hex(),
            "quarantine_count": self.quarantine_count,
            "receipt_count": self.receipt_count,
            "snapshot_id": self.snapshot_id,
            "source_counts": dict(sorted(self.source_counts.items())),
            "status": "PASS",
            "terminal_book_hashes": dict(sorted(self.terminal_book_hashes.items())),
        }


class ResumableBackfillLoader:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        snapshot_id: str,
        manifest_hash: bytes,
        apply_item: Callable[[Session, SourceLoadItem], None],
        after_apply_before_receipt: Callable[[SourceLoadItem], None] | None = None,
    ) -> None:
        if type(snapshot_id) is not str or not snapshot_id:
            raise ValueError("snapshot_id must be nonblank")
        if type(manifest_hash) is not bytes or len(manifest_hash) != 32:
            raise ValueError("manifest_hash must contain exactly 32 bytes")
        self._session_factory = session_factory
        self._snapshot_id = snapshot_id
        self._manifest_hash = manifest_hash
        self._apply_item = apply_item
        self._after_apply_before_receipt = after_apply_before_receipt

    def load(self, items: Iterable[SourceLoadItem]) -> LoadResult:
        ordered = tuple(
            sorted(
                items,
                key=lambda item: (
                    item.source_table.encode("utf-8"),
                    item.canonical_source_key.encode("utf-8"),
                    item.source_primary_key.encode("utf-8"),
                ),
            )
        )
        if any(type(item) is not SourceLoadItem for item in ordered):
            raise TypeError("backfill load accepts exact SourceLoadItem values")
        applied = 0
        replayed = 0
        last_keys: dict[str, str] = {}
        for item in ordered:
            with self._session_factory() as session, session.begin():
                checkpoint = session.execute(
                    select(BackfillCheckpointRecord)
                    .where(
                        BackfillCheckpointRecord.snapshot_id == self._snapshot_id,
                        BackfillCheckpointRecord.source_table == item.source_table,
                    )
                    .with_for_update()
                ).scalar_one_or_none()
                if (
                    checkpoint is not None
                    and checkpoint.manifest_hash != self._manifest_hash
                ):
                    raise BackfillChangedSourceError(
                        "snapshot checkpoint belongs to a different manifest"
                    )
                receipt = session.get(
                    BackfillSourceReceiptRecord,
                    (
                        self._snapshot_id,
                        item.source_table,
                        item.source_primary_key,
                    ),
                )
                if receipt is not None:
                    _assert_receipt_matches(receipt, item)
                    replayed += 1
                    last_keys[item.source_table] = receipt.canonical_source_key
                    continue
                if (
                    checkpoint is not None
                    and item.canonical_source_key
                    <= checkpoint.last_canonical_source_key
                ):
                    raise BackfillChangedSourceError(
                        "checkpoint has advanced beyond an unreceipted source key"
                    )
                self._apply_item(session, item)
                if self._after_apply_before_receipt is not None:
                    self._after_apply_before_receipt(item)
                session.add(
                    BackfillSourceReceiptRecord(
                        snapshot_id=self._snapshot_id,
                        source_table=item.source_table,
                        source_primary_key=item.source_primary_key,
                        canonical_source_key=item.canonical_source_key,
                        book_id=item.book_id,
                        source_hash=item.source_hash,
                        target_entity_id=item.target_entity_id,
                    )
                )
                if checkpoint is None:
                    checkpoint = BackfillCheckpointRecord(
                        snapshot_id=self._snapshot_id,
                        source_table=item.source_table,
                        manifest_hash=self._manifest_hash,
                        last_canonical_source_key=item.canonical_source_key,
                        processed_count=1,
                    )
                    session.add(checkpoint)
                else:
                    checkpoint.last_canonical_source_key = item.canonical_source_key
                    checkpoint.processed_count += 1
                    checkpoint.updated_at = datetime.now(UTC)
                session.flush()
                applied += 1
                last_keys[item.source_table] = item.canonical_source_key
        return LoadResult(
            attempted=len(ordered),
            applied=applied,
            replayed=replayed,
            last_keys=dict(sorted(last_keys.items())),
        )

    def load_atomic_group(
        self,
        items: Iterable[SourceLoadItem],
        *,
        apply_group: Callable[[Session, tuple[SourceLoadItem, ...]], None],
    ) -> LoadResult:
        """Apply related source rows and all of their receipts in one transaction.

        Journal transactions, postings, and reporting lines form one source
        aggregate.  Treating them as independent loader calls would allow a
        crash to commit an event while leaving some of its source identities
        unreceipted.  This group boundary preserves the existing single-row
        loader semantics while making that aggregate indivisible.
        """

        ordered = tuple(
            sorted(
                items,
                key=lambda item: (
                    item.source_table.encode("utf-8"),
                    item.canonical_source_key.encode("utf-8"),
                    item.source_primary_key.encode("utf-8"),
                ),
            )
        )
        if not ordered:
            raise ValueError("backfill atomic group must contain at least one item")
        if any(type(item) is not SourceLoadItem for item in ordered):
            raise TypeError("backfill load accepts exact SourceLoadItem values")
        identities = {(item.source_table, item.source_primary_key) for item in ordered}
        if len(identities) != len(ordered):
            raise ValueError(
                "backfill atomic group contains duplicate source identities"
            )

        with self._session_factory() as session, session.begin():
            checkpoints: dict[str, BackfillCheckpointRecord | None] = {}
            receipts: dict[tuple[str, str], BackfillSourceReceiptRecord | None] = {}
            for source_table in sorted({item.source_table for item in ordered}):
                checkpoint = session.execute(
                    select(BackfillCheckpointRecord)
                    .where(
                        BackfillCheckpointRecord.snapshot_id == self._snapshot_id,
                        BackfillCheckpointRecord.source_table == source_table,
                    )
                    .with_for_update()
                ).scalar_one_or_none()
                if (
                    checkpoint is not None
                    and checkpoint.manifest_hash != self._manifest_hash
                ):
                    raise BackfillChangedSourceError(
                        "snapshot checkpoint belongs to a different manifest"
                    )
                checkpoints[source_table] = checkpoint

            for item in ordered:
                receipt = session.get(
                    BackfillSourceReceiptRecord,
                    (
                        self._snapshot_id,
                        item.source_table,
                        item.source_primary_key,
                    ),
                )
                receipts[(item.source_table, item.source_primary_key)] = receipt
                if receipt is not None:
                    _assert_receipt_matches(receipt, item)

            existing_count = sum(receipt is not None for receipt in receipts.values())
            if existing_count:
                if existing_count != len(ordered):
                    raise BackfillChangedSourceError(
                        "atomic source group is only partially receipted"
                    )
                return LoadResult(
                    attempted=len(ordered),
                    applied=0,
                    replayed=len(ordered),
                    last_keys=dict(
                        sorted(
                            {
                                item.source_table: item.canonical_source_key
                                for item in ordered
                            }.items()
                        )
                    ),
                )

            grouped_by_table: dict[str, list[SourceLoadItem]] = {}
            for item in ordered:
                grouped_by_table.setdefault(item.source_table, []).append(item)
            for source_table, table_items in grouped_by_table.items():
                checkpoint = checkpoints[source_table]
                first_key = min(item.canonical_source_key for item in table_items)
                if (
                    checkpoint is not None
                    and first_key <= checkpoint.last_canonical_source_key
                ):
                    raise BackfillChangedSourceError(
                        "checkpoint has advanced beyond an unreceipted source key"
                    )

            apply_group(session, ordered)
            if self._after_apply_before_receipt is not None:
                for item in ordered:
                    self._after_apply_before_receipt(item)
            for item in ordered:
                session.add(
                    BackfillSourceReceiptRecord(
                        snapshot_id=self._snapshot_id,
                        source_table=item.source_table,
                        source_primary_key=item.source_primary_key,
                        canonical_source_key=item.canonical_source_key,
                        book_id=item.book_id,
                        source_hash=item.source_hash,
                        target_entity_id=item.target_entity_id,
                    )
                )
            for source_table, table_items in grouped_by_table.items():
                checkpoint = checkpoints[source_table]
                last_key = max(item.canonical_source_key for item in table_items)
                if checkpoint is None:
                    session.add(
                        BackfillCheckpointRecord(
                            snapshot_id=self._snapshot_id,
                            source_table=source_table,
                            manifest_hash=self._manifest_hash,
                            last_canonical_source_key=last_key,
                            processed_count=len(table_items),
                        )
                    )
                else:
                    checkpoint.last_canonical_source_key = last_key
                    checkpoint.processed_count += len(table_items)
                    checkpoint.updated_at = datetime.now(UTC)
            session.flush()
            return LoadResult(
                attempted=len(ordered),
                applied=len(ordered),
                replayed=0,
                last_keys=dict(
                    sorted(
                        {
                            table: max(
                                item.canonical_source_key for item in table_items
                            )
                            for table, table_items in grouped_by_table.items()
                        }.items()
                    )
                ),
            )


def _assert_receipt_matches(
    receipt: BackfillSourceReceiptRecord,
    item: SourceLoadItem,
) -> None:
    if (
        receipt.canonical_source_key != item.canonical_source_key
        or receipt.source_hash != item.source_hash
        or receipt.book_id != item.book_id
        or receipt.target_entity_id != item.target_entity_id
    ):
        raise BackfillChangedSourceError(
            "source receipt identity was reused with different content"
        )


def seal_backfill(
    session_factory: Callable[[], Session],
    *,
    snapshot_id: str,
    manifest_hash: bytes,
    source_counts: Mapping[str, int],
    terminal_book_hashes: Mapping[str, str],
) -> BackfillSeal:
    normalized_counts = _normalized_counts(source_counts)
    normalized_hashes = _normalized_hashes(terminal_book_hashes)
    with session_factory() as session, session.begin():
        existing = session.get(BackfillSealRecord, snapshot_id)
        quarantine_count = int(
            session.scalar(
                select(func.count())
                .select_from(BackfillQuarantineRecord)
                .where(BackfillQuarantineRecord.snapshot_id == snapshot_id)
            )
            or 0
        )
        receipt_counts = {
            str(source_table): int(count)
            for source_table, count in session.execute(
                select(
                    BackfillSourceReceiptRecord.source_table,
                    func.count(),
                )
                .where(BackfillSourceReceiptRecord.snapshot_id == snapshot_id)
                .group_by(BackfillSourceReceiptRecord.source_table)
            )
        }
        receipt_count = sum(receipt_counts.values())
        observed_counts = {
            table: receipt_counts.get(table, 0) for table in normalized_counts
        }
        observed_counts.update(
            {
                table: count
                for table, count in receipt_counts.items()
                if table not in normalized_counts
            }
        )
        candidate = BackfillSeal(
            snapshot_id=snapshot_id,
            manifest_hash=manifest_hash,
            source_counts=normalized_counts,
            terminal_book_hashes=normalized_hashes,
            quarantine_count=quarantine_count,
            receipt_count=receipt_count,
        )
        if existing is not None:
            if _seal_from_record(existing) != candidate:
                raise BackfillChangedSourceError(
                    "snapshot seal already exists with different evidence"
                )
            return candidate
        if quarantine_count:
            raise BackfillSealBlocked("backfill cannot seal with quarantine rows")
        if observed_counts != normalized_counts:
            raise BackfillSealBlocked(
                "backfill per-table source counts do not match durable source receipts"
            )
        session.add(
            BackfillSealRecord(
                snapshot_id=snapshot_id,
                manifest_hash=manifest_hash,
                source_counts=normalized_counts,
                terminal_book_hashes=normalized_hashes,
                quarantine_count=quarantine_count,
                receipt_count=receipt_count,
            )
        )
        session.flush()
        return candidate


def _seal_from_record(record: BackfillSealRecord) -> BackfillSeal:
    return BackfillSeal(
        snapshot_id=record.snapshot_id,
        manifest_hash=record.manifest_hash,
        source_counts=dict(record.source_counts),
        terminal_book_hashes=dict(record.terminal_book_hashes),
        quarantine_count=record.quarantine_count,
        receipt_count=record.receipt_count,
    )


def _normalized_counts(values: Mapping[str, int]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for key, value in values.items():
        if type(key) is not str or not key or type(value) is not int or value < 0:
            raise ValueError("source counts must be nonnegative integer mappings")
        normalized[key] = value
    return dict(sorted(normalized.items()))


def _normalized_hashes(values: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in values.items():
        if (
            type(key) is not str
            or not key
            or type(value) is not str
            or len(value) != 64
        ):
            raise ValueError("terminal Book hashes must contain hex SHA-256 values")
        try:
            bytes.fromhex(value)
        except ValueError:
            raise ValueError(
                "terminal Book hashes must contain hex SHA-256 values"
            ) from None
        normalized[key] = value.lower()
    return dict(sorted(normalized.items()))


def write_verification(path: Path, seal: BackfillSeal) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(seal.verification_payload()) + b"\n")


__all__ = [
    "BackfillChangedSourceError",
    "BackfillSeal",
    "BackfillSealBlocked",
    "LoadResult",
    "ResumableBackfillLoader",
    "SourceLoadItem",
    "seal_backfill",
    "write_verification",
]
