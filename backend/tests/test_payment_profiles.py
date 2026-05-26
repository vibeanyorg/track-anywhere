from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest

from track_anywhere.errors import NotFound, ValidationError
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def _setup_safepal_payment_profile(*, opening_balance: str = "277.44", idempotency_prefix: str = "safepal"):
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token

    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Food"},
        idempotency_key=f"{idempotency_prefix}-food-category",
    )
    card, _ = service.create_account(
        token,
        {
            "name": "SafePal Card USD(5964)",
            "type": "asset",
            "currency": "USD",
        },
        idempotency_key=f"{idempotency_prefix}-card-account",
    )
    usd24, _ = service.create_account(
        token,
        {
            "name": "SafePal USD24 (Arbitrum)",
            "type": "asset",
            "currency": "USD24",
            "opening_balance": opening_balance,
        },
        idempotency_key=f"{idempotency_prefix}-usd24-account",
    )
    service.create_payment_profile(
        token,
        {
            "slug": "safepal",
            "display_name": "SafePal",
            "kind": "token_backed_card",
            "instrument_account_id": card.account_id,
            "backing_account_id": usd24.account_id,
            "settlement_mode": "immediate",
            "settlement_rate": "1",
        },
        idempotency_key=f"{idempotency_prefix}-payment-profile",
    )
    return service, token, category, card, usd24


def _system_account_id(service: FinanceService, *, account_type: str, currency: str, subtype: str) -> str:
    for account in service.ledger.accounts.values():
        if (
            account.type == account_type
            and account.currency == currency
            and account.subtype == subtype
            and account.institution_type == "system"
            and account.institution == "track-anywhere"
        ):
            return account.account_id
    raise AssertionError(f"system account not found: type={account_type}, currency={currency}, subtype={subtype}")


def test_payment_profile_create_and_lookup():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token
    card, _ = service.create_account(
        token,
        {
            "name": "SafePal Card USD(5964)",
            "type": "asset",
            "currency": "USD",
        },
        idempotency_key="create-profile-card",
    )
    usd24, _ = service.create_account(
        token,
        {
            "name": "SafePal USD24 (Arbitrum)",
            "type": "asset",
            "currency": "USD24",
        },
        idempotency_key="create-profile-usd24",
    )
    profile, _ = service.create_payment_profile(
        token,
        {
            "slug": "safepal",
            "display_name": "SafePal",
            "kind": "token_backed_card",
            "instrument_account_id": card.account_id,
            "backing_account_id": usd24.account_id,
            "settlement_mode": "immediate",
            "settlement_rate": "1",
        },
        idempotency_key="create-profile",
    )

    assert profile.slug == "safepal"
    assert profile.instrument_currency == "USD"
    assert profile.backing_currency == "USD24"
    assert service.get_payment_profile(token, profile.profile_id).profile_id == profile.profile_id
    assert service.resolve_payment_profile(token, profile.slug).profile_id == profile.profile_id
    assert service.list_payment_profiles(token) == [profile]


def test_payment_profile_slug_must_be_unique_per_book():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token
    card, _ = service.create_account(
        token,
        {
            "name": "SafePal Card USD(5964)",
            "type": "asset",
            "currency": "USD",
        },
        idempotency_key="duplicate-card",
    )
    usd24, _ = service.create_account(
        token,
        {
            "name": "SafePal USD24 (Arbitrum)",
            "type": "asset",
            "currency": "USD24",
        },
        idempotency_key="duplicate-usd24",
    )
    service.create_payment_profile(
        token,
        {
            "slug": "safepal",
            "display_name": "SafePal",
            "kind": "token_backed_card",
            "instrument_account_id": card.account_id,
            "backing_account_id": usd24.account_id,
            "settlement_mode": "immediate",
            "settlement_rate": "1",
        },
        idempotency_key="duplicate-profile-1",
    )

    with pytest.raises(ValidationError, match="payment profile slug already exists"):
        service.create_payment_profile(
            token,
            {
                "slug": "safepal",
                "display_name": "SafePal #2",
                "kind": "token_backed_card",
                "instrument_account_id": card.account_id,
                "backing_account_id": usd24.account_id,
                "settlement_mode": "immediate",
                "settlement_rate": "1",
            },
            idempotency_key="duplicate-profile-2",
        )


