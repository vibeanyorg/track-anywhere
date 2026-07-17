from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
from types import MappingProxyType
from typing import cast
from uuid import UUID

from sqlalchemy import and_, case, create_engine, func, select
from sqlalchemy.orm import Session

from .infrastructure.db.models.async_projection import (
    ProjectionCheckpointRecord,
    ProjectionFailureRecord,
)
from .infrastructure.db.models.catalog import (
    AccountRecord,
    AssetRecord,
    CategoryRecord,
    CategoryVersionRecord,
)
from .infrastructure.db.models.credit_cards import CreditCardTransactionRecord
from .infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    EventStreamHeadRecord,
    LedgerEventRecord,
)
from .infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
    ReportingLineRecord,
    SynchronousProjectionAppliedEventRecord,
    TransactionExternalReferenceRecord,
    TransactionReversalRecord,
)
from .infrastructure.db.models.monthly_summary import MonthlyCategorySummaryRecord
from .infrastructure.projections.checkpoints import PROJECTION_NAME, PROJECTOR_VERSION
from .serialization.canonical_json import (
    EventHashEnvelope,
    JSONValue,
    canonical_json_bytes,
    event_hash,
    format_utc_microseconds,
)
from .serialization.event_registry import PRODUCTION_EVENT_REGISTRY


_ZERO_HASH = bytes(32)
_VERIFY_BATCH_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class LedgerVerificationReport:
    status: str
    issues: tuple[str, ...]
    counts: dict[str, int]
    terminal_book_hashes: dict[str, str]
    projection_hashes: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "issues": list(self.issues),
            "counts": dict(sorted(self.counts.items())),
            "terminal_book_hashes": dict(sorted(self.terminal_book_hashes.items())),
            "projection_hashes": dict(sorted(self.projection_hashes.items())),
        }


@dataclass(frozen=True, slots=True)
class LedgerReadbackFacts:
    """Secret-free Book facts consumed by offline independent verification."""

    book_id: str
    terminal_position: int
    terminal_hash: str
    counts: Mapping[str, int]
    hashes: Mapping[str, str]
    async_checkpoint_position: int | None
    unresolved_projection_failures: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))
        object.__setattr__(self, "hashes", MappingProxyType(dict(self.hashes)))


class _CanonicalRowArrayHasher:
    """Incrementally hash rows as the frozen canonical JSON array encoding."""

    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._digest.update(b"[")
        self._row_count = 0

    def add(self, row: dict[str, JSONValue]) -> None:
        if self._row_count:
            self._digest.update(b",")
        self._digest.update(canonical_json_bytes(row))
        self._row_count += 1

    def hexdigest(self) -> str:
        completed = self._digest.copy()
        completed.update(b"]")
        return completed.hexdigest()


def _hash_rows(rows: Iterable[dict[str, JSONValue]]) -> str:
    hasher = _CanonicalRowArrayHasher()
    for row in rows:
        hasher.add(row)
    return hasher.hexdigest()


def _event_envelope(stored: LedgerEventRecord) -> EventHashEnvelope:
    return EventHashEnvelope(
        event_id=stored.event_id,
        book_id=stored.book_id,
        book_position=stored.book_position,
        global_sequence=stored.global_sequence,
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
        recorded_at=stored.recorded_at,
        previous_hash=stored.previous_hash,
    )


def _verify_stored_event(stored: LedgerEventRecord, issues: list[str]) -> None:
    payload = cast(dict[str, JSONValue], stored.payload)
    try:
        PRODUCTION_EVENT_REGISTRY.validate_stored(
            stored.event_type,
            stored.event_schema_version,
            payload,
        )
        computed_hash = event_hash(_event_envelope(stored), payload)
    except (TypeError, ValueError):
        issues.append(f"invalid_event_contract:{stored.event_id}")
    else:
        if computed_hash != stored.event_hash:
            issues.append(f"event_hash_mismatch:{stored.event_id}")


def _load_book_heads(
    session: Session,
    *,
    terminal_hashes: dict[str, str],
) -> dict[UUID, BookEventHeadRecord]:
    heads: dict[UUID, BookEventHeadRecord] = {}
    statement = (
        select(BookEventHeadRecord)
        .order_by(BookEventHeadRecord.book_id)
        .execution_options(yield_per=_VERIFY_BATCH_SIZE)
    )
    for head in session.scalars(statement):
        heads[head.book_id] = head
        terminal_hashes[str(head.book_id)] = head.last_hash.hex()
    return heads


