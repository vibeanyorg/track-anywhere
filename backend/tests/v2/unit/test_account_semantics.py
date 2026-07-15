from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from track_anywhere.application.catalogs.create_account import CreateAccount
from track_anywhere.domain.journal import (
    AccountCatalogSnapshot,
    AccountSnapshot,
    AccountType,
    InvalidAccountCatalog,
    PostingSide,
)
from track_anywhere.queries.balances import build_balance_item


def test_account_type_is_the_closed_ledger_direction_set() -> None:
    assert {account_type.value for account_type in AccountType} == {
        "asset",
        "liability",
        "equity",
        "income",
        "expense",
        "fund",
        "system",
    }


def test_create_account_accepts_a_nullable_lowercase_slug_subtype() -> None:
    command = CreateAccount(
        book_id=uuid4(),
        account_id=uuid4(),
        asset_code="USD",
        account_type="liability",
        account_subtype="credit_card",
        current_name="Card",
    )

    assert command.account_type == "liability"
    assert command.account_subtype == "credit_card"


@pytest.mark.parametrize(
    ("account_type", "account_subtype"),
    (
        ("receivable", None),
        ("ASSET", None),
        ("liability", "Credit_Card"),
        ("liability", "credit-card"),
        ("liability", "_credit_card"),
        ("liability", "credit__card"),
        ("liability", "credit_card_"),
        ("liability", ""),
        ("asset", "credit_card"),
    ),
)
def test_create_account_fails_closed_for_unknown_type_or_invalid_subtype(
    account_type: str,
    account_subtype: str | None,
) -> None:
    with pytest.raises(ValueError):
        CreateAccount(
            book_id=uuid4(),
            account_id=uuid4(),
            asset_code="USD",
            account_type=account_type,
            account_subtype=account_subtype,
            current_name="Invalid",
        )


def test_journal_account_snapshot_carries_validated_type_and_subtype() -> None:
    account = AccountSnapshot(
        account_id="card",
        book_id="book",
        asset_code="USD",
        account_type=AccountType.LIABILITY,
        account_subtype="credit_card",
    )

    AccountCatalogSnapshot(accounts=(account,)).resolve("book", "card")

    with pytest.raises(InvalidAccountCatalog, match="runtime shape"):
        AccountCatalogSnapshot(
            accounts=(replace(account, account_type="liability"),)  # type: ignore[arg-type]
        ).resolve("book", "card")
    with pytest.raises(InvalidAccountCatalog, match="runtime shape"):
        AccountCatalogSnapshot(
            accounts=(replace(account, account_subtype="Credit_Card"),)
        ).resolve("book", "card")
    with pytest.raises(InvalidAccountCatalog, match="runtime shape"):
        AccountCatalogSnapshot(
            accounts=(replace(account, account_type=AccountType.ASSET),)
        ).resolve("book", "card")


@pytest.mark.parametrize(
    ("account_type", "raw_units", "normal_side"),
    (
        (AccountType.ASSET, 100, PostingSide.DEBIT),
        (AccountType.EXPENSE, 100, PostingSide.DEBIT),
        (AccountType.FUND, 100, PostingSide.DEBIT),
        (AccountType.SYSTEM, 100, PostingSide.DEBIT),
        (AccountType.LIABILITY, -100, PostingSide.CREDIT),
        (AccountType.EQUITY, -100, PostingSide.CREDIT),
        (AccountType.INCOME, -100, PostingSide.CREDIT),
    ),
)
def test_natural_balance_uses_each_account_types_normal_side(
    account_type: AccountType,
    raw_units: int,
    normal_side: PostingSide,
) -> None:
    item = build_balance_item(
        account_id=uuid4(),
        asset_code="USD",
        account_type=account_type,
        account_status="active",
        raw_accounting_units=raw_units,
    )

    assert item.account_status == "active"
    assert item.raw_accounting_units == raw_units
    assert item.natural_units == 100
    assert item.normal_side is normal_side
    assert item.balance_semantics == f"natural_{account_type.value}_balance"
    if account_type is AccountType.LIABILITY:
        assert item.outstanding_units == 100
        assert item.overpayment_units == 0
    else:
        assert item.outstanding_units is None
        assert item.overpayment_units is None
