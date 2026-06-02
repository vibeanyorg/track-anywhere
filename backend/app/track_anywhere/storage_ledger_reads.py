from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from decimal import Decimal

from sqlalchemy import Numeric, case, cast, func, select

from .accounting import CREDIT_NORMAL_ACCOUNT_TYPES, DEBIT_NORMAL_ACCOUNT_TYPES, posting_balance_delta
from .errors import ValidationError
from .ledger import Transaction
from .storage_models import AccountRecord, PostingRecord, TransactionRecord


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
            accounts = getattr(self, "_read_accounts", {})
            for transaction in cached_transactions.values():
                for posting in transaction.postings:
                    if posting.account_id not in ids:
                        continue
                    account = accounts.get(posting.account_id)
                    if account is None:
                        continue
                    try:
                        amount = posting_balance_delta(
                            account.type,
                            side=posting.side,
                            amount=posting.amount,
                            amount_semantics=posting.amount_semantics,
                        )
                    except ValidationError:
                        continue
                    key = (posting.account_id, posting.currency)
                    totals[key] = totals.get(key, Decimal("0")) + amount
            return totals
        totals: dict[tuple[str, str], Decimal] = {}
        with self.session_factory() as session:
            max_scale = _amount_scale_expression(session, PostingRecord.amount)
            effective_amount = _effective_posting_amount_expression(session)
            rows = session.execute(
                select(
                    PostingRecord.account_id,
                    PostingRecord.currency,
                    func.sum(effective_amount).label("amount"),
                    max_scale.label("scale"),
                )
                .join(AccountRecord, AccountRecord.account_id == PostingRecord.account_id)
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
        with self.unit_of_work() as uow:
            return uow.transactions.get_confirmed_transaction(transaction_id)

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
        with self.unit_of_work() as uow:
            return uow.transactions.list_confirmed_transactions(
                book_id=book_id,
                account_id=account_id,
                category_id=category_id,
                counterparty_id=counterparty_id,
                limit=limit,
            )

    def list_all_confirmed_transactions(self, *, book_id: str) -> list[Transaction]:
        cached_transactions = getattr(self, "_read_transactions", None)
        if cached_transactions is not None:
            transactions = [transaction for transaction in cached_transactions.values() if transaction.book_id == book_id]
            transactions.sort(key=lambda item: (item.occurred_at, item.transaction_id), reverse=True)
            return deepcopy(transactions)
        with self.unit_of_work() as uow:
            return uow.transactions.list_all_confirmed_transactions(book_id=book_id)


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


def _effective_posting_amount_expression(session):
    amount = cast(PostingRecord.amount, Numeric)
    legacy_signed = PostingRecord.amount_semantics == "legacy_signed"
    debit_credit = PostingRecord.amount_semantics == "debit_credit"
    valid_debit_credit = debit_credit & (amount > 0)
    debit_normal = AccountRecord.type.in_(tuple(DEBIT_NORMAL_ACCOUNT_TYPES))
    credit_normal = AccountRecord.type.in_(tuple(CREDIT_NORMAL_ACCOUNT_TYPES))
    debit_side = PostingRecord.side == "debit"
    credit_side = PostingRecord.side == "credit"
    return case(
        (valid_debit_credit & debit_normal & debit_side, amount),
        (valid_debit_credit & debit_normal & credit_side, -amount),
        (valid_debit_credit & credit_normal & credit_side, amount),
        (valid_debit_credit & credit_normal & debit_side, -amount),
        (legacy_signed, amount),
        else_=0,
    )
