from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from track_anywhere.auth_identities import OAuthIdentity
from track_anywhere.errors import ValidationError
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_reversal_restores_balance_and_records_reversal_link():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="reversal-cash",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Food"},
        idempotency_key="reversal-food-category",
    )
    transaction, _ = service.record_expense(
        token,
        {
            "amount": "38",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": category.category_id,
            "purpose": "lunch",
        },
        idempotency_key="reversal-expense",
    )

    assert service.account_balance(token, cash.account_id)["official_balance"]["amount"] == "62"

    reversal, _ = service.reverse_transaction(
        token,
        {"transaction_id": transaction.transaction_id, "memo": "duplicate entry"},
        idempotency_key="reversal-reverse",
    )

    assert service.account_balance(token, cash.account_id)["official_balance"]["amount"] == "100"
    assert service.get_transaction(token, transaction.transaction_id).reversed_by == reversal.transaction_id
    assert reversal.reverses_transaction_id == transaction.transaction_id
    assert all(posting.amount_semantics == "debit_credit" for posting in reversal.postings)
    assert all(posting.amount > Decimal("0") for posting in reversal.postings)
    assert {posting.side for posting in reversal.postings} == {"debit", "credit"}

    with pytest.raises(ValidationError):
        service.reverse_transaction(
            token,
            {"transaction_id": reversal.transaction_id, "memo": "bad reverse"},
            idempotency_key="reversal-reverse-reversal",
        )


def test_investment_event_keeps_non_default_book_across_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = first.owner_token
    book, _ = first.create_book(
        token,
        {"name": "Family", "kind": "family", "base_currency": "CNY"},
        idempotency_key="investment-book",
    )
    account, _ = first.create_book_account(
        token,
        book.book_id,
        {
            "name": "Family Wealth",
            "type": "asset",
            "currency": "CNY",
            "opening_balance": "100.00",
            "institution_type": "bank",
            "subtype": "wealth_management",
        },
        idempotency_key="investment-book-account",
    )
    event, _ = first.record_investment_event(
        token,
        {
            "account_id": account.account_id,
            "event_type": "buy",
            "amount": "100.00",
            "currency": "CNY",
            "occurred_at": "2026-05-01T00:00:00+08:00",
        },
        idempotency_key="investment-book-event",
    )

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    events = second.list_investment_events(token, account.account_id)

    assert event.book_id == book.book_id
    assert [(item.event_id, item.book_id) for item in events] == [(event.event_id, book.book_id)]

    with sqlite3.connect(tmp_path / "track-anywhere.sqlite3") as connection:
        stored = connection.execute(
            "select book_id from investment_events where event_id = ?",
            (event.event_id,),
        ).fetchone()
    assert stored == (book.book_id,)


def test_asset_scale_validation_for_fiat_crypto_and_stablecoins():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token

    with pytest.raises(ValidationError):
        service.create_account(
            token,
            {"name": "Bad CNY", "type": "asset", "currency": "CNY", "opening_balance": "1.234"},
            idempotency_key="bad-cny-scale",
        )
    with pytest.raises(ValidationError):
        service.create_account(
            token,
            {"name": "Bad JPY", "type": "asset", "currency": "JPY", "opening_balance": "1.5"},
            idempotency_key="bad-jpy-scale",
        )
    with pytest.raises(ValidationError):
        service.create_account(
            token,
            {"name": "Bad BTC", "type": "asset", "currency": "BTC", "opening_balance": "0.000000001"},
            idempotency_key="bad-btc-scale",
        )

    account, _ = service.create_account(
        token,
        {"name": "USDC", "type": "asset", "currency": "USDC", "opening_balance": "9.126095"},
        idempotency_key="good-usdc-scale",
    )

    assert service.account_balance(token, account.account_id)["official_balance"]["amount"] == "9.126095"


def test_asset_catalog_does_not_expose_private_custom_assets_to_other_book_members():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token
    private_book, _ = service.create_book(
        token,
        {"name": "Private Book", "kind": "custom", "base_currency": "CNY"},
        idempotency_key="private-asset-book",
    )
    service.create_book_account(
        token,
        private_book.book_id,
        {"name": "Private Asset", "type": "asset", "currency": "PRIVATEASSET"},
        idempotency_key="private-asset-account",
    )
    viewer_login = service.login_oauth_identity(
        OAuthIdentity(
            provider="test",
            subject="viewer",
            email="viewer@example.test",
            email_verified=True,
            name="Viewer",
            picture=None,
        ),
        role="viewer",
    )

    owner_assets = {asset.asset_code for asset in service.list_assets(token, status=None)}
    viewer_assets = {asset.asset_code for asset in service.list_assets(viewer_login["credential_token"], status=None)}

    assert "PRIVATEASSET" in owner_assets
    assert "PRIVATEASSET" not in viewer_assets
    assert "CNY" in viewer_assets


