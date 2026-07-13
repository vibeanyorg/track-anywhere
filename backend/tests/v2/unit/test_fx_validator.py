from __future__ import annotations

from dataclasses import replace

import pytest

from track_anywhere.domain.journal import (
    AccountCatalogSnapshot,
    AccountSnapshot,
    AccountSystemRole,
    InvalidFxTransaction,
    JournalValidator,
    PostingDraft,
    PostingSide,
    PostTransaction,
    TransactionKind,
)


BOOK_ID = "book-main"


def _account(
    account_id: str,
    asset_code: str,
    *,
    system_role: AccountSystemRole = AccountSystemRole.STANDARD,
) -> AccountSnapshot:
    return AccountSnapshot(
        account_id=account_id,
        book_id=BOOK_ID,
        asset_code=asset_code,
        system_role=system_role,
    )


@pytest.fixture
def catalog() -> AccountCatalogSnapshot:
    return AccountCatalogSnapshot(
        accounts=(
            _account("usd-wallet", "USD"),
            _account("usd-settlement", "USD"),
            _account(
                "usd-trading",
                "USD",
                system_role=AccountSystemRole.FX_TRADING,
            ),
            _account("cny-bank", "CNY"),
            _account("cny-wallet", "CNY"),
            _account(
                "cny-trading",
                "CNY",
                system_role=AccountSystemRole.FX_TRADING,
            ),
        )
    )


def _posting(
    posting_id: str,
    position: int,
    account_id: str,
    asset_code: str,
    side: PostingSide,
    units: int,
) -> PostingDraft:
    return PostingDraft(
        posting_id=posting_id,
        position=position,
        account_id=account_id,
        asset_code=asset_code,
        side=side,
        units=units,
    )


def _canonical_fx_command() -> PostTransaction:
    return PostTransaction(
        transaction_id="txn-fx",
        book_id=BOOK_ID,
        kind=TransactionKind.FX,
        postings=(
            _posting(
                "p-usd-wallet",
                0,
                "usd-wallet",
                "USD",
                PostingSide.DEBIT,
                10_000,
            ),
            _posting(
                "p-usd-trading",
                1,
                "usd-trading",
                "USD",
                PostingSide.CREDIT,
                10_000,
            ),
            _posting(
                "p-cny-trading",
                2,
                "cny-trading",
                "CNY",
                PostingSide.DEBIT,
                70_000,
            ),
            _posting(
                "p-cny-bank",
                3,
                "cny-bank",
                "CNY",
                PostingSide.CREDIT,
                70_000,
            ),
        ),
    )


def test_accepts_the_canonical_four_leg_cny_usd_fx_journal(
    catalog: AccountCatalogSnapshot,
) -> None:
    assert JournalValidator.validate(_canonical_fx_command(), catalog=catalog) is None


@pytest.mark.parametrize(
    ("replacement_accounts", "missing_asset"),
    [
        ({"usd-trading": "usd-settlement"}, "USD"),
        ({"cny-trading": "cny-wallet"}, "CNY"),
        (
            {
                "usd-trading": "usd-settlement",
                "cny-trading": "cny-wallet",
            },
            "CNY, USD",
        ),
    ],
)
def test_fx_requires_a_book_owned_trading_leg_for_every_exchanged_asset(
    catalog: AccountCatalogSnapshot,
    replacement_accounts: dict[str, str],
    missing_asset: str,
) -> None:
    command = _canonical_fx_command()
    postings = tuple(
        replace(
            posting,
            account_id=replacement_accounts.get(posting.account_id, posting.account_id),
        )
        for posting in command.postings
    )

    with pytest.raises(InvalidFxTransaction) as error:
        JournalValidator.validate(replace(command, postings=postings), catalog=catalog)

    for asset_code in missing_asset.split(", "):
        assert asset_code in str(error.value)


def test_fx_requires_a_real_multi_asset_exchange(
    catalog: AccountCatalogSnapshot,
) -> None:
    command = PostTransaction(
        transaction_id="txn-not-an-exchange",
        book_id=BOOK_ID,
        kind=TransactionKind.FX,
        postings=(
            _posting(
                "p-usd-wallet",
                0,
                "usd-wallet",
                "USD",
                PostingSide.DEBIT,
                10_000,
            ),
            _posting(
                "p-usd-trading",
                1,
                "usd-trading",
                "USD",
                PostingSide.CREDIT,
                10_000,
            ),
        ),
    )

    with pytest.raises(InvalidFxTransaction, match="at least two assets"):
        JournalValidator.validate(command, catalog=catalog)