def _finish_book_event_state(
    *,
    book_id: UUID,
    event_count: int,
    previous_hash: bytes,
    head: BookEventHeadRecord | None,
    issues: list[str],
) -> None:
    if head is None:
        if event_count:
            issues.append(f"events_without_book_head:{book_id}")
        return
    if head.last_position != event_count:
        issues.append(f"book_head_position_mismatch:{book_id}")
    if head.last_hash != previous_hash:
        issues.append(f"book_head_hash_mismatch:{book_id}")


def _verify_events(
    session: Session,
    *,
    heads: dict[UUID, BookEventHeadRecord],
    issues: list[str],
) -> int:
    total_count = 0
    current_book_id: UUID | None = None
    current_head: BookEventHeadRecord | None = None
    current_count = 0
    previous_hash = _ZERO_HASH
    expected_position = 1
    heads_without_events = set(heads)
    statement = (
        select(LedgerEventRecord)
        .order_by(
            LedgerEventRecord.book_id,
            LedgerEventRecord.book_position,
        )
        .execution_options(yield_per=_VERIFY_BATCH_SIZE)
    )
    for stored in session.scalars(statement):
        if stored.book_id != current_book_id:
            if current_book_id is not None:
                _finish_book_event_state(
                    book_id=current_book_id,
                    event_count=current_count,
                    previous_hash=previous_hash,
                    head=current_head,
                    issues=issues,
                )
            current_book_id = stored.book_id
            current_head = heads.get(stored.book_id)
            heads_without_events.discard(stored.book_id)
            current_count = 0
            previous_hash = _ZERO_HASH
            expected_position = 1
        total_count += 1
        current_count += 1
        if current_head is None:
            continue
        if stored.book_position != expected_position:
            issues.append(f"book_position_gap:{stored.book_id}")
        if stored.previous_hash != previous_hash:
            issues.append(f"previous_hash_mismatch:{stored.event_id}")
        _verify_stored_event(stored, issues)
        previous_hash = stored.event_hash
        expected_position += 1

    if current_book_id is not None:
        _finish_book_event_state(
            book_id=current_book_id,
            event_count=current_count,
            previous_hash=previous_hash,
            head=current_head,
            issues=issues,
        )
    for book_id in heads_without_events:
        _finish_book_event_state(
            book_id=book_id,
            event_count=0,
            previous_hash=_ZERO_HASH,
            head=heads[book_id],
            issues=issues,
        )
    return total_count


def _verify_stream_heads(
    session: Session,
    *,
    issues: list[str],
) -> int:
    stream_head_count = 0
    terminal_rank = func.row_number().over(
        partition_by=(
            LedgerEventRecord.book_id,
            LedgerEventRecord.stream_type,
            LedgerEventRecord.stream_id,
        ),
        order_by=LedgerEventRecord.book_position.desc(),
    )
    ranked_events = select(
        LedgerEventRecord.book_id.label("book_id"),
        LedgerEventRecord.stream_type.label("stream_type"),
        LedgerEventRecord.stream_id.label("stream_id"),
        LedgerEventRecord.event_id.label("event_id"),
        LedgerEventRecord.stream_version.label("stream_version"),
        LedgerEventRecord.book_position.label("book_position"),
        terminal_rank.label("terminal_rank"),
    ).subquery()
    statement = (
        select(
            EventStreamHeadRecord,
            ranked_events.c.event_id,
            ranked_events.c.stream_version,
            ranked_events.c.book_position,
        )
        .outerjoin(
            ranked_events,
            and_(
                ranked_events.c.book_id == EventStreamHeadRecord.book_id,
                ranked_events.c.stream_type == EventStreamHeadRecord.stream_type,
                ranked_events.c.stream_id == EventStreamHeadRecord.stream_id,
                ranked_events.c.terminal_rank == 1,
            ),
        )
        .order_by(
            EventStreamHeadRecord.book_id,
            EventStreamHeadRecord.stream_type,
            EventStreamHeadRecord.stream_id,
        )
        .execution_options(yield_per=_VERIFY_BATCH_SIZE)
    )
    for head, event_id, stream_version, book_position in session.execute(statement):
        stream_head_count += 1
        if (event_id, stream_version, book_position) != (
            head.last_event_id,
            head.last_version,
            head.last_book_position,
        ):
            issues.append(
                "stream_head_mismatch:"
                f"{head.book_id}:{head.stream_type}:{head.stream_id}"
            )
    return stream_head_count


