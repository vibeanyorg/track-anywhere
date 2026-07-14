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
        receipt_count = int(
            session.scalar(
                select(func.count())
                .select_from(BackfillSourceReceiptRecord)
                .where(BackfillSourceReceiptRecord.snapshot_id == snapshot_id)
            )
            or 0
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
        if sum(normalized_counts.values()) != receipt_count:
            raise BackfillSealBlocked(
                "backfill source counts do not match durable source receipts"
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
