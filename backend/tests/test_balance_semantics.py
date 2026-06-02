from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from track_anywhere.balance_semantics import (
    ACCOUNT_SUMMARY_AMOUNT_SEMANTICS,
    ACCOUNT_SUMMARY_NET_AMOUNT_SEMANTICS,
    ACCOUNT_TYPE_BALANCE_SEMANTICS,
    CREDIT_CARD_COLLECTION_CURRENT_BALANCE_COMPATIBILITY_ALIAS,
    CREDIT_CARD_CURRENT_BALANCE_COMPATIBILITY_ALIAS,
    CREDIT_CARD_DERIVED_AVAILABLE_CREDIT_SEMANTICS,
    LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
    LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
    account_summary_group_semantics_fields,
    account_summary_semantics_metadata,
    balance_delta_semantics_for_account_type,
    balance_semantics_for_account_type,
    liability_balance_amounts,
    liability_balance_view,
    liability_split_amount_semantics,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_account_type_balance_semantics_are_explicit_for_supported_account_types():
    assert ACCOUNT_TYPE_BALANCE_SEMANTICS == {
        "asset": "natural_asset_balance",
        "fund": "natural_fund_balance",
        "system": "natural_system_balance",
        "liability": "natural_liability_balance",
        "expense": "natural_expense_balance",
        "income": "natural_income_balance",
        "equity": "natural_equity_balance",
    }

    for account_type, semantics in ACCOUNT_TYPE_BALANCE_SEMANTICS.items():
        assert balance_semantics_for_account_type(account_type) == semantics
        assert balance_delta_semantics_for_account_type(account_type) == f"{semantics}_delta"


def test_unknown_account_type_balance_semantics_stays_generic_not_signed():
    assert balance_semantics_for_account_type("unknown") == "natural_account_balance"


def test_liability_and_summary_amount_semantics_are_explicit_contracts():
    assert liability_split_amount_semantics() == {
        "outstanding_amount_semantics": LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
        "overpayment_amount_semantics": LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
    }
    assert LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS == "natural_liability_balance_positive_owed"
    assert LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS == "natural_liability_balance_negative_overpayment"
    assert ACCOUNT_SUMMARY_AMOUNT_SEMANTICS == "natural_account_balance_sum_not_net_worth"
    assert (
        ACCOUNT_SUMMARY_NET_AMOUNT_SEMANTICS
        == "asset_amount_plus_fund_amount_plus_system_amount_plus_liability_overpayment_minus_liability_outstanding"
    )
    assert account_summary_semantics_metadata() == {
        "amount": ACCOUNT_SUMMARY_AMOUNT_SEMANTICS,
        "asset_amount": ACCOUNT_TYPE_BALANCE_SEMANTICS["asset"],
        "fund_amount": ACCOUNT_TYPE_BALANCE_SEMANTICS["fund"],
        "system_amount": ACCOUNT_TYPE_BALANCE_SEMANTICS["system"],
        "liability_amount": ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
        "liability_outstanding_amount": LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
        "liability_overpayment_amount": LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
        "net_amount": ACCOUNT_SUMMARY_NET_AMOUNT_SEMANTICS,
    }
    assert account_summary_group_semantics_fields() == {
        "amount_semantics": ACCOUNT_SUMMARY_AMOUNT_SEMANTICS,
        "asset_amount_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["asset"],
        "fund_amount_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["fund"],
        "system_amount_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["system"],
        "liability_amount_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
        "liability_outstanding_amount_semantics": LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
        "liability_overpayment_amount_semantics": LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
        "net_amount_semantics": ACCOUNT_SUMMARY_NET_AMOUNT_SEMANTICS,
    }
    assert (
        CREDIT_CARD_DERIVED_AVAILABLE_CREDIT_SEMANTICS
        == "credit_limit_minus_outstanding_balance_plus_overpayment_balance"
    )
    assert CREDIT_CARD_CURRENT_BALANCE_COMPATIBILITY_ALIAS.startswith(
        "natural liability balance compatibility alias"
    )
    assert CREDIT_CARD_COLLECTION_CURRENT_BALANCE_COMPATIBILITY_ALIAS.startswith(
        "natural liability balance compatibility alias"
    )


def test_liability_balance_view_exposes_outstanding_and_overpayment_semantics():
    assert liability_balance_amounts(Decimal("9.36")) == {
        "outstanding_amount": Decimal("9.36"),
        "overpayment_amount": Decimal("0"),
    }
    assert liability_balance_amounts(Decimal("-1.72")) == {
        "outstanding_amount": Decimal("0"),
        "overpayment_amount": Decimal("1.72"),
    }
    assert liability_balance_view(Decimal("9.36")) == {
        "semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
        "outstanding_amount": "9.36",
        "outstanding_amount_semantics": LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
        "overpayment_amount": "0",
        "overpayment_amount_semantics": LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
    }
    assert liability_balance_view(Decimal("-1.72")) == {
        "semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
        "outstanding_amount": "0",
        "outstanding_amount_semantics": LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
        "overpayment_amount": "1.72",
        "overpayment_amount_semantics": LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
    }


def test_api_routers_import_balance_semantics_from_domain_contract_not_query_service():
    for path in [
        BACKEND / "api_routers/catalog.py",
        BACKEND / "api_routers/books.py",
        BACKEND / "api_routers/backoffice.py",
    ]:
        source = path.read_text()
        assert "from ..balance_semantics import balance_semantics_for_account_type" in source
        assert "service_balance_queries import balance_semantics_for_account_type" not in source