def _journal_transaction_row(
    row: JournalTransactionRecord,
) -> dict[str, JSONValue]:
    return {
        "book_id": str(row.book_id),
        "effective_at": row.effective_at.isoformat(),
        "kind": row.transaction_kind,
        "source_event_id": str(row.source_event_id),
        "source_position": row.source_position,
        "transaction_id": str(row.transaction_id),
    }


def _journal_posting_row(row: JournalPostingRecord) -> dict[str, JSONValue]:
    return {
        "account_id": str(row.account_id),
        "asset_code": row.asset_code,
        "book_id": str(row.book_id),
        "posting_id": str(row.posting_id),
        "position": row.posting_position,
        "side": str(row.side),
        "transaction_id": str(row.transaction_id),
        "units": str(int(row.units)),
    }


def _balance_row(row: AccountBalanceRecord) -> dict[str, JSONValue]:
    return {
        "account_id": str(row.account_id),
        "asset_code": row.asset_code,
        "balance_units": str(int(row.balance_units)),
        "book_id": str(row.book_id),
    }


def _credit_card_row(
    row: CreditCardTransactionRecord,
) -> dict[str, JSONValue]:
    return {
        "asset_code": row.asset_code,
        "book_id": str(row.book_id),
        "card_account_id": str(row.card_account_id),
        "counter_account_id": str(row.counter_account_id),
        "intent": row.intent,
        "original_transaction_id": (
            None
            if row.original_transaction_id is None
            else str(row.original_transaction_id)
        ),
        "transaction_id": str(row.transaction_id),
        "units": str(int(row.units)),
    }


def _hash_journal_transactions(
    session: Session, hasher: _CanonicalRowArrayHasher
) -> int:
    transaction_count = 0
    statement = (
        select(JournalTransactionRecord)
        .order_by(
            JournalTransactionRecord.book_id,
            JournalTransactionRecord.transaction_id,
        )
        .execution_options(yield_per=_VERIFY_BATCH_SIZE)
    )
    for transaction in session.scalars(statement):
        transaction_count += 1
        hasher.add(_journal_transaction_row(transaction))
    return transaction_count


def _hash_and_verify_journal_postings(
    session: Session,
    *,
    journal_hasher: _CanonicalRowArrayHasher,
    issues: list[str],
) -> int:
    posting_count = 0
    current_transaction: tuple[UUID, UUID] | None = None
    net_by_asset: dict[str, int] = defaultdict(int)

    def finish_transaction() -> None:
        if current_transaction is None:
            return
        book_id, transaction_id = current_transaction
        for asset_code, net_units in net_by_asset.items():
            if net_units != 0:
                issues.append(
                    f"unbalanced_journal:{book_id}:{transaction_id}:{asset_code}"
                )

    posting_statement = (
        select(JournalPostingRecord)
        .order_by(
            JournalPostingRecord.book_id,
            JournalPostingRecord.transaction_id,
            JournalPostingRecord.posting_position,
        )
        .execution_options(yield_per=_VERIFY_BATCH_SIZE)
    )
    for posting in session.scalars(posting_statement):
        transaction_key = (posting.book_id, posting.transaction_id)
        if transaction_key != current_transaction:
            finish_transaction()
            current_transaction = transaction_key
            net_by_asset.clear()
        posting_count += 1
        journal_hasher.add(_journal_posting_row(posting))
        direction = 1 if str(posting.side) == "debit" else -1
        units = int(posting.units)
        net_by_asset[posting.asset_code] += direction * units
    finish_transaction()
    return posting_count


