from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

from .audit import AuditLog
from .attachments import AttachmentIntake
from .budgets import BudgetBook
from .commands import (
    BalanceAdjustmentCommand,
    CaptureDraftCommand,
    ConfirmDraftCommand,
    CreateAccountCommand,
    CreateFundCommand,
    CreateUserCommand,
    FundAllocationCommand,
    FundSpendCommand,
    IssueCredentialCommand,
    ReconciliationActionCommand,
    RecordTransactionCommand,
    RejectDraftCommand,
    RevokeCredentialCommand,
    ReverseTransactionCommand,
    SupersedeDraftCommand,
    UpdateAccountMetadataCommand,
)
from .drafts import DraftBook
from .errors import NotFound, StaleVersion, ValidationError
from .idempotency import IdempotencyStore
from .ledger import Account, Ledger, Posting, Transaction
from .security import Actor, CredentialStore, DeploymentSecurityConfig, validate_startup_security
from .storage import OrmStorage, new_owner_token
from .users import AppUser, UserDirectory


OWNER_SCOPES = {
    "account:read",
    "account:write",
    "capture:draft",
    "ledger:confirm",
    "ledger:read",
    "ledger:reverse",
    "budget:write",
    "attachment:write",
    "credential:write",
    "user:read",
    "user:write",
}
AGENT_ALLOWED_SCOPES = OWNER_SCOPES - {"credential:write"}

SYSTEM_ACTOR = Actor(actor_id="system", actor_type="system", scopes=frozenset())


