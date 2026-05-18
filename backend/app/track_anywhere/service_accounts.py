from __future__ import annotations

from decimal import Decimal
from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import CreateAccountCommand, UpdateAccountMetadataCommand
from .errors import ValidationError
from .ledger import Account, Posting


class AccountUseCases:
    def create_account(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Account, bool]:
        command = CreateAccountCommand.model_validate(payload)
        book_id = command.book_id or DEFAULT_BOOK_ID
        actor = self.actor_for_book(token, book_id, "account:write")
        request_hash = self._hash_command(command)

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
                self.ledger.create_transaction(
                    memo=f"Opening balance: {command.name}",
                    postings=[
                        Posting(account.account_id, command.opening_balance, command.currency),
                        Posting(equity.account_id, -command.opening_balance, command.currency),
                    ],
                    book_id=book_id,
                )
            self.audit.record(
                operation="account.create",
                actor=actor,
                entity_ref=account.account_id,
                details=command.model_dump(mode="json"),
            )
            return account

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="account.create",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

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
        accounts = list(self.ledger.accounts.values())
        if book_id is not None:
            accounts = [account for account in accounts if account.book_id == book_id]
        if name:
            lowered = name.lower()
            accounts = [account for account in accounts if lowered in account.name.lower()]
        if type:
            accounts = [account for account in accounts if account.type == type]
        if currency:
            accounts = [account for account in accounts if account.currency == currency]
        if institution_type:
            accounts = [account for account in accounts if account.institution_type == institution_type]
        if subtype:
            accounts = [account for account in accounts if account.subtype == subtype]
        if institution:
            lowered = institution.lower()
            accounts = [account for account in accounts if account.institution and lowered in account.institution.lower()]
        return sorted(
            accounts,
            key=lambda account: (
                account.type,
                account.institution_type or "",
                account.subtype or "",
                account.name,
                account.account_id,
            ),
        )

    def get_account(self, token: str, account_id: str) -> Account:
        account = self.ledger.get_account(account_id)
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

        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for account in self.ledger.accounts.values():
            if account.book_id != DEFAULT_BOOK_ID:
                continue
            if not include_system and account.type not in {"asset", "liability", "fund"}:
                continue
            if currency and account.currency != currency:
                continue
            if institution_type and account.institution_type != institution_type:
                continue

            account_currency = account.currency
            amount = self.ledger.balance(account.account_id).get(account_currency, Decimal("0"))
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
        account = self.ledger.get_account(account_id)
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

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="account.metadata.update",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result