def test_payment_profile_create_replay_returns_existing_profile():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token
    card, _ = service.create_account(
        token,
        {
            "name": "SafePal Card USD(5964)",
            "type": "asset",
            "currency": "USD",
        },
        idempotency_key="profile-replay-card",
    )
    usd24, _ = service.create_account(
        token,
        {
            "name": "SafePal USD24 (Arbitrum)",
            "type": "asset",
            "currency": "USD24",
        },
        idempotency_key="profile-replay-usd24",
    )
    payload = {
        "slug": "safepal",
        "display_name": "SafePal",
        "kind": "token_backed_card",
        "instrument_account_id": card.account_id,
        "backing_account_id": usd24.account_id,
        "settlement_mode": "immediate",
        "settlement_rate": "1",
    }

    profile, replay = service.create_payment_profile(token, payload, idempotency_key="profile-replay")
    replay_profile, replay_again = service.create_payment_profile(token, payload, idempotency_key="profile-replay")

    assert replay is False
    assert replay_again is True
    assert replay_profile == profile
    assert service.list_payment_profiles(token) == [profile]


def test_payment_profile_resolve_id_is_scoped_to_book():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token
    travel_book, _ = service.create_book(
        token,
        {"name": "Travel", "kind": "travel", "base_currency": "USD"},
        idempotency_key="profile-book-scope-book",
    )
    card, _ = service.create_book_account(
        token,
        travel_book.book_id,
        {
            "name": "SafePal Card USD(5964)",
            "type": "asset",
            "currency": "USD",
        },
        idempotency_key="profile-book-scope-card",
    )
    usd24, _ = service.create_book_account(
        token,
        travel_book.book_id,
        {
            "name": "SafePal USD24 (Arbitrum)",
            "type": "asset",
            "currency": "USD24",
        },
        idempotency_key="profile-book-scope-usd24",
    )
    profile, _ = service.create_payment_profile(
        token,
        {
            "slug": "safepal",
            "display_name": "SafePal",
            "kind": "token_backed_card",
            "instrument_account_id": card.account_id,
            "backing_account_id": usd24.account_id,
            "settlement_mode": "immediate",
            "settlement_rate": "1",
        },
        idempotency_key="profile-book-scope-profile",
    )

    assert service.resolve_payment_profile(token, profile.profile_id, book_id=travel_book.book_id) == profile
    with pytest.raises(NotFound):
        service.resolve_payment_profile(token, profile.profile_id)


def test_payment_profile_persists_across_service_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere-payment-profile.sqlite3'}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = first.owner_token

    card, _ = first.create_account(
        token,
        {
            "name": "SafePal Card USD(5964)",
            "type": "asset",
            "currency": "USD",
        },
        idempotency_key="persist-profile-card",
    )
    usd24, _ = first.create_account(
        token,
        {
            "name": "SafePal USD24 (Arbitrum)",
            "type": "asset",
            "currency": "USD24",
            "opening_balance": "10.00",
        },
        idempotency_key="persist-profile-usd24",
    )
    profile, _ = first.create_payment_profile(
        token,
        {
            "slug": "safepal",
            "display_name": "SafePal",
            "kind": "token_backed_card",
            "instrument_account_id": card.account_id,
            "backing_account_id": usd24.account_id,
            "settlement_mode": "immediate",
            "settlement_rate": "1",
        },
        idempotency_key="persist-profile",
    )

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    profiles = second.list_payment_profiles(token)
    assert profiles == [profile]
    assert second.get_payment_profile(token, profile.profile_id).backing_account_id == usd24.account_id
    assert second.resolve_payment_profile(token, profile.slug).slug == "safepal"


