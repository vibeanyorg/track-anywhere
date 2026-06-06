from __future__ import annotations

from decimal import Decimal
from typing import Any

from .balance_semantics import balance_semantics_for_account_type, liability_balance_view
from .ledger import Account


def official_account_balance_view(
    account: Account,
    official_amount: Decimal,
    *,
    confirmed_transaction_count: int,
    draft_count: int,
) -> dict[str, Any]:
    balance_semantics = balance_semantics_for_account_type(account.type)
    result: dict[str, Any] = {
        "account_id": account.account_id,
        "account_type": account.type,
        "currency": account.currency,
        "balance_semantics": balance_semantics,
        "official_balance": {
            "amount": str(official_amount),
            "amount_semantics": balance_semantics,
            "source": "confirmed_postings",
            "as_of_ledger_version": confirmed_transaction_count,
        },
        "default_view": "official",
        "provenance": {
            "confirmed_transaction_count": confirmed_transaction_count,
            "draft_count": draft_count,
        },
    }
    if account.type == "liability":
        result["liability_balance"] = liability_balance_view(official_amount)
    return result
