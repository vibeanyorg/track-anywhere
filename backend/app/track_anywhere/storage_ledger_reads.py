from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Numeric, case, cast, func, select

from .domain_storage_models import TransactionLineRecord
from .ledger import Posting, Transaction, TransactionLine
from .storage_models import PostingRecord, TransactionRecord


class LedgerReadStorage:
    def account_balance(self, account_id: str) -> dict[str, Decimal]:
        balances = self.account_balances([account_id])
        return {
            currency: amount
            for (stored_account_id, currency), amount in balances.items()
            if stored_account_id == account_id
        }

    def account_balances(self, account_ids: Iterable[str]) -> dict[tuple[str, str], Decimal]:
        ids = sorted(set(account_ids))
        if not ids:
            return {}
        cached_transactions = getattr(self, "_read_transactions", None)
        if cached_transactions is not None:
            totals: dict[tuple[str, str], Decimal] = {}
            for transaction in cached_transactions.values():
                for posting in transaction.postings:
                    if posting.account_id not in ids:
                        continue
                    key = (posting.account_id, posting.currency)
                    totals[key] = totals.get(key, Decimal("0")) + posting.amount
            return totals
        totals: dict[tuple[str, str], Decimal] = {}
        with self.session_factory() as session:
            max_scale = _amount_scale_expression(session, PostingRecord.amount)
            rows = session.execute(
                select(
                    PostingRecord.account_id,
                    PostingRecord.currency,
                    func.sum(cast(PostingRecord.amount, Numeric)).label("amount"),
                    max_scale.label("scale"),
                )
                .where(PostingRecord.account_id.in_(ids))
                .group_by(PostingRecord.account_id, PostingRecord.currency)
            )
            for account_id, currency, amount, scale in rows:
                totals[(account_id, currency)] = _canonical_decimal(Decimal(amount or 0), scale=int(scale or 0))
        return totals

    def confirmed_transaction_count(self, *, book_id: str | None = None) -> int:
        with self.session_factory() as session:
            statement = select(func.count(TransactionRecord.transaction_id))
            if book_id is not None:
                statement = statement.where(TransactionRecord.book_id == book_id)
            return int(session.scalar(statement) or 0)

    def get_confirmed_transaction(self, transaction_id: str) -> Transaction | None:
        cached = self._cached_get("transactions", transaction_id)
        if cached is not None:
            return cached
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
        cached_transactions = getattr(self, "_read_transactions", None)
        if cached_transactions is not None:
            transactions = [transaction for transaction in cached_transactions.values() if transaction.book_id == book_id]
            if account_id is not None:
                transactions = [
                    transaction
                    for transaction in transactions
                    if any(posting.account_id == account_id for posting in transaction.postings)
                ]
            if category_id is not None:
                transactions = [
                    transaction
                    for transaction in transactions
                    if any(line.category_id == category_id for line in transaction.lines)
                ]
            if counterparty_id is not None:
                transactions = [
                    transaction
                    for transaction in transactions
                    if any(line.counterparty_id == counterparty_id for line in transaction.lines)
                ]
            transactions.sort(key=lambda item: (item.occurred_at, item.transaction_id), reverse=True)
            return deepcopy(transactions[:limit])
        with self.session_factory() as session:
            base_statement = select(TransactionRecord.transaction_id, TransactionRecord.occurred_at).where(
                TransactionRecord.book_id == book_id
            )
            if account_id is not None:
                base_statement = base_statement.join(
                    PostingRecord,
                    PostingRecord.transaction_id == TransactionRecord.transaction_id,
                ).where(PostingRecord.account_id == account_id)
            if category_id is not None or counterparty_id is not None:
                base_statement = base_statement.join(
                    TransactionLineRecord,
                    TransactionLineRecord.transaction_id == TransactionRecord.transaction_id,
                )
            if category_id is not None:
                base_statement = base_statement.where(TransactionLineRecord.category_id == category_id)
            if counterparty_id is not None:
                base_statement = base_statement.where(TransactionLineRecord.counterparty_id == counterparty_id)
            selected = (
                base_statement.distinct()
                .order_by(TransactionRecord.occurred_at.desc(), TransactionRecord.transaction_id.desc())
                .limit(limit)
                .subquery()
            )
            rows = session.execute(
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
        cached_transactions = getattr(self, "_read_transactions", None)
        if cached_transactions is not None:
            transactions = [transaction for transaction in cached_transactions.values() if transaction.book_id == book_id]
            transactions.sort(key=lambda item: (item.occurred_at, item.transaction_id), reverse=True)
            return deepcopy(transactions)
        with self.session_factory() as session:
            rows = session.execute(
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

    def _load_confirmed_transactions(self, transaction_ids: Iterable[str]) -> dict[str, Transaction]:
        ids = list(dict.fromkeys(transaction_ids))
        if not ids:
            return {}
        with self.session_factory() as session:
            transactions = {
                row.transaction_id: row
                for row in session.scalars(
                    select(TransactionRecord).where(TransactionRecord.transaction_id.in_(ids))
                )
            }
            postings = self._postings_by_transaction(session, ids)
            lines = self._lines_by_transaction(session, ids)
        return {
            transaction_id: Transaction(
                transaction_id=row.transaction_id,
                book_id=row.book_id,
                memo=row.memo,
                occurred_at=datetime.fromisoformat(row.occurred_at),
                purpose=row.purpose,
                postings=postings.get(row.transaction_id, []),
                lines=lines.get(row.transaction_id, []),
                reversed_by=row.reversed_by,
                reverses_transaction_id=getattr(row, "reverses_transaction_id", None),
                version=row.version,
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
            self._transaction_from_records(
                row,
                sorted(postings.get(transaction_id, {}).values(), key=lambda item: (item.position, item.id)),
                sorted(lines.get(transaction_id, {}).values(), key=lambda item: (item.position, item.line_id)),
            )
            for transaction_id, row in records.items()
        ]

    def _transaction_from_records(
        self,
        row: TransactionRecord,
        postings: list[PostingRecord],
        lines: list[TransactionLineRecord],
    ) -> Transaction:
        return Transaction(
            transaction_id=row.transaction_id,
            book_id=row.book_id,
            memo=row.memo,
            occurred_at=datetime.fromisoformat(row.occurred_at),
            purpose=row.purpose,
            postings=[Posting(item.account_id, Decimal(item.amount), item.currency) for item in postings],
            lines=[self._line_from_record(item) for item in lines],
            reversed_by=row.reversed_by,
            reverses_transaction_id=getattr(row, "reverses_transaction_id", None),
            version=row.version,
        )

    def _postings_by_transaction(self, session, transaction_ids: list[str]) -> dict[str, list[Posting]]:
        rows = session.scalars(
            select(PostingRecord)
            .where(PostingRecord.transaction_id.in_(transaction_ids))
            .order_by(PostingRecord.transaction_id, PostingRecord.position, PostingRecord.id)
        )
        postings: dict[str, list[Posting]] = {}
        for row in rows:
            postings.setdefault(row.transaction_id, []).append(
                Posting(row.account_id, Decimal(row.amount), row.currency)
            )
        return postings

    def _lines_by_transaction(self, session, transaction_ids: list[str]) -> dict[str, list[TransactionLine]]:
        rows = session.scalars(
            select(TransactionLineRecord)
            .where(TransactionLineRecord.transaction_id.in_(transaction_ids))
            .order_by(TransactionLineRecord.transaction_id, TransactionLineRecord.position)
        )
        lines: dict[str, list[TransactionLine]] = {}
        for row in rows:
            lines.setdefault(row.transaction_id, []).append(self._line_from_record(row))
        return lines

    @staticmethod
    def _line_from_record(row: TransactionLineRecord) -> TransactionLine:
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


def _canonical_decimal(value: Decimal, *, scale: int) -> Decimal:
    if scale > 0:
        return value.quantize(Decimal("1").scaleb(-scale))
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return normalized.quantize(Decimal("1"))
    return normalized


def _amount_scale_expression(session, amount_column):
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        return func.max(func.length(func.split_part(amount_column, ".", 2)))
    return func.max(
        case(
            (func.instr(amount_column, ".") > 0, func.length(amount_column) - func.instr(amount_column, ".")),
            else_=0,
        )
    )
