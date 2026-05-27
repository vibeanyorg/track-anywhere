from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable
from uuid import uuid4

from .assets import default_asset_definition, validate_asset_amount
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
    counterparty_id: str | None = None
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
    book_id: str = DEFAULT_BOOK_ID
    lines: list[TransactionLine] = field(default_factory=list)
    reversed_by: str | None = None
    reverses_transaction_id: str | None = None
    version: int = 1


class Ledger:
    def __init__(self, *, asset_scale_lookup: Callable[[str], int] | None = None) -> None:
        self.accounts: dict[str, Account] = {}
        self.transactions: dict[str, Transaction] = {}
        self._asset_scale_lookup = asset_scale_lookup or (lambda code: default_asset_definition(code).scale)

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
        book_id: str | None = None,
        reverses_transaction_id: str | None = None,
    ) -> Transaction:
        posting_book_id = self._validate_transaction_postings(postings, book_id=book_id)
        transaction = Transaction(
            transaction_id=f"txn_{uuid4().hex}",
            memo=memo,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            purpose=purpose or memo,
            postings=postings,
            book_id=posting_book_id,
            lines=[],
            reverses_transaction_id=reverses_transaction_id,
        )
        self.transactions[transaction.transaction_id] = transaction
        return transaction

    def validate_transaction_integrity(self, transaction: Transaction, *, enforce_asset_scale: bool = True) -> None:
        self._validate_transaction_postings(
            transaction.postings,
            book_id=transaction.book_id,
            enforce_asset_scale=enforce_asset_scale,
        )

    def _validate_transaction_postings(
        self,
        postings: list[Posting],
        *,
        book_id: str | None = None,
        enforce_asset_scale: bool = True,
    ) -> str:
        if len(postings) < 2:
            raise ValidationError("confirmed transaction requires at least two postings")
        totals: dict[str, Decimal] = {}
        posting_book_id: str | None = book_id
        system_adjustment_posting_count = 0
        for posting in postings:
            if posting.amount == Decimal("0"):
                raise ValidationError("posting amount must not be zero")
            if enforce_asset_scale:
                validate_asset_amount(
                    posting.amount,
                    posting.currency,
                    field_name="posting amount",
                    scale_lookup=self._asset_scale_lookup,
                )
            account = self.get_account(posting.account_id)
            if posting.currency != account.currency:
                raise ValidationError(
                    f"posting currency {posting.currency} does not match "
                    f"account {posting.account_id} currency {account.currency}"
                )
            if posting_book_id is None:
                posting_book_id = account.book_id
            elif account.book_id != posting_book_id:
                raise ValidationError("transaction postings must belong to one book")
            if self._is_system_adjustment_account(account):
                system_adjustment_posting_count += 1
            totals[posting.currency] = totals.get(posting.currency, Decimal("0")) + posting.amount
        unbalanced = {currency: total for currency, total in totals.items() if total != Decimal("0")}
        if unbalanced:
            raise ValidationError(f"postings must balance by currency: {unbalanced}")
        if system_adjustment_posting_count and (
            len(postings) != 2 or system_adjustment_posting_count != 1
        ):
            raise ValidationError(
                "balance adjustment transaction requires exactly two postings: "
                "one account posting and one system adjustment posting"
            )
        return posting_book_id or DEFAULT_BOOK_ID

    @staticmethod
    def _is_system_adjustment_account(account: Account) -> bool:
        return (
            account.type == "system"
            and account.institution_type == "system"
            and account.subtype == "system_adjustment"
            and account.institution == "track-anywhere"
        )

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
        counterparty_id: str | None = None,
        memo: str = "",
    ) -> TransactionLine:
        allowed_line_types = {
            "expense",
            "income",
            "transfer_fee",
            "refund",
            "adjustment",
            "dividend",
            "interest",
            "investment_buy",
            "investment_sell",
            "investment_fee",
            "investment_tax",
            "fx_exchange",
            "fx_fee",
            "fx_gain_loss",
            "investment_gain_loss",
        }
        if line_type not in allowed_line_types:
            raise ValidationError("transaction line type is invalid")
        if amount <= Decimal("0"):
            raise ValidationError("transaction line amount must be positive")
        validate_asset_amount(amount, currency, field_name="transaction line amount", scale_lookup=self._asset_scale_lookup)
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
            counterparty_id=counterparty_id,
            memo=memo,
        )
        transaction.lines.append(line)
        return line

    def balance(self, account_id: str) -> dict[str, Decimal]:
        self.get_account(account_id)
        totals: dict[str, Decimal] = {}
        for transaction in self.transactions.values():
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
        if transaction.reverses_transaction_id is not None:
            raise ValidationError("reversal transactions cannot be reversed directly")
        reversal = self.create_transaction(
            memo=memo,
            purpose="reversal",
            postings=[
                Posting(account_id=posting.account_id, amount=-posting.amount, currency=posting.currency)
                for posting in transaction.postings
            ],
            book_id=transaction.book_id,
            reverses_transaction_id=transaction.transaction_id,
        )
        transaction.reversed_by = reversal.transaction_id
        return reversal
