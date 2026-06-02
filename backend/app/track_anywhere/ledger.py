from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable
from uuid import uuid4

from .accounting import (
    PostingAmountSemantics,
    PostingSide,
    debit_credit_balanced,
    normal_balance_side,
    posting_balance_delta,
    validate_positive_posting_amount,
    validate_posting_amount_semantics,
    validate_posting_side,
)
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
    side: PostingSide | None = None
    amount_semantics: PostingAmountSemantics = "debit_credit"


def debit_posting(account_id: str, amount: Decimal, currency: str) -> Posting:
    validate_positive_posting_amount(amount)
    return Posting(account_id, amount, currency, side="debit", amount_semantics="debit_credit")


def credit_posting(account_id: str, amount: Decimal, currency: str) -> Posting:
    validate_positive_posting_amount(amount)
    return Posting(account_id, amount, currency, side="credit", amount_semantics="debit_credit")


def legacy_signed_posting(
    account_id: str,
    signed_amount: Decimal,
    currency: str,
    *,
    side: PostingSide | None = None,
) -> Posting:
    """Construct an explicitly legacy signed posting for migration/audit compatibility.

    Do not use this for new ledger writes. It intentionally does not validate
    the signed amount so the audit path can represent dirty historical rows
    and report them as migration blockers instead of crashing during load.
    """

    return Posting(account_id, signed_amount, currency, side=side, amount_semantics="legacy_signed")


def debit_credit_posting_for_balance_delta(
    account_id: str,
    account_type: str,
    balance_delta: Decimal,
    currency: str,
) -> Posting:
    """Translate a command-level natural balance change into a debit/credit posting.

    The signed value here is not persisted as a posting amount. It is only an
    intent adapter for commands such as opening balance or balance adjustment;
    the returned posting always has explicit side plus positive amount.
    """

    if balance_delta == Decimal("0"):
        raise ValidationError("posting amount must not be zero")
    normal_side = normal_balance_side(account_type)
    side = normal_side if balance_delta > Decimal("0") else _opposite_side(normal_side)
    return _posting_for_side(account_id, side, abs(balance_delta), currency)


def opposite_side_posting(account_id: str, side: PostingSide | str | None, amount: Decimal, currency: str) -> Posting:
    return _posting_for_side(account_id, _opposite_side(side), amount, currency)


def reverse_posting(posting: Posting) -> Posting:
    amount_semantics = validate_posting_amount_semantics(posting.amount_semantics)
    if amount_semantics == "legacy_signed":
        raise ValidationError("legacy signed reversal requires account type conversion to debit_credit")
    return opposite_side_posting(posting.account_id, posting.side, posting.amount, posting.currency)


def reverse_posting_for_account(posting: Posting, account_type: str) -> Posting:
    amount_semantics = validate_posting_amount_semantics(posting.amount_semantics)
    if amount_semantics == "legacy_signed":
        return debit_credit_posting_for_balance_delta(
            posting.account_id,
            account_type,
            -posting.amount,
            posting.currency,
        )
    return reverse_posting(posting)


def _posting_for_side(account_id: str, side: PostingSide | str | None, amount: Decimal, currency: str) -> Posting:
    side = validate_posting_side(side)
    if side == "debit":
        return debit_posting(account_id, amount, currency)
    return credit_posting(account_id, amount, currency)


def _opposite_side(side: PostingSide | str | None) -> PostingSide:
    side = validate_posting_side(side)
    return "credit" if side == "debit" else "debit"


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
        allow_legacy_signed: bool = False,
    ) -> Transaction:
        posting_book_id = self._validate_transaction_postings(
            postings,
            book_id=book_id,
            allow_legacy_signed=allow_legacy_signed,
        )
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

    def validate_transaction_integrity(
        self,
        transaction: Transaction,
        *,
        enforce_asset_scale: bool = True,
        allow_legacy_signed: bool = True,
    ) -> None:
        self._validate_transaction_postings(
            transaction.postings,
            book_id=transaction.book_id,
            enforce_asset_scale=enforce_asset_scale,
            allow_legacy_signed=allow_legacy_signed,
        )

    def _validate_transaction_postings(
        self,
        postings: list[Posting],
        *,
        book_id: str | None = None,
        enforce_asset_scale: bool = True,
        allow_legacy_signed: bool = False,
    ) -> str:
        if len(postings) < 2:
            raise ValidationError("confirmed transaction requires at least two postings")
        semantics = {posting.amount_semantics for posting in postings}
        if len(semantics) != 1:
            raise ValidationError("transaction postings must not mix legacy signed and debit/credit semantics")
        amount_semantics = validate_posting_amount_semantics(next(iter(semantics)))
        if amount_semantics == "legacy_signed" and not allow_legacy_signed:
            raise ValidationError("new confirmed transactions must use debit_credit semantics")
        legacy_totals: dict[str, Decimal] = {}
        debit_credit_totals: dict[str, dict[PostingSide, Decimal]] = {}
        posting_book_id: str | None = book_id
        system_adjustment_posting_count = 0
        for posting in postings:
            if amount_semantics == "legacy_signed":
                if posting.amount == Decimal("0"):
                    raise ValidationError("posting amount must not be zero")
            else:
                validate_positive_posting_amount(posting.amount)
                if posting.side is None:
                    raise ValidationError("debit/credit posting requires side")
                validate_posting_side(posting.side)
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
            if amount_semantics == "legacy_signed":
                legacy_totals[posting.currency] = legacy_totals.get(posting.currency, Decimal("0")) + posting.amount
            else:
                side = validate_posting_side(posting.side)
                side_totals = debit_credit_totals.setdefault(posting.currency, {"debit": Decimal("0"), "credit": Decimal("0")})
                side_totals[side] = side_totals.get(side, Decimal("0")) + posting.amount
        unbalanced = (
            {currency: total for currency, total in legacy_totals.items() if total != Decimal("0")}
            if amount_semantics == "legacy_signed"
            else debit_credit_balanced(debit_credit_totals)
        )
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
                    account = self.get_account(posting.account_id)
                    amount = posting_balance_delta(
                        account.type,
                        side=posting.side,
                        amount=posting.amount,
                        amount_semantics=posting.amount_semantics,
                    )
                    totals[posting.currency] = totals.get(posting.currency, Decimal("0")) + amount
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
                reverse_posting_for_account(posting, self.get_account(posting.account_id).type)
                for posting in transaction.postings
            ],
            book_id=transaction.book_id,
            reverses_transaction_id=transaction.transaction_id,
        )
        transaction.reversed_by = reversal.transaction_id
        return reversal
