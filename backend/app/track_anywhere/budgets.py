from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

from .errors import NotFound, StaleVersion, ValidationError


@dataclass
class BudgetFund:
    fund_id: str
    account_id: str
    name: str
    currency: str
    allocated: Decimal = Decimal("0")
    spent: Decimal = Decimal("0")
    version: int = 1
    flow: list[dict[str, str]] = field(default_factory=list)

    @property
    def remaining(self) -> Decimal:
        return self.allocated - self.spent


class BudgetBook:
    def __init__(self) -> None:
        self.funds: dict[str, BudgetFund] = {}

    def create(self, *, name: str, account_id: str, currency: str) -> BudgetFund:
        fund = BudgetFund(fund_id=f"fund_{uuid4().hex}", account_id=account_id, name=name, currency=currency)
        self.funds[fund.fund_id] = fund
        return fund

    def get(self, fund_id: str) -> BudgetFund | None:
        return self.funds.get(fund_id)

    def require_current(self, fund_id: str, expected_version: int) -> BudgetFund:
        fund = self.get(fund_id)
        if fund is None:
            raise NotFound(f"fund not found: {fund_id}")
        if fund.version != expected_version:
            raise StaleVersion("fund version conflict")
        return fund

    def allocate(self, fund_id: str, expected_version: int, amount: Decimal, transaction_id: str) -> BudgetFund:
        fund = self.require_current(fund_id, expected_version)
        fund.allocated += amount
        fund.version += 1
        fund.flow.append({"kind": "allocation", "amount": str(amount), "transaction_id": transaction_id})
        return fund

    def spend(self, fund_id: str, expected_version: int, amount: Decimal, transaction_id: str) -> BudgetFund:
        fund = self.require_current(fund_id, expected_version)
        if amount > fund.remaining:
            raise ValidationError("fund spend exceeds remaining amount")
        fund.spent += amount
        fund.version += 1
        fund.flow.append({"kind": "spend", "amount": str(amount), "transaction_id": transaction_id})
        return fund
