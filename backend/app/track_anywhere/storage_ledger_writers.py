from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .errors import ValidationError
from .ledger import Transaction
from .storage_json import to_jsonable
from .storage_models import PostingRecord, TransactionRecord
from .storage_posting_integrity import stored_posting_entries, transaction_posting_entries
from .domain_storage_models import TransactionLineRecord


class LedgerStorageWriters:
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