def _hash_and_verify_balances(
    session: Session,
    *,
    balance_hasher: _CanonicalRowArrayHasher,
    issues: list[str],
) -> int:
    signed_units = case(
        (JournalPostingRecord.side == "debit", JournalPostingRecord.units),
        else_=-JournalPostingRecord.units,
    )
    computed = (
        select(
            JournalPostingRecord.book_id.label("book_id"),
            JournalPostingRecord.account_id.label("account_id"),
            JournalPostingRecord.asset_code.label("asset_code"),
            func.sum(signed_units).label("balance_units"),
        )
        .group_by(
            JournalPostingRecord.book_id,
            JournalPostingRecord.account_id,
            JournalPostingRecord.asset_code,
        )
        .subquery()
    )
    joined = AccountBalanceRecord.__table__.outerjoin(
        computed,
        and_(
            AccountBalanceRecord.book_id == computed.c.book_id,
            AccountBalanceRecord.account_id == computed.c.account_id,
            AccountBalanceRecord.asset_code == computed.c.asset_code,
        ),
        full=True,
    )
    book_id = func.coalesce(AccountBalanceRecord.book_id, computed.c.book_id)
    account_id = func.coalesce(
        AccountBalanceRecord.account_id,
        computed.c.account_id,
    )
    asset_code = func.coalesce(
        AccountBalanceRecord.asset_code,
        computed.c.asset_code,
    )
    balance_count = 0
    statement = (
        select(
            AccountBalanceRecord,
            computed.c.book_id,
            computed.c.account_id,
            computed.c.asset_code,
            computed.c.balance_units,
        )
        .select_from(joined)
        .order_by(book_id, account_id, asset_code)
        .execution_options(yield_per=_VERIFY_BATCH_SIZE)
    )
    for (
        balance,
        computed_book_id,
        computed_account_id,
        computed_asset_code,
        computed_units,
    ) in session.execute(statement):
        if balance is None:
            row_book_id = computed_book_id
            row_account_id = computed_account_id
            row_asset_code = computed_asset_code
            stored_units = 0
        else:
            balance_count += 1
            balance_hasher.add(_balance_row(balance))
            row_book_id = balance.book_id
            row_account_id = balance.account_id
            row_asset_code = balance.asset_code
            stored_units = int(balance.balance_units)
        if stored_units != int(computed_units or 0):
            issues.append(
                "balance_projection_mismatch:"
                f"{row_book_id}:{row_account_id}:{row_asset_code}"
            )
    return balance_count


def _hash_credit_cards(
    session: Session,
    *,
    hasher: _CanonicalRowArrayHasher,
) -> int:
    card_count = 0
    statement = (
        select(CreditCardTransactionRecord)
        .order_by(
            CreditCardTransactionRecord.book_id,
            CreditCardTransactionRecord.source_position,
        )
        .execution_options(yield_per=_VERIFY_BATCH_SIZE)
    )
    for card in session.scalars(statement):
        card_count += 1
        hasher.add(_credit_card_row(card))
    return card_count


def hash_verification_rows(rows: Iterable[dict[str, JSONValue]]) -> str:
    """Hash an ordered fact stream without retaining it in a report."""

    return _hash_rows(rows)


def _combined_verification_hash(**hashes: str) -> str:
    return hashlib.sha256(
        canonical_json_bytes({key: hashes[key] for key in sorted(hashes)})
    ).hexdigest()


def _readback_asset_row(row: AssetRecord) -> dict[str, JSONValue]:
    return {
        "asset_code": row.asset_code,
        "current_name": row.current_name,
        "display_scale": row.display_scale,
        "input_scale": row.input_scale,
        "kind": row.kind,
        "ledger_scale": row.ledger_scale,
        "status": row.status,
    }


def _readback_account_row(row: AccountRecord) -> dict[str, JSONValue]:
    return {
        "account_id": str(row.account_id),
        "account_subtype": row.account_subtype,
        "account_type": row.account_type,
        "asset_code": row.asset_code,
        "book_id": str(row.book_id),
        "current_name": row.current_name,
        "status": row.status,
        "system_role": row.system_role,
    }


