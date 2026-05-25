from __future__ import annotations

import urllib.parse
from argparse import Namespace
from typing import Any, Callable

from .config import CliConfig, command_idempotency_key
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]


def compact_payload(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def handle_catalog_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    if args.command == "user" and args.user_command == "create":
        payload = {"username": args.username, "display_name": args.display_name}
        return requester(
            config,
            "POST",
            "/api/v1/users",
            payload,
            key=command_idempotency_key(args, "user-create"),
        )
    if args.command == "user" and args.user_command == "list":
        return requester(config, "GET", "/api/v1/users")
    if args.command == "category" and args.category_command == "create":
        payload = compact_payload({"kind": args.kind, "name": args.name, "parent_id": args.parent_id})
        return requester(
            config,
            "POST",
            "/api/v1/categories",
            payload,
            key=command_idempotency_key(args, "category-create"),
        )
    if args.command == "category" and args.category_command == "ensure":
        payload = compact_payload({"kind": args.kind, "path": args.path})
        return requester(
            config,
            "POST",
            "/api/v1/categories/ensure-path",
            payload,
            key=command_idempotency_key(args, "category-ensure"),
        )
    if args.command == "category" and args.category_command in {"list", "find"}:
        if getattr(args, "path", None):
            path = with_query(
                "/api/v1/categories/by-path",
                {"kind": args.kind, "path": args.path},
            )
            return requester(config, "GET", path)
        path = with_query(
            "/api/v1/categories",
            {"kind": args.kind, "name": args.name, "parent_id": args.parent_id},
        )
        return requester(config, "GET", path)
    if args.command == "category" and args.category_command == "show":
        return requester(config, "GET", f"/api/v1/categories/{urllib.parse.quote(args.category_id)}")
    if args.command == "category" and args.category_command == "update":
        payload = compact_payload(
            {
                "name": args.name,
                "parent_id": args.parent_id,
                "icon": args.icon,
                "color": args.color,
                "sort_order": args.sort_order,
                "status": args.status,
            }
        )
        return requester(
            config,
            "PATCH",
            f"/api/v1/categories/{urllib.parse.quote(args.category_id)}",
            payload,
            key=command_idempotency_key(args, "category-update"),
        )
    if args.command == "credit-card" and args.credit_card_command == "list":
        return requester(config, "GET", "/api/v1/credit-cards")
    if args.command == "credit-card" and args.credit_card_command == "show":
        return requester(config, "GET", f"/api/v1/credit-cards/{urllib.parse.quote(args.account_id)}")
    if args.command == "credit-card" and args.credit_card_command == "update":
        payload = compact_payload(
            {
                "credit_limit": args.credit_limit,
                "available_credit": args.available_credit,
                "statement_day": args.statement_day,
                "due_day": args.due_day,
                "annual_fee": args.annual_fee,
            }
        )
        return requester(
            config,
            "PATCH",
            f"/api/v1/credit-cards/{urllib.parse.quote(args.account_id)}",
            payload,
            key=command_idempotency_key(args, "credit-card-update"),
        )
    if args.command == "summary" and args.summary_command == "accounts":
        path = with_query(
            "/api/v1/summary/accounts",
            {
                "group_by": args.group_by,
                "currency": args.currency,
                "institution_type": args.institution_type,
                "include_system": "true" if args.include_system else None,
            },
        )
        return requester(config, "GET", path)
    if args.command == "summary" and args.summary_command == "categories":
        path = with_query("/api/v1/summary/categories", {"kind": args.kind, "currency": args.currency})
        return requester(config, "GET", path)
    if args.command == "account" and args.account_command in {"list", "find"}:
        path = with_query(
            "/api/v1/accounts",
            {
                "name": args.name,
                "type": args.type,
                "currency": args.currency,
                "institution_type": args.institution_type,
                "subtype": args.subtype,
                "institution": args.institution,
            },
        )
        return requester(config, "GET", path)
    if args.command == "account" and args.account_command == "show":
        return requester(config, "GET", f"/api/v1/accounts/{urllib.parse.quote(args.account_id)}")
    if args.command == "account" and args.account_command == "update":
        payload = compact_payload(
            {
                "institution_type": args.institution_type,
                "subtype": args.subtype,
                "institution": args.institution,
            }
        )
        return requester(
            config,
            "PATCH",
            f"/api/v1/accounts/{urllib.parse.quote(args.account_id)}",
            payload,
            key=command_idempotency_key(args, "account-update"),
        )
    if args.command == "account-create" or (args.command == "account" and args.account_command == "create"):
        payload = compact_payload(
            {
                "name": args.name,
                "type": args.type,
                "currency": args.currency,
                "opening_balance": args.opening_balance,
                "institution_type": args.institution_type,
                "subtype": args.subtype,
                "institution": args.institution,
            }
        )
        return requester(
            config,
            "POST",
            "/api/v1/accounts",
            payload,
            key=command_idempotency_key(args, "account-create"),
        )
    return None
