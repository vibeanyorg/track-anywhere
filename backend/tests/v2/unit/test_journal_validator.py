from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from track_anywhere.domain.journal import (
    AccountCatalogSnapshot,
    AccountSnapshot,
    AccountSystemRole,
    AccountType,
    CrossBookAccount,
    DuplicatePostingId,
    DuplicatePostingPosition,
    InvalidAccountCatalog,
    InvalidJournalCommand,
    InvalidPostingUnits,
    JournalValidator,
    PostingAssetMismatch,
    PostingDraft,
    PostingSide,
    PostingValidationError,
    PostTransaction,
    TooFewPostings,
    TransactionKind,
    UnbalancedAsset,
    UnknownAccount,
)


BOOK_ID = "book-main"
OTHER_BOOK_ID = "book-other"


def _account(
    account_id: str,
    asset_code: str,
    *,
    book_id: str = BOOK_ID,
    system_role: AccountSystemRole = AccountSystemRole.STANDARD,
) -> AccountSnapshot:
    return AccountSnapshot(
        account_id=account_id,
        book_id=book_id,
        asset_code=asset_code,
        account_type=AccountType.ASSET,
        system_role=system_role,
    )


@pytest.fixture
def catalog() -> AccountCatalogSnapshot:
    return AccountCatalogSnapshot(
        accounts=(
            _account("cny-wallet", "CNY"),
            _account("cny-bank", "CNY"),
            _account("usd-wallet", "USD"),
            _account("usd-bank", "USD"),
            _account("other-cny", "CNY", book_id=OTHER_BOOK_ID),
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


def _balanced_cny_command() -> PostTransaction:
    return PostTransaction(
        transaction_id="txn-1",
        book_id=BOOK_ID,
        kind=TransactionKind.STANDARD,
        postings=(
            _posting("p-1", 0, "cny-wallet", "CNY", PostingSide.DEBIT, 1_000),
            _posting("p-2", 1, "cny-bank", "CNY", PostingSide.CREDIT, 1_000),
        ),
    )


def test_accepts_a_balanced_single_asset_journal(
    catalog: AccountCatalogSnapshot,
) -> None:
    command = _balanced_cny_command()

    result = JournalValidator.validate(command, catalog=catalog)

    assert result is None
    assert command == _balanced_cny_command()


def test_rejects_fewer_than_two_postings(catalog: AccountCatalogSnapshot) -> None:
    command = replace(
        _balanced_cny_command(),
        postings=_balanced_cny_command().postings[:1],
    )

    with pytest.raises(TooFewPostings, match="at least two"):
        JournalValidator.validate(command, catalog=catalog)


def test_rejects_duplicate_posting_ids_independently(
    catalog: AccountCatalogSnapshot,
) -> None:
    first, second = _balanced_cny_command().postings
    command = replace(
        _balanced_cny_command(),
        postings=(first, replace(second, posting_id=first.posting_id)),
    )

    with pytest.raises(DuplicatePostingId, match="p-1"):
        JournalValidator.validate(command, catalog=catalog)


def test_rejects_duplicate_posting_positions_independently(
    catalog: AccountCatalogSnapshot,
) -> None:
    first, second = _balanced_cny_command().postings
    command = replace(
        _balanced_cny_command(),
        postings=(first, replace(second, position=first.position)),
    )

    with pytest.raises(DuplicatePostingPosition, match="0"):
        JournalValidator.validate(command, catalog=catalog)


@pytest.mark.parametrize("units", [0, -1, 1.0, True, "1000"])
def test_rejects_nonpositive_or_noninteger_units(
    catalog: AccountCatalogSnapshot,
    units: object,
) -> None:
    first, second = _balanced_cny_command().postings
    command = replace(
        _balanced_cny_command(),
        postings=(replace(first, units=units), second),  # type: ignore[arg-type]
    )

    with pytest.raises(InvalidPostingUnits, match="positive integer"):
        JournalValidator.validate(command, catalog=catalog)


def test_accepts_balanced_postings_at_the_38_digit_unit_boundary(
    catalog: AccountCatalogSnapshot,
) -> None:
    boundary_units = 10**38 - 1
    first, second = _balanced_cny_command().postings
    command = replace(
        _balanced_cny_command(),
        postings=(
            replace(first, units=boundary_units),
            replace(second, units=boundary_units),
        ),
    )

    assert JournalValidator.validate(command, catalog=catalog) is None


@pytest.mark.parametrize(
    "units",
    [10**38, 10**5_000],
    ids=["39-digits", "huge"],
)
def test_rejects_posting_units_beyond_the_38_digit_boundary(
    catalog: AccountCatalogSnapshot,
    units: int,
) -> None:
    first, second = _balanced_cny_command().postings
    command = replace(
        _balanced_cny_command(),
        postings=(replace(first, units=units), replace(second, units=units)),
    )

    with pytest.raises(InvalidPostingUnits, match="at most 38 digits"):
        JournalValidator.validate(command, catalog=catalog)


def test_rejects_an_unknown_account(catalog: AccountCatalogSnapshot) -> None:
    first, second = _balanced_cny_command().postings
    command = replace(
        _balanced_cny_command(),
        postings=(replace(first, account_id="missing-account"), second),
    )

    with pytest.raises(UnknownAccount, match="missing-account"):
        JournalValidator.validate(command, catalog=catalog)


def test_rejects_an_account_from_another_book(
    catalog: AccountCatalogSnapshot,
) -> None:
    first, second = _balanced_cny_command().postings
    command = replace(
        _balanced_cny_command(),
        postings=(replace(first, account_id="other-cny"), second),
    )

    with pytest.raises(CrossBookAccount, match="other-cny"):
        JournalValidator.validate(command, catalog=catalog)


def test_composite_account_identity_allows_the_same_id_in_multiple_books() -> None:
    current_wallet = _account("shared-wallet", "CNY")
    other_wallet = _account(
        "shared-wallet",
        "USD",
        book_id=OTHER_BOOK_ID,
    )
    catalog = AccountCatalogSnapshot(
        accounts=(
            other_wallet,
            current_wallet,
            _account("cny-bank", "CNY"),
        )
    )
    command = PostTransaction(
        transaction_id="txn-composite-account",
        book_id=BOOK_ID,
        kind=TransactionKind.STANDARD,
        postings=(
            _posting(
                "p-wallet",
                0,
                "shared-wallet",
                "CNY",
                PostingSide.DEBIT,
                1_000,
            ),
            _posting(
                "p-bank",
                1,
                "cny-bank",
                "CNY",
                PostingSide.CREDIT,
                1_000,
            ),
        ),
    )

    assert catalog.resolve(BOOK_ID, "shared-wallet") is current_wallet
    assert catalog.resolve(OTHER_BOOK_ID, "shared-wallet") is other_wallet
    assert JournalValidator.validate(command, catalog=catalog) is None


def test_duplicate_exact_composite_account_identity_fails_closed() -> None:
    catalog = AccountCatalogSnapshot(
        accounts=(
            _account("cny-wallet", "CNY"),
            _account("cny-wallet", "CNY"),
            _account("cny-bank", "CNY"),
        )
    )

    with pytest.raises(InvalidAccountCatalog, match="book-main.*cny-wallet"):
        catalog.resolve(BOOK_ID, "cny-wallet")
    with pytest.raises(InvalidAccountCatalog, match="book-main.*cny-wallet"):
        JournalValidator.validate(_balanced_cny_command(), catalog=catalog)


def test_only_other_book_composite_account_still_raises_cross_book() -> None:
    catalog = AccountCatalogSnapshot(
        accounts=(
            _account("shared-wallet", "CNY", book_id=OTHER_BOOK_ID),
            _account("cny-bank", "CNY"),
        )
    )
    first, second = _balanced_cny_command().postings
    command = replace(
        _balanced_cny_command(),
        postings=(replace(first, account_id="shared-wallet"), second),
    )

    with pytest.raises(CrossBookAccount, match="shared-wallet"):
        JournalValidator.validate(command, catalog=catalog)


def test_rejects_asset_code_that_disagrees_with_the_account(
    catalog: AccountCatalogSnapshot,
) -> None:
    first, second = _balanced_cny_command().postings
    command = replace(
        _balanced_cny_command(),
        postings=(replace(first, asset_code="USD"), second),
    )

    with pytest.raises(PostingAssetMismatch, match="cny-wallet"):
        JournalValidator.validate(command, catalog=catalog)


def test_balances_each_asset_independently_and_names_the_unbalanced_assets(
    catalog: AccountCatalogSnapshot,
) -> None:
    command = PostTransaction(
        transaction_id="txn-unbalanced",
        book_id=BOOK_ID,
        kind=TransactionKind.STANDARD,
        postings=(
            _posting("p-usd", 0, "usd-wallet", "USD", PostingSide.DEBIT, 10_000),
            _posting("p-cny", 1, "cny-bank", "CNY", PostingSide.CREDIT, 70_000),
        ),
    )

    with pytest.raises(UnbalancedAsset) as error:
        JournalValidator.validate(command, catalog=catalog)

    assert error.value.assets == ("CNY", "USD")
    assert "CNY" in str(error.value)
    assert "USD" in str(error.value)


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("side", "debit", PostingValidationError),
        ("position", True, PostingValidationError),
        ("posting_id", 1, PostingValidationError),
        ("account_id", None, PostingValidationError),
        ("asset_code", "", PostingValidationError),
    ],
)
def test_posting_runtime_types_fail_closed(
    catalog: AccountCatalogSnapshot,
    field: str,
    value: object,
    error_type: type[Exception],
) -> None:
    first, second = _balanced_cny_command().postings
    invalid = replace(first, **{field: value})
    command = replace(_balanced_cny_command(), postings=(invalid, second))

    with pytest.raises(error_type):
        JournalValidator.validate(command, catalog=catalog)