def _readback_category_rows(
    categories: Iterable[CategoryRecord],
    versions: Iterable[CategoryVersionRecord],
) -> list[dict[str, JSONValue]]:
    rows: list[dict[str, JSONValue]] = []
    for row in categories:
        rows.append(
            {
                "book_id": str(row.book_id),
                "category_id": str(row.category_id),
                "current_name": row.current_name,
                "current_version_id": (
                    None
                    if row.current_version_id is None
                    else str(row.current_version_id)
                ),
                "parent_category_id": (
                    None
                    if row.parent_category_id is None
                    else str(row.parent_category_id)
                ),
                "record_type": "category",
                "status": row.status,
            }
        )
    for row in versions:
        rows.append(
            {
                "book_id": str(row.book_id),
                "category_id": str(row.category_id),
                "category_version_id": str(row.category_version_id),
                "change_reason_code": row.change_reason_code,
                "name": row.name,
                "parent_category_id": (
                    None
                    if row.parent_category_id is None
                    else str(row.parent_category_id)
                ),
                "record_type": "category_version",
                "status": row.status,
            }
        )
    rows.sort(
        key=lambda row: (
            str(row["category_id"]),
            str(row["record_type"]),
            str(row.get("category_version_id", "")),
        )
    )
    return rows


def _readback_event_row(row: LedgerEventRecord) -> dict[str, JSONValue]:
    return {
        "actor_subject_id": row.actor_subject_id,
        "book_id": str(row.book_id),
        "book_position": row.book_position,
        "causation_event_id": (
            None if row.causation_event_id is None else str(row.causation_event_id)
        ),
        "command_id": str(row.command_id),
        "correlation_id": str(row.correlation_id),
        "effective_at": format_utc_microseconds(row.effective_at),
        "event_hash": row.event_hash.hex(),
        "event_id": str(row.event_id),
        "event_schema_version": row.event_schema_version,
        "event_type": row.event_type,
        "payload": cast(dict[str, JSONValue], row.payload),
        "previous_hash": row.previous_hash.hex(),
        "stream_id": str(row.stream_id),
        "stream_type": row.stream_type,
        "stream_version": row.stream_version,
    }


def _readback_transaction_row(
    row: JournalTransactionRecord,
) -> dict[str, JSONValue]:
    return {
        "book_id": str(row.book_id),
        "description_ref": (
            None if row.description_ref is None else str(row.description_ref)
        ),
        "effective_at": format_utc_microseconds(row.effective_at),
        "kind": row.transaction_kind,
        "source_event_id": str(row.source_event_id),
        "source_position": row.source_position,
        "transaction_id": str(row.transaction_id),
    }


def _readback_posting_row(row: JournalPostingRecord) -> dict[str, JSONValue]:
    return {
        "account_id": str(row.account_id),
        "asset_code": row.asset_code,
        "book_id": str(row.book_id),
        "position": row.posting_position,
        "posting_id": str(row.posting_id),
        "side": str(row.side),
        "transaction_id": str(row.transaction_id),
        "units": str(int(row.units)),
    }


def _readback_external_reference_row(
    row: TransactionExternalReferenceRecord,
) -> dict[str, JSONValue]:
    return {
        "book_id": str(row.book_id),
        "provider_code": row.provider_code,
        "reference_kind": row.reference_kind,
        "reference_value": row.reference_value,
        "source_event_id": str(row.source_event_id),
        "transaction_id": str(row.transaction_id),
    }


def _readback_balance_row(row: AccountBalanceRecord) -> dict[str, JSONValue]:
    return {
        "account_id": str(row.account_id),
        "as_of_position": row.as_of_position,
        "asset_code": row.asset_code,
        "balance_units": str(int(row.balance_units)),
        "book_id": str(row.book_id),
    }


def _readback_reversal_row(
    row: TransactionReversalRecord,
) -> dict[str, JSONValue]:
    return {
        "book_id": str(row.book_id),
        "original_event_hash": row.original_event_hash.hex(),
        "original_event_id": str(row.original_event_id),
        "original_transaction_id": str(row.original_transaction_id),
        "reason_code": row.reason_code,
        "reversal_transaction_id": str(row.reversal_transaction_id),
        "source_event_id": str(row.source_event_id),
    }


