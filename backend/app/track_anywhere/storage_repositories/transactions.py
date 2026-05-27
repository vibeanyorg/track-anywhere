from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import delete, select

from ..domain_storage_models import TransactionLineRecord
from ..errors import ValidationError
from ..ledger import Posting, Transaction, TransactionLine
from ..storage_json import to_jsonable
from ..storage_models import PostingRecord, TransactionRecord
from ..storage_posting_integrity import stored_posting_entries, transaction_posting_entries
from ..storage_upsert_writers import upsert_record


class TransactionRepository:
    def __init__(self, session) -> None:
        self.session = session

    def get_confirmed_transaction(self, transaction_id: str) -> Transaction | None:
        transactions = self._load_confirmed_transactions([transaction_id])
        return transactions.get(transaction_id)

    def list_confirmed_transactions(
        self,
        *,
        book_id: str,
        account_id: str | None = None,
        category_id: str | None = None,
        counterparty_id: str | None = None,
        limit: int = 20,
    ) -> list[Transaction]:
        limit = max(0, min(limit, 200))
        if limit == 0:
            return []
        selected = self._selected_transaction_ids(
            book_id=book_id,
            account_id=account_id,
            category_id=category_id,
            counterparty_id=counterparty_id,
            limit=limit,
        )
        rows = self.session.execute(
            select(TransactionRecord, PostingRecord, TransactionLineRecord)
            .join(selected, selected.c.transaction_id == TransactionRecord.transaction_id)
            .outerjoin(PostingRecord, PostingRecord.transaction_id == TransactionRecord.transaction_id)
            .outerjoin(TransactionLineRecord, TransactionLineRecord.transaction_id == TransactionRecord.transaction_id)
            .order_by(
                selected.c.occurred_at.desc(),
                TransactionRecord.transaction_id.desc(),
                PostingRecord.position,
                PostingRecord.id,
                TransactionLineRecord.position,
                TransactionLineRecord.line_id,
            )
        )
        return self._transactions_from_joined_rows(rows)

    def list_all_confirmed_transactions(self, *, book_id: str) -> list[Transaction]:
        rows = self.session.execute(
            select(TransactionRecord, PostingRecord, TransactionLineRecord)
            .outerjoin(PostingRecord, PostingRecord.transaction_id == TransactionRecord.transaction_id)
            .outerjoin(TransactionLineRecord, TransactionLineRecord.transaction_id == TransactionRecord.transaction_id)
            .where(TransactionRecord.book_id == book_id)
            .order_by(
                TransactionRecord.occurred_at.desc(),
                TransactionRecord.transaction_id.desc(),
                PostingRecord.position,
                PostingRecord.id,
                TransactionLineRecord.position,
                TransactionLineRecord.line_id,
            )
        )
        return self._transactions_from_joined_rows(rows)

    def save(self, transactions: Iterable[Any]) -> None:
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

    def _selected_transaction_ids(
        self,
        *,
        book_id: str,
        account_id: str | None,
        category_id: str | None,
        counterparty_id: str | None,
        limit: int,
    ):
        statement = select(TransactionRecord.transaction_id, TransactionRecord.occurred_at).where(
            TransactionRecord.book_id == book_id
        )
        if account_id is not None:
            statement = statement.join(
                PostingRecord,
                PostingRecord.transaction_id == TransactionRecord.transaction_id,
            ).where(PostingRecord.account_id == account_id)
        if category_id is not None or counterparty_id is not None:
            statement = statement.join(
                TransactionLineRecord,
                TransactionLineRecord.transaction_id == TransactionRecord.transaction_id,
            )
        if category_id is not None:
            statement = statement.where(TransactionLineRecord.category_id == category_id)
        if counterparty_id is not None:
            statement = statement.where(TransactionLineRecord.counterparty_id == counterparty_id)
        return (
            statement.distinct()
            .order_by(TransactionRecord.occurred_at.desc(), TransactionRecord.transaction_id.desc())
            .limit(limit)
            .subquery()
        )

    def _load_confirmed_transactions(self, transaction_ids: Iterable[str]) -> dict[str, Transaction]:
        ids = list(dict.fromkeys(transaction_ids))
        if not ids:
            return {}
        transactions = {
            row.transaction_id: row
            for row in self.session.scalars(
                select(TransactionRecord).where(TransactionRecord.transaction_id.in_(ids))
            )
        }
        postings = self._postings_by_transaction(ids)
        lines = self._lines_by_transaction(ids)
        return {
            transaction_id: transaction_from_record(
                row,
                postings.get(row.transaction_id, []),
                lines.get(row.transaction_id, []),
            )
            for transaction_id, row in transactions.items()
        }

    def _transactions_from_joined_rows(self, rows) -> list[Transaction]:
        records: dict[str, TransactionRecord] = {}
        postings: dict[str, dict[int, PostingRecord]] = {}
        lines: dict[str, dict[str, TransactionLineRecord]] = {}
        for transaction_row, posting_row, line_row in rows:
            transaction_id = transaction_row.transaction_id
            records.setdefault(transaction_id, transaction_row)
            if posting_row is not None:
                postings.setdefault(transaction_id, {})[posting_row.id] = posting_row
            if line_row is not None:
                lines.setdefault(transaction_id, {})[line_row.line_id] = line_row
        return [
            transaction_from_record(
                row,
                [
                    posting_from_record(item)
                    for item in sorted(
                        postings.get(transaction_id, {}).values(),
                        key=lambda item: (item.position, item.id),
                    )
                ],
                [
                    line_from_record(item)
                    for item in sorted(
                        lines.get(transaction_id, {}).values(),
                        key=lambda item: (item.position, item.line_id),
                    )
                ],
            )
            for transaction_id, row in records.items()
        ]

    def _postings_by_transaction(self, transaction_ids: list[str]) -> dict[str, list[Posting]]:
        rows = self.session.scalars(
            select(PostingRecord)
            .where(PostingRecord.transaction_id.in_(transaction_ids))
            .order_by(PostingRecord.transaction_id, PostingRecord.position, PostingRecord.id)
        )
        postings: dict[str, list[Posting]] = {}
        for row in rows:
            postings.setdefault(row.transaction_id, []).append(posting_from_record(row))
        return postings

    def _lines_by_transaction(self, transaction_ids: list[str]) -> dict[str, list[TransactionLine]]:
        rows = self.session.scalars(
            select(TransactionLineRecord)
            .where(TransactionLineRecord.transaction_id.in_(transaction_ids))
            .order_by(TransactionLineRecord.transaction_id, TransactionLineRecord.position)
        )
        lines: dict[str, list[TransactionLine]] = {}
        for row in rows:
            lines.setdefault(row.transaction_id, []).append(line_from_record(row))
        return lines

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


