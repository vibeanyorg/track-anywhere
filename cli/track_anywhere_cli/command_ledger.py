from __future__ import annotations

import urllib.parse
from argparse import Namespace
from typing import Any, Callable

from .config import CliConfig, command_idempotency_key
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]


def _transaction_payload(args: Namespace) -> dict[str, Any]:
    payload = {
        "amount": args.amount,
        "currency": args.currency,
        "from_account_id": args.from_account_id,
        "to_account_id": args.to_account_id,
        "purpose": args.purpose,
    }
    if args.occurred_at:
        payload["occurred_at"] = args.occurred_at
    if args.category_id:
        payload["category_id"] = args.category_id
    return payload


def handle_ledger_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    if args.command == "capture":
        payload = {
            "memo": args.memo,
            "amount": args.amount,
            "currency": args.currency,
            "source_account_id": args.source_account_id,
            "expense_account_id": args.expense_account_id,
        }
        if args.dry_run:
            return 200, {"dry_run": True, "policy_decision": "would_create_draft", "payload": payload}
        return requester(
            config,
            "POST",
            "/api/v1/drafts/capture",
            payload,
            key=command_idempotency_key(args, "draft-capture"),
        )
    if args.command == "draft-confirm":
        return requester(
            config,
            "POST",
            "/api/v1/drafts/confirm",
            {"draft_id": args.draft_id, "expected_version": args.expected_version},
            key=command_idempotency_key(args, "draft-confirm"),
        )
    if args.command in {"record"} or (args.command == "tx" and args.tx_command == "record"):
        return requester(
            config,
            "POST",
            "/api/v1/ledger/transactions",
            _transaction_payload(args),
            key=command_idempotency_key(args, "tx-record"),
        )
    if args.command == "expense" and args.expense_command == "record":
        payload = {
            "amount": args.amount,
            "currency": args.currency,
            "from_account_id": args.from_account_id,
            "category_id": args.category_id,
            "purpose": args.purpose,
        }
        if args.occurred_at:
            payload["occurred_at"] = args.occurred_at
        return requester(config, "POST", "/api/v1/expenses", payload, key=command_idempotency_key(args, "expense-record"))
    if args.command == "income" and args.income_command == "record":
        payload = {
            "amount": args.amount,
            "currency": args.currency,
            "to_account_id": args.to_account_id,
            "category_id": args.category_id,
            "purpose": args.purpose,
        }
        if args.occurred_at:
            payload["occurred_at"] = args.occurred_at
        return requester(config, "POST", "/api/v1/incomes", payload, key=command_idempotency_key(args, "income-record"))
    if args.command == "tx" and args.tx_command == "list":
        path = with_query(
            "/api/v1/ledger/transactions",
            {"account_id": args.account_id, "category_id": args.category_id, "limit": args.limit},
        )
        return requester(config, "GET", path)
    if args.command == "tx" and args.tx_command == "show":
        return requester(config, "GET", f"/api/v1/ledger/transactions/{urllib.parse.quote(args.transaction_id)}")
    if args.command == "tx" and args.tx_command == "reverse":
        payload = {"transaction_id": args.transaction_id, "memo": args.memo}
        return requester(
            config,
            "POST",
            "/api/v1/ledger/reverse",
            payload,
            key=command_idempotency_key(args, "tx-reverse"),
        )
    if args.command == "balance-adjust" or (args.command == "account" and args.account_command == "adjust"):
        payload = {
            "account_id": args.account_id,
            "amount": args.amount,
            "currency": args.currency,
            "purpose": args.purpose,
        }
        if args.occurred_at:
            payload["occurred_at"] = args.occurred_at
        return requester(
            config,
            "POST",
            "/api/v1/ledger/adjustments",
            payload,
            key=command_idempotency_key(args, "balance-adjust"),
        )
    if args.command == "balance" or (args.command == "account" and args.account_command == "balance"):
        suffix = "?include_drafts=true" if args.include_drafts else ""
        return requester(config, "GET", f"/api/v1/query/accounts/{args.account_id}/balance{suffix}")
    return None
