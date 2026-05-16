from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .errors import NotFound


@dataclass
class CreditCardProfile:
    account_id: str
    credit_limit: Decimal | None = None
    available_credit: Decimal | None = None
    statement_day: int | None = None
    due_day: int | None = None
    annual_fee: Decimal | None = None
    version: int = 1


class CreditCardBook:
    def __init__(self) -> None:
        self.profiles: dict[str, CreditCardProfile] = {}

    def get(self, account_id: str) -> CreditCardProfile:
        try:
            return self.profiles[account_id]
        except KeyError as exc:
            raise NotFound(f"credit card profile not found: {account_id}") from exc

    def get_optional(self, account_id: str) -> CreditCardProfile | None:
        return self.profiles.get(account_id)

    def upsert(
        self,
        account_id: str,
        *,
        credit_limit: Decimal | None = None,
        available_credit: Decimal | None = None,
        statement_day: int | None = None,
        due_day: int | None = None,
        annual_fee: Decimal | None = None,
        update_fields: set[str],
    ) -> CreditCardProfile:
        profile = self.profiles.get(account_id)
        if profile is None:
            profile = CreditCardProfile(account_id=account_id)
            self.profiles[account_id] = profile
        else:
            profile.version += 1

        if "credit_limit" in update_fields:
            profile.credit_limit = credit_limit
        if "available_credit" in update_fields:
            profile.available_credit = available_credit
        if "statement_day" in update_fields:
            profile.statement_day = statement_day
        if "due_day" in update_fields:
            profile.due_day = due_day
        if "annual_fee" in update_fields:
            profile.annual_fee = annual_fee
        return profile