def _readback_reporting_row(row: ReportingLineRecord) -> dict[str, JSONValue]:
    return {
        "asset_code": row.asset_code,
        "book_id": str(row.book_id),
        "catalog_id": str(row.catalog_id),
        "classification_revision": row.classification_revision,
        "description_ref": (
            None if row.description_ref is None else str(row.description_ref)
        ),
        "dimension": row.dimension,
        "dimension_id": None if row.dimension_id is None else str(row.dimension_id),
        "line_id": str(row.line_id),
        "line_kind": row.line_kind,
        "line_version_id": str(row.line_version_id),
        "position": row.line_position,
        "source_event_id": str(row.source_event_id),
        "transaction_id": str(row.transaction_id),
        "units": str(int(row.units)),
    }


def _readback_async_row(
    row: MonthlyCategorySummaryRecord,
) -> dict[str, JSONValue]:
    return {
        "asset_code": row.asset_code,
        "book_id": str(row.book_id),
        "category_id": str(row.category_id),
        "category_version_id": str(row.category_version_id),
        "line_kind": row.line_kind,
        "period_start": row.period_start.isoformat(),
        "units": str(int(row.units)),
    }


def read_ledger_readback_facts(
    session: Session,
    book_id: UUID,
) -> LedgerReadbackFacts:
    """Read deterministic, non-plaintext facts for one Book."""

    if not isinstance(session, Session) or type(book_id) is not UUID:
        raise TypeError("ledger read-back requires a Session and UUID Book")
    head = session.get(BookEventHeadRecord, book_id)
    if head is None:
        raise LookupError("Book event head not found")

    assets = tuple(
        session.scalars(select(AssetRecord).order_by(AssetRecord.asset_code))
    )
    accounts = tuple(
        session.scalars(
            select(AccountRecord)
            .where(AccountRecord.book_id == book_id)
            .order_by(AccountRecord.account_id)
        )
    )
    categories = tuple(
        session.scalars(
            select(CategoryRecord)
            .where(CategoryRecord.book_id == book_id)
            .order_by(CategoryRecord.category_id)
        )
    )
    category_versions = tuple(
        session.scalars(
            select(CategoryVersionRecord)
            .where(CategoryVersionRecord.book_id == book_id)
            .order_by(
                CategoryVersionRecord.category_id,
                CategoryVersionRecord.category_version_id,
            )
        )
    )
    events = tuple(
        session.scalars(
            select(LedgerEventRecord)
            .where(LedgerEventRecord.book_id == book_id)
            .order_by(LedgerEventRecord.book_position)
        )
    )
    transactions = tuple(
        session.scalars(
            select(JournalTransactionRecord)
            .where(JournalTransactionRecord.book_id == book_id)
            .order_by(JournalTransactionRecord.transaction_id)
        )
    )
    postings = tuple(
        session.scalars(
            select(JournalPostingRecord)
            .where(JournalPostingRecord.book_id == book_id)
            .order_by(
                JournalPostingRecord.transaction_id,
                JournalPostingRecord.posting_position,
            )
        )
    )
    external_references = tuple(
        session.scalars(
            select(TransactionExternalReferenceRecord)
            .where(TransactionExternalReferenceRecord.book_id == book_id)
            .order_by(
                TransactionExternalReferenceRecord.transaction_id,
                TransactionExternalReferenceRecord.provider_code,
                TransactionExternalReferenceRecord.reference_kind,
            )
        )
    )
    balances = tuple(
        session.scalars(
            select(AccountBalanceRecord)
            .where(AccountBalanceRecord.book_id == book_id)
            .order_by(
                AccountBalanceRecord.account_id,
                AccountBalanceRecord.asset_code,
            )
        )
    )
    reversals = tuple(
        session.scalars(
            select(TransactionReversalRecord)
            .where(TransactionReversalRecord.book_id == book_id)
            .order_by(TransactionReversalRecord.reversal_transaction_id)
        )
    )
    reporting = tuple(
        session.scalars(
            select(ReportingLineRecord)
            .where(ReportingLineRecord.book_id == book_id)
            .order_by(
                ReportingLineRecord.transaction_id,
                ReportingLineRecord.line_position,
            )
        )
    )
    typed_card_transactions = tuple(
        session.scalars(
            select(CreditCardTransactionRecord)
            .where(CreditCardTransactionRecord.book_id == book_id)
            .order_by(CreditCardTransactionRecord.source_position)
        )
    )
    synchronous = tuple(
        session.scalars(
            select(SynchronousProjectionAppliedEventRecord)
            .where(SynchronousProjectionAppliedEventRecord.book_id == book_id)
            .order_by(SynchronousProjectionAppliedEventRecord.event_id)
        )
    )
    checkpoint = session.get(
        ProjectionCheckpointRecord,
        (PROJECTION_NAME, PROJECTOR_VERSION, book_id),
    )
    async_rows = ()
    if checkpoint is not None:
        async_rows = tuple(
            session.scalars(
                select(MonthlyCategorySummaryRecord)
                .where(
                    MonthlyCategorySummaryRecord.projection_name == PROJECTION_NAME,
                    MonthlyCategorySummaryRecord.projector_version == PROJECTOR_VERSION,
                    MonthlyCategorySummaryRecord.book_id == book_id,
                    MonthlyCategorySummaryRecord.generation
                    == checkpoint.active_generation,
                )
                .order_by(
                    MonthlyCategorySummaryRecord.period_start,
                    MonthlyCategorySummaryRecord.category_id,
                    MonthlyCategorySummaryRecord.category_version_id,
                    MonthlyCategorySummaryRecord.asset_code,
                    MonthlyCategorySummaryRecord.line_kind,
                )
            )
        )
    failure_count = int(
        session.scalar(
            select(func.count())
            .select_from(ProjectionFailureRecord)
            .where(
                ProjectionFailureRecord.book_id == book_id,
                ProjectionFailureRecord.resolved_at.is_(None),
            )
        )
        or 0
    )

    asset_rows = [_readback_asset_row(row) for row in assets]
    account_rows = [_readback_account_row(row) for row in accounts]
    category_rows = _readback_category_rows(categories, category_versions)
    event_rows = [_readback_event_row(row) for row in events]
    event_order_rows = [
        {
            "book_position": row["book_position"],
            "event_id": row["event_id"],
        }
        for row in event_rows
    ]
    event_payload_rows = [
        {
            "event_id": row["event_id"],
            "event_schema_version": row["event_schema_version"],
            "event_type": row["event_type"],
            "payload": row["payload"],
        }
        for row in event_rows
    ]
    transaction_rows = [_readback_transaction_row(row) for row in transactions]
    posting_rows = [_readback_posting_row(row) for row in postings]
    external_reference_rows = [
        _readback_external_reference_row(row) for row in external_references
    ]
    balance_rows = [_readback_balance_row(row) for row in balances]
    reversal_rows = [_readback_reversal_row(row) for row in reversals]
    balance_semantic_rows = [
        {key: value for key, value in row.items() if key != "as_of_position"}
        for row in balance_rows
    ]
    reversal_semantic_rows = [
        {
            "book_id": row["book_id"],
            "original_transaction_id": row["original_transaction_id"],
            "reason_code": row["reason_code"],
            "reversal_transaction_id": row["reversal_transaction_id"],
        }
        for row in reversal_rows
    ]
    reporting_rows = [_readback_reporting_row(row) for row in reporting]
    synchronous_rows = [
        {
            "event_id": str(row.event_id),
            "projection_version": row.projection_version,
        }
        for row in synchronous
    ]
    monthly_rows = [_readback_async_row(row) for row in async_rows]
    balances_by_account_asset = {
        (row.account_id, row.asset_code): int(row.balance_units) for row in balances
    }
    card_rows = [
        {
            "account_id": str(row.account_id),
            "asset_code": row.asset_code,
            "book_id": str(row.book_id),
            "natural_balance_units": str(
                -balances_by_account_asset.get((row.account_id, row.asset_code), 0)
                if row.account_type in {"liability", "equity", "income"}
                else balances_by_account_asset.get((row.account_id, row.asset_code), 0)
            ),
            "status": row.status,
        }
        for row in accounts
        if row.account_subtype == "credit_card"
    ]
    usdt_rows = sorted(
        (row for row in posting_rows if row["asset_code"] == "USDT"),
        key=lambda row: str(row["posting_id"]),
    )

    transaction_hash = _hash_rows(transaction_rows)
    posting_hash = _hash_rows(posting_rows)
    external_reference_hash = _hash_rows(external_reference_rows)
    counts = {
        "accounts": len(accounts),
        "assets": len(assets),
        "async_projection_rows": len(async_rows),
        "categories": len(categories),
        "category_versions": len(category_versions),
        "credit_card_transactions": len(typed_card_transactions),
        "journal_postings": len(postings),
        "journal_transactions": len(transactions),
        "ledger_events": len(events),
        "reporting_lines": len(reporting),
        "reversals": len(reversals),
        "synchronous_projection_applied_events": len(synchronous),
    }
    hashes = {
        "account_balances_semantic": _hash_rows(balance_semantic_rows),
        "accounts": _hash_rows(account_rows),
        "assets": _hash_rows(asset_rows),
        "async_projection": _hash_rows(monthly_rows),
        "balances": _hash_rows(balance_rows),
        "cards": _hash_rows(card_rows),
        "categories": _hash_rows(category_rows),
        "event_order": _hash_rows(event_order_rows),
        "event_payloads": _hash_rows(event_payload_rows),
        "events": _hash_rows(event_rows),
        "external_references": external_reference_hash,
        "journal": _combined_verification_hash(
            external_references=external_reference_hash,
            postings=posting_hash,
            transactions=transaction_hash,
        ),
        "journal_postings": posting_hash,
        "journal_transactions": transaction_hash,
        "reporting": _hash_rows(reporting_rows),
        "reversal_semantic": _hash_rows(reversal_semantic_rows),
        "reversals": _hash_rows(reversal_rows),
        "synchronous_projection": _hash_rows(synchronous_rows),
        "usdt_postings": _hash_rows(usdt_rows),
    }
    return LedgerReadbackFacts(
        book_id=str(book_id),
        terminal_position=head.last_position,
        terminal_hash=head.last_hash.hex(),
        counts=counts,
        hashes=hashes,
        async_checkpoint_position=(
            None if checkpoint is None else checkpoint.last_book_position
        ),
        unresolved_projection_failures=failure_count,
    )


