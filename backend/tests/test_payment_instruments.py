from __future__ import annotations

from decimal import Decimal

import pytest

from track_anywhere.errors import ValidationError
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_payment_instruments_attach_to_one_shared_credit_account(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'ledger.sqlite3'}")
    token = service.owner_token
    card, _ = service.create_account(
        token,
        {
            "name": "交通银行信用卡共享额度",
            "type": "liability",
            "currency": "CNY",
            "opening_balance": "200",
            "institution_type": "bank",
            "subtype": "credit_card",
            "institution": "交通银行",
        },
        idempotency_key="shared-credit-account",
    )

    physical, replay = service.create_payment_instrument(
        token,
        {
            "slug": "bocom-2862",
            "display_name": "交通银行实体卡(2862)",
            "kind": "credit_card",
            "account_id": card.account_id,
            "last4": "2862",
        },
        idempotency_key="instrument-2862",
    )
    virtual, _ = service.create_payment_instrument(
        token,
        {
            "slug": "bocom-2863",
            "display_name": "交通银行电子卡(2863)",
            "kind": "credit_card",
            "account_id": card.account_id,
            "last4": "2863",
        },
        idempotency_key="instrument-2863",
    )

    overview = service.get_credit_card(token, card.account_id)

    assert replay is False
    assert physical.account_id == card.account_id
    assert virtual.account_id == card.account_id
    assert overview["natural_balance"] == Decimal("200")
    assert overview["current_balance"] == Decimal("200")
    assert [item.slug for item in overview["instruments"]] == ["bocom-2862", "bocom-2863"]
    assert service.resolve_payment_instrument(token, "bocom-2863").account_id == card.account_id


def test_payment_instrument_requires_matching_credit_card_account(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'ledger.sqlite3'}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="instrument-cash",
    )

    with pytest.raises(ValidationError, match="credit card instrument requires a liability account with subtype credit_card"):
        service.create_payment_instrument(
            token,
            {
                "slug": "cash-card",
                "display_name": "Cash Card",
                "kind": "credit_card",
                "account_id": cash.account_id,
            },
            idempotency_key="instrument-cash-card",
        )


def test_payment_instruments_persist_across_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'ledger.sqlite3'}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = first.owner_token
    card, _ = first.create_account(
        token,
        {
            "name": "Persisted Shared Card",
            "type": "liability",
            "currency": "CNY",
            "institution_type": "bank",
            "subtype": "credit_card",
            "institution": "交通银行",
        },
        idempotency_key="persist-instrument-account",
    )
    instrument, _ = first.create_payment_instrument(
        token,
        {
            "slug": "bocom-2862",
            "display_name": "交通银行实体卡(2862)",
            "kind": "credit_card",
            "account_id": card.account_id,
            "last4": "2862",
        },
        idempotency_key="persist-instrument",
    )

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    assert second.resolve_payment_instrument(token, "bocom-2862") == instrument
    assert second.list_payment_instruments(token, account_id=card.account_id) == [instrument]
