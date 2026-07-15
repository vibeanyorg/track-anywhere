from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
from typing import cast
from uuid import UUID

from sqlalchemy import and_, case, create_engine, func, select
from sqlalchemy.orm import Session

from .infrastructure.db.models.async_projection import ProjectionFailureRecord
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
)
from .serialization.canonical_json import (
    EventHashEnvelope,
    JSONValue,
    canonical_json_bytes,
    event_hash,
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


__all__ = ["LedgerVerificationReport", "verify_v2_ledger"]