def verify_v2_ledger(database_url: str) -> LedgerVerificationReport:
    engine = create_engine(database_url, pool_pre_ping=True)
    issues: list[str] = []
    terminal_hashes: dict[str, str] = {}
    counts = {
        "account_balances": 0,
        "books": 0,
        "credit_card_transactions": 0,
        "journal_postings": 0,
        "journal_transactions": 0,
        "ledger_events": 0,
        "stream_heads": 0,
        "unresolved_projection_failures": 0,
    }
    journal_hasher = _CanonicalRowArrayHasher()
    balance_hasher = _CanonicalRowArrayHasher()
    credit_card_hasher = _CanonicalRowArrayHasher()
    try:
        with Session(engine) as session:
            heads = _load_book_heads(session, terminal_hashes=terminal_hashes)
            counts["books"] = len(heads)
            counts["ledger_events"] = _verify_events(
                session,
                heads=heads,
                issues=issues,
            )
            counts["stream_heads"] = _verify_stream_heads(
                session,
                issues=issues,
            )

            counts["journal_transactions"] = _hash_journal_transactions(
                session, journal_hasher
            )
            counts["journal_postings"] = _hash_and_verify_journal_postings(
                session,
                journal_hasher=journal_hasher,
                issues=issues,
            )
            counts["account_balances"] = _hash_and_verify_balances(
                session,
                balance_hasher=balance_hasher,
                issues=issues,
            )
            counts["credit_card_transactions"] = _hash_credit_cards(
                session,
                hasher=credit_card_hasher,
            )

            failure_count = session.scalar(
                select(func.count())
                .select_from(ProjectionFailureRecord)
                .where(ProjectionFailureRecord.resolved_at.is_(None))
            )
            counts["unresolved_projection_failures"] = int(failure_count or 0)
            if failure_count:
                issues.append("unresolved_projection_failures")
    finally:
        engine.dispose()

    return LedgerVerificationReport(
        status="PASS" if not issues else "FAIL",
        issues=tuple(sorted(set(issues))),
        counts=counts,
        terminal_book_hashes=terminal_hashes,
        projection_hashes={
            "balances": balance_hasher.hexdigest(),
            "credit_cards": credit_card_hasher.hexdigest(),
            "journal": journal_hasher.hexdigest(),
        },
    )


__all__ = [
    "LedgerReadbackFacts",
    "LedgerVerificationReport",
    "hash_verification_rows",
    "read_ledger_readback_facts",
    "verify_v2_ledger",
]
