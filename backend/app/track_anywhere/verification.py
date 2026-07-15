from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
from typing import cast

from sqlalchemy import create_engine, select
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


def _hash_rows(rows: list[dict[str, JSONValue]]) -> str:
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def verify_v2_ledger(database_url: str) -> LedgerVerificationReport:
    engine = create_engine(database_url, pool_pre_ping=True)
    issues: list[str] = []
    counts: dict[str, int] = {}
    terminal_hashes: dict[str, str] = {}
    try:
        with Session(engine) as session:
            heads = tuple(
                session.scalars(
                    select(BookEventHeadRecord).order_by(BookEventHeadRecord.book_id)
                )
            )
            events = tuple(
                session.scalars(
                    select(LedgerEventRecord).order_by(
                        LedgerEventRecord.book_id,
                        LedgerEventRecord.book_position,
                    )
                )
            )
            events_by_book: dict[object, list[LedgerEventRecord]] = defaultdict(list)
            for stored in events:
                events_by_book[stored.book_id].append(stored)

            for head in heads:
                previous_hash = _ZERO_HASH
                expected_position = 1
                book_events = events_by_book.pop(head.book_id, [])
                for stored in book_events:
                    if stored.book_position != expected_position:
                        issues.append(f"book_position_gap:{head.book_id}")
                    if stored.previous_hash != previous_hash:
                        issues.append(f"previous_hash_mismatch:{stored.event_id}")
                    payload = cast(dict[str, JSONValue], stored.payload)
                    try:
                        PRODUCTION_EVENT_REGISTRY.validate_stored(
                            stored.event_type,
                            stored.event_schema_version,
                            payload,
                        )
                        computed_hash = event_hash(
                            EventHashEnvelope(
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
                            ),
                            payload,
                        )
                    except (TypeError, ValueError):
                        issues.append(f"invalid_event_contract:{stored.event_id}")
                    else:
                        if computed_hash != stored.event_hash:
                            issues.append(f"event_hash_mismatch:{stored.event_id}")
                    previous_hash = stored.event_hash
                    expected_position += 1
                if head.last_position != len(book_events):
                    issues.append(f"book_head_position_mismatch:{head.book_id}")
                if head.last_hash != previous_hash:
                    issues.append(f"book_head_hash_mismatch:{head.book_id}")
                terminal_hashes[str(head.book_id)] = head.last_hash.hex()
            if events_by_book:
                issues.extend(
                    f"events_without_book_head:{book_id}" for book_id in events_by_book
                )

            event_index = {
                (event.book_id, event.stream_type, event.stream_id): event
                for event in events
            }
            stream_heads = tuple(session.scalars(select(EventStreamHeadRecord)))
            for head in stream_heads:
                terminal = event_index.get(
                    (head.book_id, head.stream_type, head.stream_id)
                )
                if terminal is None or (
                    terminal.event_id,
                    terminal.stream_version,
                    terminal.book_position,
                ) != (
                    head.last_event_id,
                    head.last_version,
                    head.last_book_position,
                ):
                    issues.append(
                        f"stream_head_mismatch:{head.book_id}:{head.stream_type}:{head.stream_id}"
                    )

            transactions = tuple(
                session.scalars(
                    select(JournalTransactionRecord).order_by(
                        JournalTransactionRecord.book_id,
                        JournalTransactionRecord.transaction_id,
                    )
                )
            )
            postings = tuple(
                session.scalars(
                    select(JournalPostingRecord).order_by(
                        JournalPostingRecord.book_id,
                        JournalPostingRecord.transaction_id,
                        JournalPostingRecord.posting_position,
                    )
                )
            )
            net_by_transaction_asset: dict[tuple[object, object, str], int] = (
                defaultdict(int)
            )
            computed_balances: dict[tuple[object, object, str], int] = defaultdict(int)
            for posting in postings:
                direction = 1 if str(posting.side) == "debit" else -1
                units = int(posting.units)
                net_by_transaction_asset[
                    (posting.book_id, posting.transaction_id, posting.asset_code)
                ] += direction * units
                computed_balances[
                    (posting.book_id, posting.account_id, posting.asset_code)
                ] += direction * units
            for key, net_units in net_by_transaction_asset.items():
                if net_units != 0:
                    issues.append(f"unbalanced_journal:{key[0]}:{key[1]}:{key[2]}")

            balances = tuple(
                session.scalars(
                    select(AccountBalanceRecord).order_by(
                        AccountBalanceRecord.book_id,
                        AccountBalanceRecord.account_id,
                        AccountBalanceRecord.asset_code,
                    )
                )
            )
            stored_balances = {
                (row.book_id, row.account_id, row.asset_code): int(row.balance_units)
                for row in balances
            }
            balance_keys = computed_balances.keys() | stored_balances.keys()
            for key in balance_keys:
                if computed_balances.get(key, 0) != stored_balances.get(key, 0):
                    issues.append(f"balance_projection_mismatch:{key[0]}:{key[1]}:{key[2]}")

            cards = tuple(
                session.scalars(
                    select(CreditCardTransactionRecord).order_by(
                        CreditCardTransactionRecord.book_id,
                        CreditCardTransactionRecord.source_position,
                    )
                )
            )
            unresolved_failures = session.scalars(
                select(ProjectionFailureRecord).where(
                    ProjectionFailureRecord.resolved_at.is_(None)
                )
            ).all()
            if unresolved_failures:
                issues.append("unresolved_projection_failures")

            counts.update(
                {
                    "account_balances": len(balances),
                    "books": len(heads),
                    "credit_card_transactions": len(cards),
                    "journal_postings": len(postings),
                    "journal_transactions": len(transactions),
                    "ledger_events": len(events),
                    "stream_heads": len(stream_heads),
                    "unresolved_projection_failures": len(unresolved_failures),
                }
            )
            journal_rows: list[dict[str, JSONValue]] = [
                {
                    "book_id": str(row.book_id),
                    "effective_at": row.effective_at.isoformat(),
                    "kind": row.transaction_kind,
                    "source_event_id": str(row.source_event_id),
                    "source_position": row.source_position,
                    "transaction_id": str(row.transaction_id),
                }
                for row in transactions
            ] + [
                {
                    "account_id": str(row.account_id),
                    "asset_code": row.asset_code,
                    "book_id": str(row.book_id),
                    "posting_id": str(row.posting_id),
                    "position": row.posting_position,
                    "side": str(row.side),
                    "transaction_id": str(row.transaction_id),
                    "units": str(int(row.units)),
                }
                for row in postings
            ]
            balance_rows: list[dict[str, JSONValue]] = [
                {
                    "account_id": str(row.account_id),
                    "asset_code": row.asset_code,
                    "balance_units": str(int(row.balance_units)),
                    "book_id": str(row.book_id),
                }
                for row in balances
            ]
            card_rows: list[dict[str, JSONValue]] = [
                {
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
                for row in cards
            ]
            projection_hashes = {
                "balances": _hash_rows(balance_rows),
                "credit_cards": _hash_rows(card_rows),
                "journal": _hash_rows(journal_rows),
            }
    finally:
        engine.dispose()

    return LedgerVerificationReport(
        status="PASS" if not issues else "FAIL",
        issues=tuple(sorted(set(issues))),
        counts=counts,
        terminal_book_hashes=terminal_hashes,
        projection_hashes=projection_hashes,
    )


__all__ = ["LedgerVerificationReport", "verify_v2_ledger"]
