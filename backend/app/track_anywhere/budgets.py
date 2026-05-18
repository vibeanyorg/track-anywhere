from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import uuid4

from .books import DEFAULT_BOOK_ID
from .errors import NotFound, StaleVersion, ValidationError


@dataclass
class BudgetFund:
    fund_id: str
    account_id: str
    name: str
    currency: str
    book_id: str = DEFAULT_BOOK_ID
    allocated: Decimal = Decimal("0")
    spent: Decimal = Decimal("0")
    version: int = 1
    flow: list[dict[str, str]] = field(default_factory=list)

    @property
    def remaining(self) -> Decimal:
        return self.allocated - self.spent


@dataclass
class Budget:
    budget_id: str
    book_id: str
    name: str
    period: str
    currency: str
    total_amount: Decimal
    starts_on: date | None = None
    ends_on: date | None = None
    rollover_policy: str = "none"
    alert_thresholds: list[str] = field(default_factory=list)
    status: str = "active"
    version: int = 1


@dataclass
class BudgetTarget:
    budget_target_id: str
    budget_id: str
    target_type: str
    target_id: str | None
    mode: str
    amount: Decimal | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    version: int = 1


class BudgetBook:
    def __init__(self) -> None:
        self.funds: dict[str, BudgetFund] = {}
        self.budgets: dict[str, Budget] = {}
        self.targets: dict[str, BudgetTarget] = {}

    def create(self, *, name: str, account_id: str, currency: str, book_id: str = DEFAULT_BOOK_ID) -> BudgetFund:
        fund = BudgetFund(fund_id=f"fund_{uuid4().hex}", account_id=account_id, name=name, currency=currency, book_id=book_id)
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

    def create_budget(
        self,
        *,
        name: str,
        period: str,
        currency: str,
        total_amount: Decimal,
        book_id: str = DEFAULT_BOOK_ID,
        starts_on: date | None = None,
        ends_on: date | None = None,
        rollover_policy: str = "none",
    ) -> Budget:
        if period not in {"monthly", "weekly", "yearly", "custom"}:
            raise ValidationError("budget period is invalid")
        if rollover_policy not in {"none", "carry_remaining", "carry_overspend"}:
            raise ValidationError("budget rollover policy is invalid")
        budget = Budget(
            budget_id=f"budget_{uuid4().hex}",
            book_id=book_id,
            name=name,
            period=period,
            starts_on=starts_on,
            ends_on=ends_on,
            currency=currency,
            total_amount=total_amount,
            rollover_policy=rollover_policy,
        )
        self.budgets[budget.budget_id] = budget
        return budget

    def get_budget(self, budget_id: str) -> Budget:
        try:
            return self.budgets[budget_id]
        except KeyError as exc:
            raise NotFound(f"budget not found: {budget_id}") from exc

    def list_budgets(self, *, book_id: str = DEFAULT_BOOK_ID, status: str | None = "active") -> list[Budget]:
        budgets = [budget for budget in self.budgets.values() if budget.book_id == book_id]
        if status is not None:
            budgets = [budget for budget in budgets if budget.status == status]
        return sorted(budgets, key=lambda budget: (budget.name, budget.budget_id))

    def add_target(
        self,
        *,
        budget_id: str,
        target_type: str,
        target_id: str | None,
        mode: str = "include",
        amount: Decimal | None = None,
        metadata: dict[str, str] | None = None,
    ) -> BudgetTarget:
        self.get_budget(budget_id)
        if target_type not in {"book", "category_node", "category_subtree", "project", "merchant"}:
            raise ValidationError("budget target type is invalid")
        if mode not in {"include", "exclude"}:
            raise ValidationError("budget target mode is invalid")
        target = BudgetTarget(
            budget_target_id=f"btgt_{uuid4().hex}",
            budget_id=budget_id,
            target_type=target_type,
            target_id=target_id,
            mode=mode,
            amount=amount,
            metadata=metadata or {},
        )
        self.targets[target.budget_target_id] = target
        return target

    def list_targets(self, budget_id: str) -> list[BudgetTarget]:
        self.get_budget(budget_id)
        return sorted(
            [target for target in self.targets.values() if target.budget_id == budget_id],
            key=lambda target: (target.target_type, target.target_id or "", target.budget_target_id),
        )