class FinanceService:
    def __init__(self, config: DeploymentSecurityConfig | None = None, *, database_url: str | None = None) -> None:
        self.config = config or DeploymentSecurityConfig()
        self.startup_warnings = validate_startup_security(self.config)
        self.storage = OrmStorage(database_url)
        self.credentials = CredentialStore()
        self.audit = AuditLog()
        self.idempotency = IdempotencyStore()
        self.ledger = Ledger()
        self.drafts = DraftBook()
        self.budgets = BudgetBook()
        self.attachments = AttachmentIntake(self.config)
        self.users = UserDirectory()
        self.reconciliation_actions: list[dict[str, Any]] = []
        self.adjustment_account_ids: dict[str, str] = {}
        self.owner_token = new_owner_token()
        self.storage.load_into(self)
        try:
            actor = self.credentials.verify(self.owner_token)
            if not OWNER_SCOPES.issubset(actor.scopes):
                self.credentials.issue(
                    actor_id="owner",
                    actor_type="human",
                    scopes=set(OWNER_SCOPES),
                    ttl=timedelta(days=30),
                    token=self.owner_token,
                )
        except Exception:
            self.credentials.issue(
                actor_id="owner",
                actor_type="human",
                scopes=set(OWNER_SCOPES),
                ttl=timedelta(days=30),
                token=self.owner_token,
            )
        self._persist()

    def actor_from_token(self, token: str, required_scope: str | None = None) -> Actor:
        return self.credentials.verify(token, required_scope=required_scope)

    def issue_agent_credential(self, token: str, scopes: set[str], ttl_minutes: int = 30) -> str:
        actor = self.actor_from_token(token, "credential:write")
        if actor.actor_type != "human":
            raise ValidationError("only human owner credentials can issue agent credentials")
        unknown = scopes - AGENT_ALLOWED_SCOPES
        if unknown:
            raise ValidationError(f"unknown credential scopes: {sorted(unknown)}")
        agent_token = self.credentials.issue(
            actor_id="agent",
            actor_type="agent",
            scopes=scopes,
            ttl=timedelta(minutes=ttl_minutes),
        )
        self.audit.record(
            operation="credential.issue",
            actor=actor,
            entity_ref="agent",
            details={"scopes": sorted(scopes), "token": agent_token},
        )
        self._persist()
        return agent_token

    def create_account(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Account, bool]:
        actor = self.actor_from_token(token, "account:write")
        command = CreateAccountCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            account = self.ledger.create_account(
                command.name,
                command.type,
                command.currency,
                institution_type=command.institution_type,
                subtype=command.subtype,
                institution=command.institution,
            )
            if command.opening_balance:
                equity = self.ledger.create_account(
                    f"Opening equity for {command.name}",
                    "equity",
                    command.currency,
                    institution_type="system",
                    subtype="opening_equity",
                    institution="track-anywhere",
                )
                self.ledger.create_transaction(
                    memo=f"Opening balance: {command.name}",
                    postings=[
                        Posting(account.account_id, command.opening_balance, command.currency),
                        Posting(equity.account_id, -command.opening_balance, command.currency),
                    ],
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
    ) -> list[Account]:
        self.actor_from_token(token, "account:read")
        accounts = list(self.ledger.accounts.values())
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
        self.actor_from_token(token, "account:read")
        return self.ledger.get_account(account_id)

    def account_summary(
        self,
        token: str,
        *,
        group_by: str = "subtype",
        currency: str | None = None,
        institution_type: str | None = None,
        include_system: bool = False,
    ) -> dict[str, Any]:
        self.actor_from_token(token, "account:read")
        allowed_groupings = {"type", "institution_type", "subtype", "institution", "currency"}
        if group_by not in allowed_groupings:
            raise ValidationError(f"group_by must be one of {sorted(allowed_groupings)}")

        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for account in self.ledger.accounts.values():
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
                    "account_count": 0,
                    "account_ids": [],
                    "types": set(),
                },
            )
            group["amount"] += amount
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
                    "account_count": group["account_count"],
                    "account_ids": sorted(group["account_ids"]),
                    "types": sorted(group["types"]),
                }
                for group in sorted(groups.values(), key=lambda item: (item["currency"], item["key"]))
            ],
        }

    def update_account_metadata(self, token: str, account_id: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Account, bool]:
        actor = self.actor_from_token(token, "account:write")
        command = UpdateAccountMetadataCommand.model_validate(payload)
        if command.institution_type is None and command.subtype is None and command.institution is None:
            raise ValidationError("at least one account metadata field is required")
        request_hash = self._hash_command_payload(command, {"account_id": account_id})

        def run():
            account = self.ledger.get_account(account_id)
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

    def create_user(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[AppUser, bool]:
        actor = self.actor_from_token(token, "user:write")
        command = CreateUserCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            user = self.users.create(username=command.username, display_name=command.display_name)
            self.audit.record(
                operation="user.create",
                actor=actor,
                entity_ref=user.user_id,
                details=command.model_dump(mode="json"),
            )
            return user

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="user.create",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def list_users(self, token: str) -> list[AppUser]:
        self.actor_from_token(token, "user:read")
        return self.users.list()

    def capture_draft(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Any, bool]:
        actor = self.actor_from_token(token, "capture:draft")
        command = CaptureDraftCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            draft = self._draft_from_capture_command(command, actor=actor)
            self.audit.record(
                operation="draft.capture",
                actor=actor,
                entity_ref=draft.draft_id,
                details={"command": command.model_dump(mode="json"), "state": draft.state},
            )
            return draft

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="draft.capture",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def record_transaction(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "ledger:confirm")
        command = RecordTransactionCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            transaction = self.ledger.create_transaction(
                memo=command.purpose,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=[
                    Posting(command.from_account_id, -command.amount, command.currency),
                    Posting(command.to_account_id, command.amount, command.currency),
                ],
            )
            self.audit.record(
                operation="ledger.transaction.record",
                actor=actor,
                entity_ref=transaction.transaction_id,
                details=command.model_dump(mode="json"),
            )
            return transaction

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.transaction.record",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def list_transactions(
        self,
        token: str,
        *,
        account_id: str | None = None,
        limit: int = 20,
    ) -> list[Transaction]:
        self.actor_from_token(token, "ledger:read")
        if account_id:
            self.ledger.get_account(account_id)
        transactions = list(self.ledger.transactions.values())
        if account_id:
            transactions = [
                transaction
                for transaction in transactions
                if any(posting.account_id == account_id for posting in transaction.postings)
            ]
        transactions.sort(key=lambda transaction: (transaction.occurred_at, transaction.transaction_id), reverse=True)
        return transactions[: max(0, min(limit, 200))]

    def get_transaction(self, token: str, transaction_id: str) -> Transaction:
        self.actor_from_token(token, "ledger:read")
        transaction = self.ledger.transactions.get(transaction_id)
        if transaction is None:
            raise NotFound(f"transaction not found: {transaction_id}")
        return transaction

    def adjust_balance(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "ledger:confirm")
        command = BalanceAdjustmentCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            account = self.ledger.get_account(command.account_id)
            if account.currency != command.currency:
                raise ValidationError("balance adjustment currency must match account currency")
            adjustment_account_id = self._system_adjustment_account_id(command.currency)
            transaction = self.ledger.create_transaction(
                memo=command.purpose,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=[
                    Posting(command.account_id, command.amount, command.currency),
                    Posting(adjustment_account_id, -command.amount, command.currency),
                ],
            )
            self.audit.record(
                operation="ledger.balance.adjust",
                actor=actor,
                entity_ref=command.account_id,
                details={
                    **command.model_dump(mode="json"),
                    "transaction_id": transaction.transaction_id,
                    "offset_account_id": adjustment_account_id,
                },
            )
            return transaction

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.balance.adjust",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def confirm_draft(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Transaction, bool]:
        actor = self.actor_from_token(token, "ledger:confirm")
        command = ConfirmDraftCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            draft = self.drafts.get(command.draft_id)
            if draft is None:
                raise NotFound(f"draft not found: {command.draft_id}")
            if draft.version != command.expected_version:
                raise StaleVersion("draft version conflict")
            if draft.state != "ready_to_confirm":
                raise ValidationError("draft is not ready to confirm")
            transaction = self.ledger.create_transaction(draft.memo, draft.proposed_postings)
            draft.state = "confirmed"
            draft.version += 1
            self.audit.record(
                operation="draft.confirm",
                actor=actor,
                entity_ref=draft.draft_id,
                details={"transaction_id": transaction.transaction_id},
            )
            return transaction

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="draft.confirm",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def reject_draft(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "ledger:confirm")
        command = RejectDraftCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            draft = self.drafts.reject(command.draft_id, command.expected_version)
            self.audit.record(
                operation="draft.reject",
                actor=actor,
                entity_ref=draft.draft_id,
                details={"reason": command.reason, "state": draft.state, "version": draft.version},
            )
            return draft

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="draft.reject",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def supersede_draft(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "capture:draft")
        command = SupersedeDraftCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            replacement = self._draft_from_capture_command(command.replacement, actor=actor)
            replacement = self.drafts.supersede(command.draft_id, command.expected_version, replacement)
            self.audit.record(
                operation="draft.supersede",
                actor=actor,
                entity_ref=command.draft_id,
                details={"replacement_draft_id": replacement.draft_id, "replacement_state": replacement.state},
            )
            return replacement

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="draft.supersede",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def create_fund(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "budget:write")
        command = CreateFundCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            account = self.ledger.create_account(
                command.name,
                "fund",
                command.currency,
                institution_type="system",
                subtype="fund",
                institution="track-anywhere",
            )
            fund = self.budgets.create(name=command.name, account_id=account.account_id, currency=command.currency)
            self.audit.record(operation="fund.create", actor=actor, entity_ref=fund.fund_id, details=command.model_dump())
            return fund

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="fund.create",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def allocate_fund(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "budget:write")
        command = FundAllocationCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            fund = self.budgets.require_current(command.fund_id, command.expected_version)
            transaction = self.ledger.create_transaction(
                command.memo or f"Allocate to {fund.name}",
                [
                    Posting(command.source_account_id, -command.amount, command.currency),
                    Posting(fund.account_id, command.amount, command.currency),
                ],
            )
            updated = self.budgets.allocate(command.fund_id, command.expected_version, command.amount, transaction.transaction_id)
            self.audit.record(
                operation="fund.allocate",
                actor=actor,
                entity_ref=updated.fund_id,
                details={"transaction_id": transaction.transaction_id, "amount": str(command.amount)},
            )
            return {"fund": updated, "transaction": transaction}

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="fund.allocate",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def spend_fund(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "budget:write")
        command = FundSpendCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            fund = self.budgets.require_current(command.fund_id, command.expected_version)
            transaction = self.ledger.create_transaction(
                command.memo or f"Spend from {fund.name}",
                [
                    Posting(fund.account_id, -command.amount, command.currency),
                    Posting(command.expense_account_id, command.amount, command.currency),
                ],
            )
            updated = self.budgets.spend(command.fund_id, command.expected_version, command.amount, transaction.transaction_id)
            self.audit.record(
                operation="fund.spend",
                actor=actor,
                entity_ref=updated.fund_id,
                details={"transaction_id": transaction.transaction_id, "amount": str(command.amount)},
            )
            return {"fund": updated, "transaction": transaction}

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="fund.spend",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def reverse_transaction(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "ledger:reverse")
        command = ReverseTransactionCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            reversal = self.ledger.reverse_transaction(command.transaction_id, command.memo)
            self.audit.record(
                operation="ledger.reverse",
                actor=actor,
                entity_ref=command.transaction_id,
                details={"reversal_transaction_id": reversal.transaction_id},
            )
            return reversal

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.reverse",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def account_balance(self, token: str, account_id: str, *, include_drafts: bool = False) -> dict[str, Any]:
        self.actor_from_token(token, "account:read")
        official = self.ledger.balance(account_id)
        pending = self.drafts.projected_impact(account_id) if include_drafts else {}
        currency = self.ledger.get_account(account_id).currency
        official_amount = official.get(currency, Decimal("0"))
        pending_amount = pending.get(currency, Decimal("0"))
        result = {
            "account_id": account_id,
            "currency": currency,
            "official_balance": {
                "amount": str(official_amount),
                "source": "confirmed_postings",
                "as_of_ledger_version": len(self.ledger.transactions),
            },
            "default_view": "official",
            "provenance": {
                "confirmed_transaction_count": len(self.ledger.transactions),
                "draft_count": len(self.drafts.drafts),
            },
        }
        if include_drafts:
            result["projected_balance"] = {
                "amount": str(official_amount + pending_amount),
                "pending_impact": str(pending_amount),
                "included_draft_ids": [
                    draft.draft_id
                    for draft in self.drafts.drafts.values()
                    if any(posting.account_id == account_id for posting in draft.proposed_postings)
                ],
                "projection_version": len(self.drafts.drafts),
            }
        return result

    def upload_attachment(
        self,
        token: str,
        *,
        filename: str,
        mime_type: str,
        content: bytes,
        idempotency_key: str,
    ):
        actor = self.actor_from_token(token, "attachment:write")
        scanner_available = self.config.attachment_scanner_available
        request_hash = sha256(content + filename.encode() + mime_type.encode() + str(scanner_available).encode()).hexdigest()

        def run():
            attachment = self.attachments.ingest(
                filename=filename,
                mime_type=mime_type,
                content=content,
                scanner_available=scanner_available,
            )
            draft = self.drafts.create(
                memo=f"Review attachment {attachment.original_filename}",
                proposed_postings=[],
                missing_fields=["amount", "source_account_id", "expense_account_id"],
                source="ocr",
                confidence=0.0,
                attachment_id=attachment.attachment_id,
            )
            self.audit.record(
                operation="attachment.upload",
                actor=actor,
                entity_ref=attachment.attachment_id,
                details={"attachment": asdict(attachment), "draft_id": draft.draft_id},
            )
            return {"attachment": attachment, "draft": draft}

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="attachment.upload",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def issue_agent_credential_command(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "credential:write")
        command = IssueCredentialCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            agent_token = self.issue_agent_credential(token, set(command.scopes), command.ttl_minutes)
            return {"token": agent_token, "scopes": command.scopes, "ttl_minutes": command.ttl_minutes}

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="credential.issue",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def revoke_credential_command(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "credential:write")
        if actor.actor_type != "human":
            raise ValidationError("only human owner credentials can revoke credentials")
        command = RevokeCredentialCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            self.credentials.revoke(command.target_token)
            self.audit.record(
                operation="credential.revoke",
                actor=actor,
                entity_ref="credential",
                details={"target_token": command.target_token, "reason": command.reason},
            )
            return {"revoked": True}

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="credential.revoke",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def record_reconciliation_action(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "ledger:confirm")
        command = ReconciliationActionCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            action = {
                "reconciliation_id": f"recon_{len(self.reconciliation_actions) + 1}",
                "summary": command.summary,
                "version": 1,
            }
            self.reconciliation_actions.append(action)
            self.audit.record(
                operation="reconciliation.record",
                actor=actor,
                entity_ref=action["reconciliation_id"],
                details=action,
            )
            return action

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="reconciliation.record",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def record_security_failure(self, operation: str, details: dict[str, Any] | None = None) -> None:
        self.audit.record(operation=operation, actor=SYSTEM_ACTOR, entity_ref=None, details=details or {})
        self._persist()

    def _persist(self) -> None:
        self.storage.save(self)

    @staticmethod
    def _hash_command(command) -> str:
        return sha256(command.model_dump_json().encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_command_payload(command, extra: dict[str, Any]) -> str:
        payload = {**extra, **command.model_dump(mode="json", exclude_none=True)}
        return sha256(str(sorted(payload.items())).encode("utf-8")).hexdigest()

    def _system_adjustment_account_id(self, currency: str) -> str:
        account_id = self.adjustment_account_ids.get(currency)
        if account_id is not None:
            return account_id
        account = self.ledger.create_account(
            f"System balance adjustments {currency}",
            "system",
            currency,
            institution_type="system",
            subtype="system_adjustment",
            institution="track-anywhere",
        )
        self.adjustment_account_ids[currency] = account.account_id
        return account.account_id

    def _draft_from_capture_command(self, command: CaptureDraftCommand, *, actor: Actor):
        proposed: list[Posting] = []
        missing: list[str] = []
        for field in ("amount", "source_account_id", "expense_account_id"):
            if getattr(command, field) in (None, ""):
                missing.append(field)
        if not missing:
            amount = command.amount
            source_account_id = command.source_account_id
            expense_account_id = command.expense_account_id
            if amount is None or source_account_id is None or expense_account_id is None:
                raise ValidationError("complete draft command lost required posting fields")
            proposed.extend(
                [
                    Posting(source_account_id, -amount, command.currency),
                    Posting(expense_account_id, amount, command.currency),
                ]
            )
        return self.drafts.create(
            memo=command.memo,
            proposed_postings=proposed,
            missing_fields=missing,
            source=actor.actor_type,
            confidence=command.confidence,
        )
