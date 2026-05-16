from __future__ import annotations

from decimal import Decimal

import pytest

from track_anywhere.errors import ValidationError
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_credit_card_profile_update_and_overview(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    card, _ = service.create_account(
        service.owner_token,
        {
            "name": "Test Visa",
            "type": "liability",
            "currency": "CNY",
            "opening_balance": "250",
            "institution_type": "bank",
            "subtype": "credit_card",
            "institution": "Test Bank",
        },
        idempotency_key="credit-card-account",
    )

    overview, replay = service.update_credit_card_profile(
        service.owner_token,
        card.account_id,
        {
            "credit_limit": "10000",
            "available_credit": "9750",
            "statement_day": 10,
            "due_day": 28,
            "annual_fee": "0",
        },
        idempotency_key="credit-card-profile",
    )

    assert replay is False
    assert overview["profile"].credit_limit == Decimal("10000")
    assert overview["current_balance"] == Decimal("250")
    assert overview["derived_available_credit"] == Decimal("9750")
    assert overview["utilization_rate"] == Decimal("0.025")
    assert overview["profile"].statement_day == 10
    assert service.list_credit_cards(service.owner_token)[0]["profile"].account_id == card.account_id


def test_credit_card_profile_requires_credit_card_account(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    cash, _ = service.create_account(
        service.owner_token,
        {"name": "Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="not-card",
    )

    with pytest.raises(ValidationError):
        service.update_credit_card_profile(
            service.owner_token,
            cash.account_id,
            {"credit_limit": "1000"},
            idempotency_key="not-card-profile",
        )


def test_credit_card_profile_persists(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = first.owner_token
    card, _ = first.create_account(
        token,
        {
            "name": "Persisted Card",
            "type": "liability",
            "currency": "USD",
            "institution_type": "bank",
            "subtype": "credit_card",
            "institution": "Persist Bank",
        },
        idempotency_key="persist-card",
    )
    first.update_credit_card_profile(
        token,
        card.account_id,
        {"credit_limit": "5000", "statement_day": 5, "due_day": 25},
        idempotency_key="persist-card-profile",
    )

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    overview = second.get_credit_card(token, card.account_id)

    assert overview["profile"].credit_limit == Decimal("5000")
    assert overview["profile"].statement_day == 5
    assert overview["profile"].due_day == 25