def test_transaction_kind_runtime_type_fails_closed(
    catalog: AccountCatalogSnapshot,
) -> None:
    command = replace(_balanced_cny_command(), kind="standard")  # type: ignore[arg-type]

    with pytest.raises(InvalidJournalCommand, match="transaction kind"):
        JournalValidator.validate(command, catalog=catalog)


def test_transaction_kind_contract_has_only_the_approved_values() -> None:
    assert {kind.value for kind in TransactionKind} == {
        "standard",
        "opening",
        "adjustment",
        "transfer",
        "fx",
        "investment_cash",
    }
    assert not hasattr(TransactionKind, "GENERAL")


def test_command_and_posting_container_runtime_types_fail_closed(
    catalog: AccountCatalogSnapshot,
) -> None:
    with pytest.raises(InvalidJournalCommand):
        JournalValidator.validate(object(), catalog=catalog)  # type: ignore[arg-type]

    command = replace(
        _balanced_cny_command(),
        postings=list(_balanced_cny_command().postings),  # type: ignore[arg-type]
    )
    with pytest.raises(InvalidJournalCommand, match="tuple"):
        JournalValidator.validate(command, catalog=catalog)

    command = replace(
        _balanced_cny_command(),
        postings=(object(), _balanced_cny_command().postings[1]),  # type: ignore[arg-type]
    )
    with pytest.raises(PostingValidationError):
        JournalValidator.validate(command, catalog=catalog)


def test_journal_commands_and_catalog_are_immutable_stable_values(
    catalog: AccountCatalogSnapshot,
) -> None:
    command = _balanced_cny_command()

    assert isinstance(command.postings, tuple)
    assert not hasattr(command, "__dict__")
    assert not hasattr(command.postings[0], "__dict__")
    assert not hasattr(catalog, "__dict__")
    with pytest.raises(FrozenInstanceError):
        command.book_id = OTHER_BOOK_ID  # type: ignore[misc]


def test_validator_module_has_no_persistence_or_framework_imports() -> None:
    from track_anywhere.domain.journal import validators

    tree = ast.parse(Path(validators.__file__).read_text(encoding="utf-8"))
    imported_modules = {
        module
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for module in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
        )
    }

    forbidden_fragments = (
        "sqlalchemy",
        "storage",
        "repository",
        "service",
        "fastapi",
        "api",
    )
    assert not any(
        fragment in module
        for module in imported_modules
        for fragment in forbidden_fragments
    )