def _assert_backed_card_postings(
    service: FinanceService,
    transaction,
    *,
    card_account_id: str,
    usd24_account_id: str,
) -> None:
    assert len(transaction.postings) == 6
    assert [line.line_type for line in transaction.lines] == ["expense", "fx_exchange"]

    actual = Counter((posting.account_id, posting.amount, posting.currency) for posting in transaction.postings)
    expected = Counter(
        {
            (card_account_id, Decimal("-3.40"), "USD"): 1,
            (usd24_account_id, Decimal("-3.40"), "USD24"): 1,
            (card_account_id, Decimal("3.40"), "USD"): 1,
            (
                _system_account_id(service, account_type="expense", currency="USD", subtype="expense_clearing"),
                Decimal("3.40"),
                "USD",
            ): 1,
            (
                _system_account_id(service, account_type="system", currency="USD24", subtype="fx_clearing"),
                Decimal("3.40"),
                "USD24",
            ): 1,
            (
                _system_account_id(service, account_type="system", currency="USD", subtype="fx_clearing"),
                Decimal("-3.40"),
                "USD",
            ): 1,
        },
    )
    assert actual == expected


def test_token_backed_card_expense_settles_immediately():
    service, token, category, card, usd24 = _setup_safepal_payment_profile(idempotency_prefix="safepal")

    transaction, _ = service.record_payment_profile_expense(
        token,
        {
            "payment": "safepal",
            "amount": "3.40",
            "currency": "USD",
            "category_id": category.category_id,
            "purpose": "meituan",
        },
        idempotency_key="safepal-expense",
    )

    assert service.account_balance(token, card.account_id)["official_balance"]["amount"] == "0.00"
    assert service.account_balance(token, usd24.account_id)["official_balance"]["amount"] == "274.04"
    assert service.category_summary(token, kind="expense", currency="USD")["groups"][0]["amount"] == "3.40"
    _assert_backed_card_postings(
        service,
        transaction,
        card_account_id=card.account_id,
        usd24_account_id=usd24.account_id,
    )


def test_token_backed_card_expense_replay_deduplicates_settlement():
    service, token, category, card, usd24 = _setup_safepal_payment_profile(idempotency_prefix="replay")

    _, replay = service.record_payment_profile_expense(
        token,
        {
            "payment": "safepal",
            "amount": "3.40",
            "currency": "USD",
            "category_id": category.category_id,
            "purpose": "meituan",
        },
        idempotency_key="safepal-expense-replay",
    )
    _, replay_again = service.record_payment_profile_expense(
        token,
        {
            "payment": "safepal",
            "amount": "3.40",
            "currency": "USD",
            "category_id": category.category_id,
            "purpose": "meituan",
        },
        idempotency_key="safepal-expense-replay",
    )

    assert replay is False
    assert replay_again is True
    assert service.account_balance(token, card.account_id)["official_balance"]["amount"] == "0.00"
    assert service.account_balance(token, usd24.account_id)["official_balance"]["amount"] == "274.04"


def test_token_backed_card_expense_requires_sufficient_usd24_backing():
    service, token, category, card, usd24 = _setup_safepal_payment_profile(
        opening_balance="1.00",
        idempotency_prefix="insufficient",
    )

    with pytest.raises(ValidationError, match="insufficient backing balance"):
        service.record_payment_profile_expense(
            token,
            {
                "payment": "safepal",
                "amount": "3.40",
                "currency": "USD",
                "category_id": category.category_id,
                "purpose": "meituan",
            },
            idempotency_key="insufficient-expense",
        )

    assert service.account_balance(token, card.account_id)["official_balance"]["amount"] == "0.00"
    assert service.account_balance(token, usd24.account_id)["official_balance"]["amount"] == "1.00"
