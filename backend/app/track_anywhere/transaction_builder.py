from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from .assets import default_asset_definition, validate_asset_amount
from .books import DEFAULT_BOOK_ID
from .errors import NotFound, ValidationError
from .ledger import Account, Posting, Transaction, TransactionLine
from .accounting import (
    PostingSide,
    debit_credit_balanced,
    validate_positive_posting_amount,
    validate_posting_amount_semantics,
    validate_posting_side,
)


VALID_LINE_TYPES = {
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


def build_transaction(
    *,
    memo: str,
    postings: list[Posting],
    accounts: Iterable[Account],
    occurred_at: datetime | None = None,
    purpose: str | None = None,
    book_id: str | None = None,
    reverses_transaction_id: str | None = None,
    scale_lookup: Callable[[str], int] | None = None,
    allow_legacy_signed: bool = False,
) -> Transaction:
    account_by_id = {account.account_id: account for account in accounts}
    posting_book_id = validate_transaction_postings(
        postings,
        accounts=account_by_id,
        book_id=book_id,
        scale_lookup=scale_lookup,
        allow_legacy_signed=allow_legacy_signed,
    )
    return Transaction(
        transaction_id=f"txn_{uuid4().hex}",
        memo=memo,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        purpose=purpose or memo,
        postings=postings,
        book_id=posting_book_id,
        lines=[],
        reverses_transaction_id=reverses_transaction_id,
    )


def validate_transaction_postings(
    postings: list[Posting],
    *,
    accounts: dict[str, Account],
    book_id: str | None = None,
    scale_lookup: Callable[[str], int] | None = None,
    allow_legacy_signed: bool = False,
) -> str:
    scale_lookup = scale_lookup or (lambda code: default_asset_definition(code).scale)
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
        validate_asset_amount(
            posting.amount,
            posting.currency,
            field_name="posting amount",
            scale_lookup=scale_lookup,
        )
        account = accounts.get(posting.account_id)
        if account is None:
            raise NotFound(f"account not found: {posting.account_id}")
        if posting.currency != account.currency:
            raise ValidationError(
                f"posting currency {posting.currency} does not match "
                f"account {posting.account_id} currency {account.currency}"
            )
        if posting_book_id is None:
            posting_book_id = account.book_id
        elif account.book_id != posting_book_id:
            raise ValidationError("transaction postings must belong to one book")
        if _is_system_adjustment_account(account):
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
    if system_adjustment_posting_count and (len(postings) != 2 or system_adjustment_posting_count != 1):
        raise ValidationError(
            "balance adjustment transaction requires exactly two postings: "
            "one account posting and one system adjustment posting"
        )
    return posting_book_id or DEFAULT_BOOK_ID


def add_transaction_line(
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
    scale_lookup: Callable[[str], int] | None = None,
) -> TransactionLine:
    scale_lookup = scale_lookup or (lambda code: default_asset_definition(code).scale)
    if line_type not in VALID_LINE_TYPES:
        raise ValidationError("transaction line type is invalid")
    if amount <= Decimal("0"):
        raise ValidationError("transaction line amount must be positive")
    validate_asset_amount(amount, currency, field_name="transaction line amount", scale_lookup=scale_lookup)
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


def _is_system_adjustment_account(account: Account) -> bool:
    return (
        account.type == "system"
        and account.institution_type == "system"
        and account.subtype == "system_adjustment"
        and account.institution == "track-anywhere"
    )