def test_fx_rejects_trading_nets_that_all_have_the_same_direction(
    catalog: AccountCatalogSnapshot,
) -> None:
    command = _canonical_fx_command()
    postings = tuple(
        replace(posting, side=PostingSide.CREDIT)
        if posting.account_id == "cny-trading"
        else replace(posting, side=PostingSide.DEBIT)
        if posting.account_id == "cny-bank"
        else posting
        for posting in command.postings
    )

    with pytest.raises(InvalidFxTransaction, match="opposite directions") as error:
        JournalValidator.validate(replace(command, postings=postings), catalog=catalog)

    assert "CNY" in str(error.value)
    assert "USD" in str(error.value)


def test_fx_rejects_an_asset_with_zero_net_trading_units(
    catalog: AccountCatalogSnapshot,
) -> None:
    command = PostTransaction(
        transaction_id="txn-zero-trading-net",
        book_id=BOOK_ID,
        kind=TransactionKind.FX,
        postings=(
            _posting(
                "p-usd-wallet",
                0,
                "usd-wallet",
                "USD",
                PostingSide.DEBIT,
                10_000,
            ),
            _posting(
                "p-usd-trading",
                1,
                "usd-trading",
                "USD",
                PostingSide.CREDIT,
                10_000,
            ),
            _posting(
                "p-cny-trading-debit",
                2,
                "cny-trading",
                "CNY",
                PostingSide.DEBIT,
                70_000,
            ),
            _posting(
                "p-cny-trading-credit",
                3,
                "cny-trading",
                "CNY",
                PostingSide.CREDIT,
                70_000,
            ),
        ),
    )

    with pytest.raises(InvalidFxTransaction, match="nonzero") as error:
        JournalValidator.validate(command, catalog=catalog)

    assert "CNY" in str(error.value)


def test_fx_accepts_split_trading_legs_with_opposite_nonzero_asset_nets(
    catalog: AccountCatalogSnapshot,
) -> None:
    command = PostTransaction(
        transaction_id="txn-split-fx",
        book_id=BOOK_ID,
        kind=TransactionKind.FX,
        postings=(
            _posting(
                "p-usd-wallet",
                0,
                "usd-wallet",
                "USD",
                PostingSide.DEBIT,
                10_000,
            ),
            _posting(
                "p-usd-trading-1",
                1,
                "usd-trading",
                "USD",
                PostingSide.CREDIT,
                7_000,
            ),
            _posting(
                "p-usd-trading-2",
                2,
                "usd-trading",
                "USD",
                PostingSide.CREDIT,
                3_000,
            ),
            _posting(
                "p-cny-trading-1",
                3,
                "cny-trading",
                "CNY",
                PostingSide.DEBIT,
                40_000,
            ),
            _posting(
                "p-cny-trading-2",
                4,
                "cny-trading",
                "CNY",
                PostingSide.DEBIT,
                30_000,
            ),
            _posting(
                "p-cny-bank",
                5,
                "cny-bank",
                "CNY",
                PostingSide.CREDIT,
                70_000,
            ),
        ),
    )

    assert JournalValidator.validate(command, catalog=catalog) is None


def test_non_fx_balanced_multi_asset_journal_does_not_require_trading_accounts(
    catalog: AccountCatalogSnapshot,
) -> None:
    command = replace(
        _canonical_fx_command(),
        kind=TransactionKind.STANDARD,
        postings=(
            replace(
                _canonical_fx_command().postings[0],
                account_id="usd-wallet",
            ),
            replace(
                _canonical_fx_command().postings[1],
                account_id="usd-settlement",
            ),
            replace(
                _canonical_fx_command().postings[2],
                account_id="cny-wallet",
            ),
            replace(
                _canonical_fx_command().postings[3],
                account_id="cny-bank",
            ),
        ),
    )

    assert JournalValidator.validate(command, catalog=catalog) is None
