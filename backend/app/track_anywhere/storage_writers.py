from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .audit import AuditEvent
from .drafts import DraftTransaction
from .errors import ValidationError
from .ledger import Transaction
from .storage_json import to_jsonable
from .storage_models import (
    AuditEventRecord,
    DraftPostingRecord,
    DraftRecord,
    FundRecord,
    IdempotencyReceiptRecord,
    InvestmentEventRecord,
    InvestmentValuationRecord,
    PostingRecord,
    RecurringItemRecord,
    TransactionRecord,
)
from .storage_posting_integrity import stored_posting_entries, transaction_posting_entries
from .storage_redaction import redact_idempotency_result
from .domain_storage_models import TransactionLineRecord


class StorageWriters:
    def _upsert_record(self, session: Session, model, values: dict[str, Any], key_columns: list[str]) -> None:
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            insert = postgresql_insert
        elif dialect_name == "sqlite":
            insert = sqlite_insert
        else:
            session.merge(model(**values))
            return
        statement = insert(model).values(**values)
        update_values = {key: getattr(statement.excluded, key) for key in values if key not in key_columns}
        if update_values:
            statement = statement.on_conflict_do_update(index_elements=key_columns, set_=update_values)
        else:
            statement = statement.on_conflict_do_nothing(index_elements=key_columns)
        session.execute(statement)

    def _save_transaction_postings(self, session: Session, transaction: Transaction) -> None:
        existing = list(
            session.scalars(
                select(PostingRecord)
                .where(PostingRecord.transaction_id == transaction.transaction_id)
                .order_by(PostingRecord.position, PostingRecord.id)
            )
        )
        if existing:
            if stored_posting_entries(existing, transaction.book_id) != transaction_posting_entries(transaction):
                raise ValidationError(
                    "confirmed transaction postings are immutable; write a reversal or adjustment instead"
                )
            return
        for index, posting in enumerate(transaction.postings):
            session.add(
                PostingRecord(
                    transaction_id=transaction.transaction_id,
                    book_id=transaction.book_id,
                    position=index,
                    account_id=posting.account_id,
                    amount=str(posting.amount),
                    currency=posting.currency,
                )
            )

    def _replace_transaction_lines(self, session: Session, transaction: Transaction) -> None:
        session.execute(delete(TransactionLineRecord).where(TransactionLineRecord.transaction_id == transaction.transaction_id))
        for line in transaction.lines:
            session.add(
                TransactionLineRecord(
                    line_id=line.line_id,
                    transaction_id=line.transaction_id,
                    position=line.position,
                    line_type=line.line_type,
                    amount=str(line.amount),
                    currency=line.currency,
                    book_id=line.book_id,
                    category_id=line.category_id,
                    category_version_id=line.category_version_id,
                    category_path_snapshot=to_jsonable(line.category_path_snapshot),
                    counterparty_id=line.counterparty_id,
                    project_id=line.project_id,
                    necessity=line.necessity,
                    reimbursement_status=line.reimbursement_status,
                    memo=line.memo,
                    version=line.version,
                )
            )

    def _replace_draft_postings(self, session: Session, draft: DraftTransaction) -> None:
        session.execute(delete(DraftPostingRecord).where(DraftPostingRecord.draft_id == draft.draft_id))
        for index, posting in enumerate(draft.proposed_postings):
            session.add(
                DraftPostingRecord(
                    draft_id=draft.draft_id,
                    position=index,
                    account_id=posting.account_id,
                    amount=str(posting.amount),
                    currency=posting.currency,
                )
            )

    def _save_transactions(self, session: Session, transactions) -> None:
        for transaction in transactions:
            self._upsert_record(
                session,
                TransactionRecord,
                {
                    "transaction_id": transaction.transaction_id,
                    "book_id": transaction.book_id,
                    "memo": transaction.memo,
                    "occurred_at": transaction.occurred_at.isoformat(),
                    "purpose": transaction.purpose,
                    "reversed_by": transaction.reversed_by,
                    "reverses_transaction_id": transaction.reverses_transaction_id,
                    "version": transaction.version,
                },
                ["transaction_id"],
            )
            self._save_transaction_postings(session, transaction)
            self._replace_transaction_lines(session, transaction)

    def _save_drafts(self, session: Session, drafts) -> None:
        for draft in drafts:
            session.merge(
                DraftRecord(
                    draft_id=draft.draft_id,
                    memo=draft.memo,
                    state=draft.state,
                    book_id=draft.book_id,
                    missing_fields=list(draft.missing_fields),
                    source=draft.source,
                    confidence=draft.confidence,
                    version=draft.version,
                    attachment_id=draft.attachment_id,
                    category_id=draft.category_id,
                    metadata_json=to_jsonable(draft.metadata or {}),
                )
            )
            self._replace_draft_postings(session, draft)

    def _save_recurring_items(self, session: Session, items) -> None:
        for item in items:
            recurrence = {"type": item.recurrence.type, "day": item.recurrence.day}
            if item.recurrence.month is not None:
                recurrence["month"] = item.recurrence.month
            session.merge(
                RecurringItemRecord(
                    recurring_id=item.recurring_id,
                    name=item.name,
                    kind=item.kind,
                    status=item.status,
                    book_id=item.book_id,
                    amount=str(item.amount) if item.amount is not None else None,
                    currency=item.currency,
                    provider=item.provider,
                    reference=item.reference,
                    recurrence=recurrence,
                    reminder_days=list(item.reminder_days),
                    anchor_date=item.anchor_date.isoformat(),
                    source_account_id=item.source_account_id,
                    category_id=item.category_id,
                    last_draft_renewal_date=(
                        item.last_draft_renewal_date.isoformat() if item.last_draft_renewal_date else None
                    ),
                    last_draft_id=item.last_draft_id,
                    version=item.version,
                )
            )

    def _save_funds(self, session: Session, funds) -> None:
        for fund in funds:
            session.merge(
                FundRecord(
                    fund_id=fund.fund_id,
                    book_id=fund.book_id,
                    account_id=fund.account_id,
                    name=fund.name,
                    currency=fund.currency,
                    allocated=str(fund.allocated),
                    spent=str(fund.spent),
                    version=fund.version,
                    flow=to_jsonable(fund.flow),
                )
            )

    def _save_investment_events(self, session: Session, events) -> None:
        for event in events:
            session.merge(
                InvestmentEventRecord(
                    event_id=event.event_id,
                    book_id=event.book_id,
                    account_id=event.account_id,
                    event_type=event.event_type,
                    amount=str(event.amount),
                    currency=event.currency,
                    occurred_at=event.occurred_at.isoformat(),
                    memo=event.memo,
                    units=str(event.units) if event.units is not None else None,
                    nav=str(event.nav) if event.nav is not None else None,
                    transaction_id=event.transaction_id,
                    version=event.version,
                )
            )

    def _save_investment_valuations(self, session: Session, valuations) -> None:
        for valuation in valuations:
            session.merge(
                InvestmentValuationRecord(
                    valuation_id=valuation.valuation_id,
                    book_id=valuation.book_id,
                    account_id=valuation.account_id,
                    value=str(valuation.value),
                    currency=valuation.currency,
                    observed_at=valuation.observed_at.isoformat(),
                    source=valuation.source,
                    memo=valuation.memo,
                    version=valuation.version,
                )
            )

    def _save_audit_events(self, session: Session, events: list[AuditEvent]) -> None:
        for event in events:
            self._upsert_record(
                session,
                AuditEventRecord,
                {
                    "event_id": event.event_id,
                    "operation": event.operation,
                    "actor_id": event.actor_id,
                    "actor_type": event.actor_type,
                    "entity_ref": event.entity_ref,
                    "details": to_jsonable(event.details),
                    "created_at": event.created_at,
                },
                ["event_id"],
            )

    def _save_idempotency_receipts(self, session: Session, receipts) -> None:
        for receipt in receipts:
            self._upsert_record(
                session,
                IdempotencyReceiptRecord,
                {
                    "key_hash": receipt.key_hash,
                    "actor_id": receipt.actor_id,
                    "operation": receipt.operation,
                    "request_hash": receipt.request_hash,
                    "result": redact_idempotency_result(to_jsonable(receipt.stored_result)),
                    "replay_count": receipt.replay_count,
                },
                ["key_hash", "actor_id", "operation"],
            )
