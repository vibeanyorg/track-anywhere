from __future__ import annotations

from decimal import Decimal
from typing import Any

from .errors import ValidationError


SPENDING_LINE_TYPES = {"expense", "refund", "transfer_fee"}


class BookReportUseCases:
    def budget_execution_report(self, token: str, book_id: str, budget_id: str) -> dict[str, Any]:
        self.actor_for_book(token, book_id, "budget:read")
        budget = self.budgets.get_budget(budget_id)
        if budget.book_id != book_id:
            raise ValidationError("budget does not belong to book")
        target_reports = []
        total_spent = Decimal("0")
        transactions = self.storage.list_all_confirmed_transactions(book_id=book_id)
        for target in self.budgets.list_targets(budget_id):
            amount = self._budget_target_spend(book_id, target, transactions=transactions)
            if target.mode == "exclude":
                total_spent -= amount
            else:
                total_spent += amount
            target_reports.append(
                {
                    "budget_target_id": target.budget_target_id,
                    "target_type": target.target_type,
                    "target_id": target.target_id,
                    "mode": target.mode,
                    "amount": str(amount),
                }
            )
        return {
            "book_id": book_id,
            "budget_id": budget_id,
            "currency": budget.currency,
            "total_amount": str(budget.total_amount),
            "spent": str(total_spent),
            "remaining": str(budget.total_amount - total_spent),
            "targets": target_reports,
        }

    def spending_report(self, token: str, book_id: str, *, group_by: str = "category_parent", currency: str | None = None) -> dict[str, Any]:
        self.actor_for_book(token, book_id, "ledger:read")
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for transaction in self.storage.list_all_confirmed_transactions(book_id=book_id):
            if transaction.book_id != book_id or transaction.reversed_by is not None:
                continue
            for line in self._report_lines_for_transaction(transaction):
                if line.line_type not in SPENDING_LINE_TYPES:
                    continue
                if currency is not None and line.currency != currency:
                    continue
                key = self._spending_report_key(line, group_by)
                group = groups.setdefault((key, line.currency), {"key": key, "currency": line.currency, "amount": Decimal("0"), "line_count": 0})
                group["amount"] += line.amount
                group["line_count"] += 1
        return {"book_id": book_id, "group_by": group_by, "currency": currency, "groups": [{"key": item["key"], "currency": item["currency"], "amount": str(item["amount"]), "line_count": item["line_count"]} for item in sorted(groups.values(), key=lambda item: (item["currency"], item["key"]))]}

    def _spending_report_key(self, line, group_by: str) -> str:
        if group_by == "category":
            return line.category_id or "uncategorized"
        if group_by == "category_parent":
            snapshot = line.category_path_snapshot or {}
            return str(snapshot.get("primary") or "uncategorized")
        if group_by == "necessity":
            return line.necessity
        raise ValidationError("unsupported spending report grouping")

    def _budget_target_spend(self, book_id: str, target, *, transactions=None) -> Decimal:
        total = Decimal("0")
        for transaction in transactions if transactions is not None else self.storage.list_all_confirmed_transactions(book_id=book_id):
            if transaction.book_id != book_id or transaction.reversed_by is not None:
                continue
            for line in self._report_lines_for_transaction(transaction):
                if line.line_type not in SPENDING_LINE_TYPES:
                    continue
                if self._line_matches_budget_target(line, target):
                    total += line.amount
        return total

    def _line_matches_budget_target(self, line, target) -> bool:
        if target.target_type == "book":
            return True
        if target.target_type == "category_node":
            return line.category_id == target.target_id
        if target.target_type == "category_subtree":
            if line.category_id == target.target_id:
                return True
            category = self.categories.categories.get(line.category_id)
            return bool(category and category.parent_id == target.target_id)
        if target.target_type == "project":
            return line.project_id == target.target_id
        if target.target_type == "merchant":
            return line.merchant_id == target.target_id
        return False
