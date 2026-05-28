from __future__ import annotations

from typing import Any

from . import commands
from .errors import NotFound, ValidationError
from .ledger import Posting
from .transaction_builder import build_transaction


class FundFlowUseCases:
    def allocate_fund(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = commands.FundAllocationCommand.model_validate(payload)
        fund = self.budgets.get(command.fund_id)
        if fund is None:
            raise NotFound(f"fund not found: {command.fund_id}")
        actor = self.actor_for_book(token, fund.book_id, "budget:write")
        source = self._get_account_from_storage(command.source_account_id)
        if source.book_id != fund.book_id:
            raise ValidationError("fund allocation account must belong to the fund book")
        if source.currency != command.currency or fund.currency != command.currency:
            raise ValidationError("fund allocation currency must match source and fund currencies")
        self.assets.validate_amount(command.currency, command.amount)
        request_hash = self._hash_command(command)

        def run():
            current_fund = self.budgets.require_current(command.fund_id, command.expected_version)
            fund_account = self._get_account_from_storage(current_fund.account_id)
            transaction = build_transaction(
                memo=command.memo,
                purpose="fund_allocation",
                postings=[
                    Posting(command.source_account_id, -command.amount, command.currency),
                    Posting(current_fund.account_id, command.amount, command.currency),
                ],
                accounts=[source, fund_account],
                book_id=fund.book_id,
                scale_lookup=self.assets.scale_for,
            )
            updated = self.budgets.allocate(
                command.fund_id,
                command.expected_version,
                command.amount,
                transaction.transaction_id,
            )
            self.audit.record(
                operation="fund.allocate",
                actor=actor,
                entity_ref=updated.fund_id,
                details={"transaction_id": transaction.transaction_id, "amount": str(command.amount)},
            )
            return {"fund": updated, "transaction": transaction}

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="fund.allocate",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(
            replay,
            lambda: self._commit_finance_change(
                funds=(result["fund"],),
                transactions=(result["transaction"],),
            ),
        )
        return result, replay

    def spend_fund(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = commands.FundSpendCommand.model_validate(payload)
        fund = self.budgets.get(command.fund_id)
        if fund is None:
            raise NotFound(f"fund not found: {command.fund_id}")
        actor = self.actor_for_book(token, fund.book_id, "budget:write")
        expense = self._get_account_from_storage(command.expense_account_id)
        if expense.book_id != fund.book_id:
            raise ValidationError("fund spend account must belong to the fund book")
        if expense.currency != command.currency or fund.currency != command.currency:
            raise ValidationError("fund spend currency must match expense and fund currencies")
        self.assets.validate_amount(command.currency, command.amount)
        request_hash = self._hash_command(command)

        def run():
            current_fund = self.budgets.require_current(command.fund_id, command.expected_version)
            fund_account = self._get_account_from_storage(current_fund.account_id)
            transaction = build_transaction(
                memo=command.memo,
                purpose="fund_spend",
                postings=[
                    Posting(current_fund.account_id, -command.amount, command.currency),
                    Posting(command.expense_account_id, command.amount, command.currency),
                ],
                accounts=[fund_account, expense],
                book_id=fund.book_id,
                scale_lookup=self.assets.scale_for,
            )
            updated = self.budgets.spend(
                command.fund_id,
                command.expected_version,
                command.amount,
                transaction.transaction_id,
            )
            self.audit.record(
                operation="fund.spend",
                actor=actor,
                entity_ref=updated.fund_id,
                details={"transaction_id": transaction.transaction_id, "amount": str(command.amount)},
            )
            return {"fund": updated, "transaction": transaction}

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="fund.spend",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(
            replay,
            lambda: self._commit_finance_change(
                funds=(result["fund"],),
                transactions=(result["transaction"],),
            ),
        )
        return result, replay
