from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

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
    version: int = 1


@dataclass
class Posting:
    account_id: str
    amount: Decimal
    currency: str


@dataclass
class Transaction:
    transaction_id: str
    memo: str
    occurred_at: datetime
    purpose: str
    postings: list[Posting]
    category_id: str | None = None
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
    ) -> Account:
        account = Account(
            account_id=f"acc_{uuid4().hex}",
            name=name,
            type=type,
            currency=currency,
            institution_type=institution_type,
            subtype=subtype,
            institution=institution,
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
    ) -> Transaction:
        if len(postings) < 2:
            raise ValidationError("confirmed transaction requires at least two postings")
        totals: dict[str, Decimal] = {}
        for posting in postings:
            self.get_account(posting.account_id)
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
        )
        self.transactions[transaction.transaction_id] = transaction
        return transaction

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