def test_fx_exchange_uses_clearing_accounts_and_keeps_plain_transfers_single_asset():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token
    cny, _ = service.create_account(
        token,
        {"name": "CNY Bank", "type": "asset", "currency": "CNY", "opening_balance": "700.00"},
        idempotency_key="fx-cny-bank",
    )
    usd, _ = service.create_account(
        token,
        {"name": "USD Wallet", "type": "asset", "currency": "USD"},
        idempotency_key="fx-usd-wallet",
    )

    with pytest.raises(ValidationError):
        service.record_transaction(
            token,
            {
                "amount": "700.00",
                "currency": "CNY",
                "from_account_id": cny.account_id,
                "to_account_id": usd.account_id,
                "purpose": "bad cross-currency transfer",
            },
            idempotency_key="fx-bad-plain-transfer",
        )

    transaction, _ = service.record_fx_exchange(
        token,
        {
            "from_account_id": cny.account_id,
            "from_amount": "700.00",
            "from_currency": "CNY",
            "to_account_id": usd.account_id,
            "to_amount": "100.00",
            "to_currency": "USD",
            "memo": "manual exchange",
        },
        idempotency_key="fx-exchange",
    )

    assert len(transaction.postings) == 4
    assert [line.line_type for line in transaction.lines] == ["fx_exchange"]
    assert service.account_balance(token, cny.account_id)["official_balance"]["amount"] == "0.00"
    assert service.account_balance(token, usd.account_id)["official_balance"]["amount"] == "100.00"
    assert service.spending_report(token, "book_default")["groups"] == []


def test_fx_exchange_to_credit_card_liability_reduces_outstanding_balance():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token
    cny, _ = service.create_account(
        token,
        {"name": "Wechat Lingqian Tong", "type": "asset", "currency": "CNY", "opening_balance": "20.00"},
        idempotency_key="fx-repay-cny-source",
    )
    card, _ = service.create_account(
        token,
        {
            "name": "Guangfa Visa",
            "type": "liability",
            "currency": "USD",
            "opening_balance": "12.80",
            "subtype": "credit_card",
        },
        idempotency_key="fx-repay-usd-card",
    )
    fee, _ = service.create_account(
        token,
        {
            "name": "Fees",
            "type": "expense",
            "currency": "CNY",
            "institution_type": "other",
            "subtype": "fee",
        },
        idempotency_key="fx-repay-fee",
    )

    transaction, _ = service.record_fx_exchange(
        token,
        {
            "from_account_id": cny.account_id,
            "from_amount": "11.66",
            "from_currency": "CNY",
            "to_account_id": card.account_id,
            "to_amount": "1.72",
            "to_currency": "USD",
            "fee_account_id": fee.account_id,
            "fee_amount": "0.10",
            "memo": "credit card fx repayment",
        },
        idempotency_key="fx-repay-credit-card",
    )

    assert [line.line_type for line in transaction.lines] == ["fx_exchange", "fx_fee"]
    assert service.account_balance(token, cny.account_id)["official_balance"]["amount"] == "8.24"
    assert service.account_balance(token, card.account_id)["official_balance"]["amount"] == "11.08"
    assert service.account_balance(token, fee.account_id)["official_balance"]["amount"] == "0.10"


def test_investment_event_can_link_to_ledger_transaction_and_valuation_persists(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = first.owner_token
    cash, _ = first.create_account(
        token,
        {"name": "Cash", "type": "asset", "currency": "CNY", "opening_balance": "5000.00"},
        idempotency_key="linked-investment-cash",
    )
    wealth, _ = first.create_account(
        token,
        {"name": "Wealth", "type": "asset", "currency": "CNY"},
        idempotency_key="linked-investment-wealth",
    )

    event, _ = first.record_investment_event(
        token,
        {
            "account_id": wealth.account_id,
            "cash_account_id": cash.account_id,
            "event_type": "buy",
            "amount": "5000.00",
            "currency": "CNY",
            "occurred_at": "2026-05-01T00:00:00+08:00",
            "memo": "buy wealth product",
        },
        idempotency_key="linked-investment-event",
    )
    valuation, _ = first.record_investment_valuation(
        token,
        {
            "account_id": wealth.account_id,
            "value": "5051.25",
            "currency": "CNY",
            "observed_at": "2026-05-21T00:00:00+08:00",
            "source": "manual",
        },
        idempotency_key="linked-investment-valuation",
    )

    assert event.transaction_id is not None
    assert first.get_transaction(token, event.transaction_id).book_id == "book_default"
    assert first.account_balance(token, cash.account_id)["official_balance"]["amount"] == "0.00"
    assert first.account_balance(token, wealth.account_id)["official_balance"]["amount"] == "5000.00"

    performance = first.investment_performance(token, wealth.account_id, as_of="2026-05-21T00:00:00+08:00")
    assert performance["current_value"] == "5051.25"
    assert performance["current_value_source"] == "valuation_snapshot"
    assert performance["valuation_id"] == valuation.valuation_id
    assert performance["total_return"] == "51.25"

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    [persisted_event] = second.list_investment_events(token, wealth.account_id)
    [persisted_valuation] = second.list_investment_valuations(token, wealth.account_id)
    assert persisted_event.transaction_id == event.transaction_id
    assert persisted_valuation.valuation_id == valuation.valuation_id