def transaction_from_record(
    row: TransactionRecord,
    postings: list[Posting],
    lines: list[TransactionLine],
) -> Transaction:
    return Transaction(
        transaction_id=row.transaction_id,
        book_id=row.book_id,
        memo=row.memo,
        occurred_at=datetime.fromisoformat(row.occurred_at),
        purpose=row.purpose,
        postings=postings,
        lines=lines,
        reversed_by=row.reversed_by,
        reverses_transaction_id=getattr(row, "reverses_transaction_id", None),
        version=row.version,
    )


def posting_from_record(row: PostingRecord) -> Posting:
    return Posting(row.account_id, Decimal(row.amount), row.currency)


def line_from_record(row: TransactionLineRecord) -> TransactionLine:
    return TransactionLine(
        line_id=row.line_id,
        transaction_id=row.transaction_id,
        position=row.position,
        line_type=row.line_type,
        amount=Decimal(row.amount),
        currency=row.currency,
        book_id=row.book_id,
        category_id=row.category_id,
        category_version_id=row.category_version_id,
        category_path_snapshot=row.category_path_snapshot,
        counterparty_id=row.counterparty_id,
        project_id=row.project_id,
        necessity=row.necessity,
        reimbursement_status=row.reimbursement_status,
        memo=row.memo,
        version=row.version,
    )
