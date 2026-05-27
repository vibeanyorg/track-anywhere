from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import delete, select

from ..domain_storage_models import TransactionLineRecord
from ..errors import ValidationError
from ..ledger import Transaction
from ..storage_json import to_jsonable
from ..storage_models import AdjustmentAccountRecord, AppStateRecord
from ..storage_models import AccountRecord, PostingRecord, TransactionRecord
from ..storage_posting_integrity import stored_posting_entries, transaction_posting_entries
from ..storage_upsert_writers import upsert_record


class LedgerRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def save_accounts(self, accounts: Iterable[Any]) -> None:
        for account in accounts:
            upsert_record(
                self.session,
                AccountRecord,
                {
                    "account_id": account.account_id,
                    "book_id": account.book_id,
                    "name": account.name,
                    "type": account.type,
                    "currency": account.currency,
                    "institution_type": account.institution_type,
                    "subtype": account.subtype,
                    "institution": account.institution,
                    "version": account.version,
                },
                ["account_id"],
            )

    def save_transactions(self, transactions: Iterable[Any]) -> None:
        for transaction in transactions:
            upsert_record(
                self.session,
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
            self._save_transaction_postings(transaction)
            self._replace_transaction_lines(transaction)

    def _save_transaction_postings(self, transaction: Transaction) -> None:
        existing = list(
            self.session.scalars(
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
            self.session.add(
                PostingRecord(
                    transaction_id=transaction.transaction_id,
                    book_id=transaction.book_id,
                    position=index,
                    account_id=posting.account_id,
                    amount=str(posting.amount),
                    currency=posting.currency,
                )
            )

    def _replace_transaction_lines(self, transaction: Transaction) -> None:
        self.session.execute(
            delete(TransactionLineRecord).where(TransactionLineRecord.transaction_id == transaction.transaction_id)
        )
        for line in transaction.lines:
            self.session.add(
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

    def save_adjustment_accounts(self, adjustment_account_ids: dict[str, str]) -> None:
        for currency, account_id in adjustment_account_ids.items():
            self.session.merge(AdjustmentAccountRecord(currency=currency, account_id=account_id))


class StateRepository:
    def __init__(self, session) -> None:
        self.session = session

    def delete_app_state(self, key: str) -> None:
        self.session.execute(delete(AppStateRecord).where(AppStateRecord.key == key))
