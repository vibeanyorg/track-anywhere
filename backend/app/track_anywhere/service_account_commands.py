from __future__ import annotations

from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import CreateAccountCommand, UpdateAccountMetadataCommand
from .errors import ValidationError
from .ledger import Account, Posting
from .transaction_builder import build_transaction


class AccountCommandUseCases:
    def create_account(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Account, bool]:
        command = CreateAccountCommand.model_validate(payload)
        book_id = command.book_id or DEFAULT_BOOK_ID
        actor = self.actor_for_book(token, book_id, "account:write")
        self.assets.ensure(command.currency)
        self.assets.validate_amount(command.currency, command.opening_balance, field_name="opening balance")
        request_hash = self._hash_command(command)
        created_transactions = []
        created_accounts = []

        def run():
            account = self._new_account(
                command.name,
                command.type,
                command.currency,
                institution_type=command.institution_type,
                subtype=command.subtype,
                institution=command.institution,
                book_id=book_id,
            )
            created_accounts.append(account)
            if command.opening_balance:
                equity = self._new_account(
                    f"Opening equity for {command.name}",
                    "equity",
                    command.currency,
                    institution_type="system",
                    subtype="opening_equity",
                    institution="track-anywhere",
                    book_id=book_id,
                )
                created_accounts.append(equity)
                transaction = build_transaction(
                    memo=f"Opening balance: {command.name}",
                    purpose="opening_balance",
                    postings=[
                        Posting(account.account_id, command.opening_balance, command.currency),
                        Posting(equity.account_id, -command.opening_balance, command.currency),
                    ],
                    accounts=[account, equity],
                    book_id=book_id,
                    scale_lookup=self.assets.scale_for,
                )
                created_transactions.append(transaction)
            self.audit.record(
                operation="account.create",
                actor=actor,
                entity_ref=account.account_id,
                details=command.model_dump(mode="json"),
            )
            return account

        account, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="account.create",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_ledger_change(*created_transactions, accounts=created_accounts))
        return account, replay

    def update_account_metadata(self, token: str, account_id: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Account, bool]:
        command = UpdateAccountMetadataCommand.model_validate(payload)
        if command.institution_type is None and command.subtype is None and command.institution is None:
            raise ValidationError("at least one account metadata field is required")
        account = self.storage.get_account(account_id)
        actor = self.actor_for_book(token, account.book_id, "account:write")
        request_hash = self._hash_command_payload(command, {"account_id": account_id})

        def run():
            if command.institution_type is not None:
                account.institution_type = command.institution_type
            if command.subtype is not None:
                account.subtype = command.subtype
            if command.institution is not None:
                account.institution = command.institution
            account.version += 1
            self.audit.record(
                operation="account.metadata.update",
                actor=actor,
                entity_ref=account.account_id,
                details={"account_id": account_id, **command.model_dump(mode="json", exclude_none=True)},
            )
            return account

        account, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="account.metadata.update",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_ledger_change(accounts=(account,)))
        return account, replay
