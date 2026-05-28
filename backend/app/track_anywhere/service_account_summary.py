from __future__ import annotations

from decimal import Decimal
from typing import Any

from .books import DEFAULT_BOOK_ID
from .errors import ValidationError


class AccountSummaryUseCases:
    def account_summary(
        self,
        token: str,
        *,
        group_by: str = "subtype",
        currency: str | None = None,
        institution_type: str | None = None,
        include_system: bool = False,
    ) -> dict[str, Any]:
        self.actor_for_book(token, DEFAULT_BOOK_ID, "account:read")
        allowed_groupings = {"type", "institution_type", "subtype", "institution", "currency"}
        if group_by not in allowed_groupings:
            raise ValidationError(f"group_by must be one of {sorted(allowed_groupings)}")

        accounts = self._list_accounts_from_storage(
            book_id=DEFAULT_BOOK_ID,
            currency=currency,
            institution_type=institution_type,
        )
        accounts = [account for account in accounts if include_system or account.type in {"asset", "liability", "fund"}]
        balances = self._account_balances_from_storage(account.account_id for account in accounts)

        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for account in accounts:
            if account.book_id != DEFAULT_BOOK_ID:
                continue

            account_currency = account.currency
            amount = balances.get((account.account_id, account_currency), Decimal("0"))
            key_value = getattr(account, group_by)
            key = str(key_value) if key_value else "unclassified"
            group = groups.setdefault(
                (key, account_currency),
                {
                    "key": key,
                    "currency": account_currency,
                    "amount": Decimal("0"),
                    "asset_amount": Decimal("0"),
                    "liability_amount": Decimal("0"),
                    "account_count": 0,
                    "account_ids": [],
                    "types": set(),
                },
            )
            group["amount"] += amount
            if account.type == "liability":
                group["liability_amount"] += amount
            else:
                group["asset_amount"] += amount
            group["account_count"] += 1
            group["account_ids"].append(account.account_id)
            group["types"].add(account.type)

        return {
            "group_by": group_by,
            "currency": currency,
            "institution_type": institution_type,
            "include_system": include_system,
            "groups": [
                {
                    "key": group["key"],
                    "currency": group["currency"],
                    "amount": str(group["amount"]),
                    "asset_amount": str(group["asset_amount"]),
                    "liability_amount": str(group["liability_amount"]),
                    "net_amount": str(group["asset_amount"] - group["liability_amount"]),
                    "account_count": group["account_count"],
                    "account_ids": sorted(group["account_ids"]),
                    "types": sorted(group["types"]),
                }
                for group in sorted(groups.values(), key=lambda item: (item["currency"], item["key"]))
            ],
        }
