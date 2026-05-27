from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from .audit import AuditEvent
from .budgets import BudgetFund
from .drafts import DraftTransaction
from .idempotency import CommandReceipt
from .investments import InvestmentEvent, InvestmentValuation
from .ledger import Posting, Transaction, TransactionLine
from .recurring import Recurrence, RecurringItem
from .security import Actor, Credential
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
from .storage_auth_models import CredentialRecord
from .domain_storage_models import TransactionLineRecord


class StorageLoaders:
    def _load_transactions(self, session: Session) -> dict[str, Transaction]:
        postings_by_transaction: dict[str, list[Posting]] = {}
        for posting in session.query(PostingRecord).order_by(PostingRecord.position).all():
            postings_by_transaction.setdefault(posting.transaction_id, []).append(
                Posting(posting.account_id, Decimal(posting.amount), posting.currency)
            )
        lines_by_transaction: dict[str, list[TransactionLine]] = {}
        for line in session.query(TransactionLineRecord).order_by(TransactionLineRecord.position).all():
            lines_by_transaction.setdefault(line.transaction_id, []).append(
                TransactionLine(
                    line_id=line.line_id,
                    transaction_id=line.transaction_id,
                    position=line.position,
                    line_type=line.line_type,
                    amount=Decimal(line.amount),
                    currency=line.currency,
                    book_id=line.book_id,
                    category_id=line.category_id,
                    category_version_id=line.category_version_id,
                    category_path_snapshot=line.category_path_snapshot,
                    counterparty_id=line.counterparty_id,
                    project_id=line.project_id,
                    necessity=line.necessity,
                    reimbursement_status=line.reimbursement_status,
                    memo=line.memo,
                    version=line.version,
                )
            )
        return {
            row.transaction_id: Transaction(
                transaction_id=row.transaction_id,
                book_id=row.book_id,
                memo=row.memo,
                occurred_at=datetime.fromisoformat(row.occurred_at),
                purpose=row.purpose,
                postings=postings_by_transaction.get(row.transaction_id, []),
                lines=lines_by_transaction.get(row.transaction_id, []),
                reversed_by=row.reversed_by,
                reverses_transaction_id=getattr(row, "reverses_transaction_id", None),
                version=row.version,
            )
            for row in session.query(TransactionRecord).all()
        }

    def _load_drafts(self, session: Session) -> dict[str, DraftTransaction]:
        postings_by_draft: dict[str, list[Posting]] = {}
        for posting in session.query(DraftPostingRecord).order_by(DraftPostingRecord.position).all():
            postings_by_draft.setdefault(posting.draft_id, []).append(
                Posting(posting.account_id, Decimal(posting.amount), posting.currency)
            )
        return {
            row.draft_id: DraftTransaction(
                draft_id=row.draft_id,
                memo=row.memo,
                state=row.state,
                proposed_postings=postings_by_draft.get(row.draft_id, []),
                missing_fields=list(row.missing_fields),
                source=row.source,
                confidence=row.confidence,
                book_id=row.book_id,
                version=row.version,
                attachment_id=row.attachment_id,
                category_id=row.category_id,
                metadata=dict(row.metadata_json or {}),
            )
            for row in session.query(DraftRecord).all()
        }

    def _load_recurring_items(self, session: Session) -> dict[str, RecurringItem]:
        return {
            row.recurring_id: RecurringItem(
                recurring_id=row.recurring_id,
                name=row.name,
                kind=row.kind,
                status=row.status,
                book_id=row.book_id,
                amount=Decimal(row.amount) if row.amount is not None else None,
                currency=row.currency,
                provider=row.provider,
                reference=row.reference,
                recurrence=Recurrence(**row.recurrence),
                reminder_days=list(row.reminder_days),
                anchor_date=date.fromisoformat(row.anchor_date),
                source_account_id=row.source_account_id,
                category_id=row.category_id,
                last_draft_renewal_date=(
                    date.fromisoformat(row.last_draft_renewal_date) if row.last_draft_renewal_date else None
                ),
                last_draft_id=row.last_draft_id,
                version=row.version,
            )
            for row in session.query(RecurringItemRecord).all()
        }

    def _load_funds(self, session: Session) -> dict[str, BudgetFund]:
        return {
            row.fund_id: BudgetFund(
                fund_id=row.fund_id,
                book_id=row.book_id,
                account_id=row.account_id,
                name=row.name,
                currency=row.currency,
                allocated=Decimal(row.allocated),
                spent=Decimal(row.spent),
                version=row.version,
                flow=list(row.flow),
            )
            for row in session.query(FundRecord).all()
        }

    def _load_investment_events(self, session: Session) -> dict[str, InvestmentEvent]:
        return {
            row.event_id: InvestmentEvent(
                event_id=row.event_id,
                book_id=row.book_id,
                account_id=row.account_id,
                event_type=row.event_type,
                amount=Decimal(row.amount),
                currency=row.currency,
                occurred_at=datetime.fromisoformat(row.occurred_at),
                memo=row.memo,
                units=Decimal(row.units) if row.units is not None else None,
                nav=Decimal(row.nav) if row.nav is not None else None,
                transaction_id=getattr(row, "transaction_id", None),
                version=row.version,
            )
            for row in session.query(InvestmentEventRecord).all()
        }

    def _load_investment_valuations(self, session: Session) -> dict[str, InvestmentValuation]:
        return {
            row.valuation_id: InvestmentValuation(
                valuation_id=row.valuation_id,
                book_id=row.book_id,
                account_id=row.account_id,
                value=Decimal(row.value),
                currency=row.currency,
                observed_at=datetime.fromisoformat(row.observed_at),
                source=row.source,
                memo=row.memo,
                version=row.version,
            )
            for row in session.query(InvestmentValuationRecord).all()
        }

    def _load_credentials(self, session: Session) -> dict[str, Credential]:
        return {
            row.token_hash: Credential(
                token_hash=row.token_hash,
                actor=Actor(row.actor_id, row.actor_type, frozenset(row.scopes)),
                issued_at=datetime.fromisoformat(row.issued_at),
                expires_at=datetime.fromisoformat(row.expires_at),
                jti=row.jti,
                revoked_at=datetime.fromisoformat(row.revoked_at) if row.revoked_at else None,
                auth_kind=getattr(row, "auth_kind", "api_key"),
                name=getattr(row, "name", None),
                description=getattr(row, "description", "") or "",
                key_prefix=getattr(row, "key_prefix", None),
                created_by_actor_id=getattr(row, "created_by_actor_id", None),
                last_used_at=(datetime.fromisoformat(row.last_used_at) if getattr(row, "last_used_at", None) else None),
                rotated_from_jti=getattr(row, "rotated_from_jti", None),
            )
            for row in session.query(CredentialRecord).all()
        }

    def _load_audit_events(self, session: Session) -> list[AuditEvent]:
        return [
            AuditEvent(
                event_id=row.event_id,
                operation=row.operation,
                actor_id=row.actor_id,
                actor_type=row.actor_type,
                entity_ref=row.entity_ref,
                details=row.details,
                created_at=row.created_at,
            )
            for row in session.query(AuditEventRecord).all()
        ]

    def _load_idempotency_receipts(self, session: Session) -> dict[tuple[str, str, str], CommandReceipt]:
        receipts: dict[tuple[str, str, str], CommandReceipt] = {}
        for row in session.query(IdempotencyReceiptRecord).all():
            receipts[(row.key_hash, row.actor_id, row.operation)] = CommandReceipt(
                key_hash=row.key_hash,
                actor_id=row.actor_id,
                operation=row.operation,
                request_hash=row.request_hash,
                result=row.result,
                stored_result=row.result,
                replay_count=row.replay_count,
            )
        return receipts
