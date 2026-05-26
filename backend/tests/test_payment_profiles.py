from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest

from track_anywhere.errors import ValidationError
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
