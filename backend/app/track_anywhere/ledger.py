from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from .books import DEFAULT_BOOK_ID
from .errors import NotFound, ValidationError


@dataclass
class Account:
    account_id: str
    name: str
    type: str
    currency: str
    institution_type: str | None = None
    subtype: str | None = None
    institution: str | None = None
    book_id: str = DEFAULT_BOOK_ID
    version: int = 1


@dataclass
class Posting:
    account_id: str
    amount: Decimal
    currency: str


@dataclass
class TransactionLine:
    line_id: str
    transaction_id: str
    position: int
    line_type: str
    amount: Decimal
    currency: str
    book_id: str = DEFAULT_BOOK_ID
    category_id: str | None = None
    category_version_id: str | None = None
    category_path_snapshot: dict[str, str | None] | None = None
    merchant_id: str | None = None
    project_id: str | None = None
    necessity: str = "unknown"
    reimbursement_status: str = "none"
    memo: str = ""
    version: int = 1


@dataclass
class Transaction:
    transaction_id: str
    memo: str
    occurred_at: datetime
    purpose: str
    postings: list[Posting]
    category_id: str | None = None
    book_id: str = DEFAULT_BOOK_ID
    lines: list[TransactionLine] = field(default_factory=list)
    reversed_by: str | None = None
    version: int = 1


class Ledger:
    def __init__(self) -> None:
        self.accounts: dict[str, Account] = {}
        self.transactions: dict[str, Transaction] = {}

    def create_account(
        self,
        name: str,
        type: str,
        currency: str,
        *,
        institution_type: str | None = None,
        subtype: str | None = None,
        institution: str | None = None,
        book_id: str = DEFAULT_BOOK_ID,
    ) -> Account:
        account = Account(
            account_id=f"acc_{uuid4().hex}",
            name=name,
            type=type,
            currency=currency,
            institution_type=institution_type,
            subtype=subtype,
            institution=institution,
            book_id=book_id,
        )
        self.accounts[account.account_id] = account
        return account

    def get_account(self, account_id: str) -> Account:
        try:
            return self.accounts[account_id]
        except KeyError as exc:
            raise NotFound(f"account not found: {account_id}") from exc

    def create_transaction(
        self,
        memo: str,
        postings: list[Posting],
        *,
        occurred_at: datetime | None = None,
        purpose: str | None = None,
        category_id: str | None = None,
        book_id: str | None = None,
    ) -> Transaction:
        if len(postings) < 2:
            raise ValidationError("confirmed transaction requires at least two postings")
        totals: dict[str, Decimal] = {}
        posting_book_id: str | None = book_id
        for posting in postings:
            account = self.get_account(posting.account_id)
            if posting_book_id is None:
                posting_book_id = account.book_id
            elif account.book_id != posting_book_id:
                raise ValidationError("transaction postings must belong to one book")
            totals[posting.currency] = totals.get(posting.currency, Decimal("0")) + posting.amount
        unbalanced = {currency: total for currency, total in totals.items() if total != Decimal("0")}
        if unbalanced:
            raise ValidationError(f"postings must balance by currency: {unbalanced}")
        transaction = Transaction(
            transaction_id=f"txn_{uuid4().hex}",
            memo=memo,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            purpose=purpose or memo,
            postings=postings,
            category_id=category_id,
            book_id=posting_book_id or DEFAULT_BOOK_ID,
            lines=[],
        )
        self.transactions[transaction.transaction_id] = transaction
        return transaction

    def add_line(
        self,
        transaction: Transaction,
        *,
        line_type: str,
        amount: Decimal,
        currency: str,
        category_id: str | None = None,
        category_version_id: str | None = None,
        category_path_snapshot: dict[str, str | None] | None = None,
        memo: str = "",
    ) -> TransactionLine:
        if line_type not in {"expense", "income", "transfer_fee", "refund", "adjustment"}:
            raise ValidationError("transaction line type is invalid")
        if amount <= Decimal("0"):
            raise ValidationError("transaction line amount must be positive")
        line = TransactionLine(
            line_id=f"line_{uuid4().hex}",
            transaction_id=transaction.transaction_id,
            position=len(transaction.lines),
            line_type=line_type,
            amount=amount,
            currency=currency,
            book_id=transaction.book_id,
            category_id=category_id,
            category_version_id=category_version_id,
            category_path_snapshot=category_path_snapshot,
            memo=memo,
        )
        transaction.lines.append(line)
        return line

    def balance(self, account_id: str) -> dict[str, Decimal]:
        self.get_account(account_id)
        totals: dict[str, Decimal] = {}
        for transaction in self.transactions.values():
            if transaction.reversed_by is not None:
                continue
            for posting in transaction.postings:
                if posting.account_id == account_id:
                    totals[posting.currency] = totals.get(posting.currency, Decimal("0")) + posting.amount
        return totals

    def reverse_transaction(self, transaction_id: str, memo: str) -> Transaction:
        transaction = self.transactions.get(transaction_id)
        if transaction is None:
            raise NotFound(f"transaction not found: {transaction_id}")
        if transaction.reversed_by is not None:
            raise ValidationError("transaction is already reversed")
        reversal = self.create_transaction(
            memo=memo,
            postings=[
                Posting(account_id=posting.account_id, amount=-posting.amount, currency=posting.currency)
                for posting in transaction.postings
            ],
        )
        transaction.reversed_by = reversal.transaction_id
        return reversal
