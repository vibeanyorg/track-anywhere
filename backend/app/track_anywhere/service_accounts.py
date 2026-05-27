from __future__ import annotations

from decimal import Decimal
from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import CreateAccountCommand, UpdateAccountMetadataCommand
from .errors import ValidationError
from .ledger import Account, Posting
from .transaction_builder import build_transaction


class AccountUseCases:
    def create_account(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Account, bool]:
        command = CreateAccountCommand.model_validate(payload)
        book_id = command.book_id or DEFAULT_BOOK_ID
        actor = self.actor_for_book(token, book_id, "account:write")
        self.assets.ensure(command.currency)
        self.assets.validate_amount(command.currency, command.opening_balance, field_name="opening balance")
        request_hash = self._hash_command(command)
        created_transactions = []

        def run():
            account = self.ledger.create_account(
                command.name,
                command.type,
                command.currency,
                institution_type=command.institution_type,
                subtype=command.subtype,
                institution=command.institution,
                book_id=book_id,
            )
            if command.opening_balance:
                equity = self.ledger.create_account(
                    f"Opening equity for {command.name}",
                    "equity",
                    command.currency,
                    institution_type="system",
                    subtype="opening_equity",
                    institution="track-anywhere",
                    book_id=book_id,
                )
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
        if replay:
            self._persist_idempotency()
        else:
            self._persist_ledger_change(*created_transactions)
        return account, replay

    def list_accounts(
        self,
        token: str,
        *,
        name: str | None = None,
        type: str | None = None,
        currency: str | None = None,
        institution_type: str | None = None,
        subtype: str | None = None,
        institution: str | None = None,
        book_id: str | None = DEFAULT_BOOK_ID,
    ) -> list[Account]:
        if book_id is not None:
            self.actor_for_book(token, book_id, "account:read")
        else:
            self.actor_from_token(token, "account:read")
        return self.storage.list_accounts(
            book_id=book_id,
            name=name,
            type=type,
            currency=currency,
            institution_type=institution_type,
            subtype=subtype,
            institution=institution,
        )

    def get_account(self, token: str, account_id: str) -> Account:
        account = self.storage.get_account(account_id)
        self.actor_for_book(token, account.book_id, "account:read")
        return account

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

        accounts = self.storage.list_accounts(book_id=DEFAULT_BOOK_ID, currency=currency, institution_type=institution_type)
        accounts = [account for account in accounts if include_system or account.type in {"asset", "liability", "fund"}]
        balances = self.storage.account_balances(account.account_id for account in accounts)

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
        if replay:
            self._persist_idempotency()
        else:
            self._persist_ledger_change(accounts=(account,))
        return account, replay
