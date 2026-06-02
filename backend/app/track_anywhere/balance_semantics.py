from __future__ import annotations

from decimal import Decimal


ACCOUNT_TYPE_BALANCE_SEMANTICS = {
    "asset": "natural_asset_balance",
    "fund": "natural_fund_balance",
    "system": "natural_system_balance",
    "liability": "natural_liability_balance",
    "expense": "natural_expense_balance",
    "income": "natural_income_balance",
    "equity": "natural_equity_balance",
}

LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS = "natural_liability_balance_positive_owed"
LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS = "natural_liability_balance_negative_overpayment"
ACCOUNT_SUMMARY_AMOUNT_SEMANTICS = "natural_account_balance_sum_not_net_worth"
ACCOUNT_SUMMARY_NET_AMOUNT_SEMANTICS = (
    "asset_amount_plus_fund_amount_plus_system_amount_plus_liability_overpayment_minus_liability_outstanding"
)
CREDIT_CARD_DERIVED_AVAILABLE_CREDIT_SEMANTICS = "credit_limit_minus_outstanding_balance_plus_overpayment_balance"
CREDIT_CARD_CURRENT_BALANCE_COMPATIBILITY_ALIAS = (
    "natural liability balance compatibility alias; "
    "prefer natural_balance, natural_balance_semantics, outstanding_balance, and overpayment_balance"
)
CREDIT_CARD_COLLECTION_CURRENT_BALANCE_COMPATIBILITY_ALIAS = (
    "natural liability balance compatibility alias; "
    "prefer credit_cards.natural_balance, credit_cards.natural_balance_semantics, "
    "credit_cards.outstanding_balance, and credit_cards.overpayment_balance"
)


def balance_semantics_for_account_type(account_type: str) -> str:
    return ACCOUNT_TYPE_BALANCE_SEMANTICS.get(account_type, "natural_account_balance")


def balance_delta_semantics_for_account_type(account_type: str) -> str:
    return f"{balance_semantics_for_account_type(account_type)}_delta"


def account_summary_semantics_metadata() -> dict[str, str]:
    return {
        "amount": ACCOUNT_SUMMARY_AMOUNT_SEMANTICS,
        "asset_amount": ACCOUNT_TYPE_BALANCE_SEMANTICS["asset"],
        "fund_amount": ACCOUNT_TYPE_BALANCE_SEMANTICS["fund"],
        "system_amount": ACCOUNT_TYPE_BALANCE_SEMANTICS["system"],
        "liability_amount": ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
        "liability_outstanding_amount": LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
        "liability_overpayment_amount": LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
        "net_amount": ACCOUNT_SUMMARY_NET_AMOUNT_SEMANTICS,
    }


def account_summary_group_semantics_fields() -> dict[str, str]:
    return {
        "amount_semantics": ACCOUNT_SUMMARY_AMOUNT_SEMANTICS,
        "asset_amount_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["asset"],
        "fund_amount_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["fund"],
        "system_amount_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["system"],
        "liability_amount_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
        "liability_outstanding_amount_semantics": LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
        "liability_overpayment_amount_semantics": LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
        "net_amount_semantics": ACCOUNT_SUMMARY_NET_AMOUNT_SEMANTICS,
    }


def liability_split_amount_semantics() -> dict[str, str]:
    return {
        "outstanding_amount_semantics": LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
        "overpayment_amount_semantics": LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
    }


def liability_balance_amounts(amount: Decimal) -> dict[str, Decimal]:
    return {
        "outstanding_amount": max(amount, Decimal("0")),
        "overpayment_amount": max(-amount, Decimal("0")),
    }


def liability_balance_view(amount: Decimal) -> dict[str, str]:
    amounts = liability_balance_amounts(amount)
    return {
        "semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
        "outstanding_amount": str(amounts["outstanding_amount"]),
        "overpayment_amount": str(amounts["overpayment_amount"]),
        **liability_split_amount_semantics(),
    }
