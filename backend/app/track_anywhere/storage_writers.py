from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .audit import AuditEvent
from .drafts import DraftTransaction
from .ledger import Transaction
from .security import redact
from .storage_json import to_jsonable
from .storage_models import (
    AuditEventRecord,
    CredentialRecord,
    DraftPostingRecord,
    DraftRecord,
    FundRecord,
    IdempotencyReceiptRecord,
    InvestmentEventRecord,
    PostingRecord,
    RecurringItemRecord,
    TransactionRecord,
)


class StorageWriters:
    def _replace_transaction_postings(self, session: Session, transaction: Transaction) -> None:
        session.execute(delete(PostingRecord).where(PostingRecord.transaction_id == transaction.transaction_id))
        for index, posting in enumerate(transaction.postings):
            session.add(
                PostingRecord(
                    transaction_id=transaction.transaction_id,
                    position=index,
                    account_id=posting.account_id,
                    amount=str(posting.amount),
                    currency=posting.currency,
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
            session.merge(
                TransactionRecord(
                    transaction_id=transaction.transaction_id,
                    memo=transaction.memo,
                    occurred_at=transaction.occurred_at.isoformat(),
                    purpose=transaction.purpose,
                    category_id=transaction.category_id,
                    reversed_by=transaction.reversed_by,
                    version=transaction.version,
                )
            )
            self._replace_transaction_postings(session, transaction)

    def _save_drafts(self, session: Session, drafts) -> None:
        for draft in drafts:
            session.merge(
                DraftRecord(
                    draft_id=draft.draft_id,
                    memo=draft.memo,
                    state=draft.state,
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
                    account_id=event.account_id,
                    event_type=event.event_type,
                    amount=str(event.amount),
                    currency=event.currency,
                    occurred_at=event.occurred_at.isoformat(),
                    memo=event.memo,
                    units=str(event.units) if event.units is not None else None,
                    nav=str(event.nav) if event.nav is not None else None,
                    version=event.version,
                )
            )

    def _save_credentials(self, session: Session, credentials) -> None:
        for credential in credentials:
            session.merge(
                CredentialRecord(
                    token_hash=credential.token_hash,
                    actor_id=credential.actor.actor_id,
                    actor_type=credential.actor.actor_type,
                    scopes=sorted(credential.actor.scopes),
                    issued_at=credential.issued_at.isoformat(),
                    expires_at=credential.expires_at.isoformat(),
                    jti=credential.jti,
                    revoked_at=credential.revoked_at.isoformat() if credential.revoked_at else None,
                )
            )

    def _save_audit_events(self, session: Session, events: list[AuditEvent]) -> None:
        for event in events:
            session.merge(
                AuditEventRecord(
                    event_id=event.event_id,
                    operation=event.operation,
                    actor_id=event.actor_id,
                    actor_type=event.actor_type,
                    entity_ref=event.entity_ref,
                    details=to_jsonable(event.details),
                    created_at=event.created_at,
                )
            )

    def _save_idempotency_receipts(self, session: Session, receipts) -> None:
        for receipt in receipts:
            session.merge(
                IdempotencyReceiptRecord(
                    key_hash=receipt.key_hash,
                    actor_id=receipt.actor_id,
                    operation=receipt.operation,
                    request_hash=receipt.request_hash,
                    result=redact(to_jsonable(receipt.result)),
                    replay_count=receipt.replay_count,
                )
            )
