from __future__ import annotations

from typing import Any

from . import commands
from .books import DEFAULT_BOOK_ID


class FundCatalogUseCases:
    def create_fund(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_for_book(token, DEFAULT_BOOK_ID, "budget:write")
        command = commands.CreateFundCommand.model_validate(payload)
        self.assets.ensure(command.currency)
        request_hash = self._hash_command(command)
        created_accounts = []

        def run():
            book_id = self.books.ensure_default().book_id
            account = self._new_account(
                command.name,
                "fund",
                command.currency,
                institution_type="system",
                subtype="fund",
                institution="track-anywhere",
                book_id=book_id,
            )
            created_accounts.append(account)
            fund = self.budgets.create(
                name=command.name,
                account_id=account.account_id,
                currency=command.currency,
                book_id=book_id,
            )
            self.audit.record(operation="fund.create", actor=actor, entity_ref=fund.fund_id, details=command.model_dump())
            return fund

        fund, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="fund.create",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_finance_change(funds=(fund,), accounts=created_accounts))
        return fund, replay
