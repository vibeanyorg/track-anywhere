from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable
import urllib.parse

from .config import CliConfig, command_idempotency_key
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


def compact_payload(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def handle_catalog_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    command_path = infer_catalog_command_path(args)
    if command_path is None:
        return None
    handler = CATALOG_COMMAND_HANDLERS.get(command_path)
    if handler is None:
        return None
    return handler(args, config, requester)


def infer_catalog_command_path(args: Namespace) -> str | None:
    command = getattr(args, "command", None)
    if command == "account-create":
        return "account.create"
    if command == "account" and getattr(args, "account_command", None) in {"create", "list", "find", "show", "update"}:
        return f"account.{args.account_command}"
    if command == "category" and getattr(args, "category_command", None) in {"create", "ensure", "find", "list", "show", "update"}:
        return f"category.{args.category_command}"
    if command == "counterparty" and getattr(args, "counterparty_command", None) in {"create", "ensure", "list", "show"}:
        return f"counterparty.{args.counterparty_command}"
    if command == "credit-card" and getattr(args, "credit_card_command", None) in {"list", "show", "update"}:
        return f"credit_card.{args.credit_card_command}"
    if command == "financial-account" and getattr(args, "financial_account_command", None) in {"list", "show", "balance"}:
        return f"financial_account.{args.financial_account_command}"
    if command == "summary" and getattr(args, "summary_command", None) in {"accounts", "categories"}:
        return f"summary.{args.summary_command}"
    if command == "user" and getattr(args, "user_command", None) in {"create", "list"}:
        return f"user.{args.user_command}"
    return None


def request_create_user(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    user_payload = {"username": args.username, "display_name": args.display_name}
    return requester(config, "POST", "/api/v1/users", user_payload, key=command_idempotency_key(args, "user-create"))


def request_list_users(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(config, "GET", "/api/v1/users")


def request_ensure_counterparty(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    counterparty_payload = compact_payload({"name": args.name, "kind": args.kind, "slug": args.slug})
    return requester(config, "POST", "/api/v1/counterparties/ensure", counterparty_payload, key=command_idempotency_key(args, "counterparty-ensure"))


def request_create_counterparty(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    counterparty_payload = compact_payload({"name": args.name, "kind": args.kind, "slug": args.slug})
    return requester(config, "POST", "/api/v1/counterparties", counterparty_payload, key=command_idempotency_key(args, "counterparty-create"))


def request_list_counterparties(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    counterparty_query = with_query("/api/v1/counterparties", {"kind": args.kind, "status": args.status, "name": args.name})
    return requester(config, "GET", counterparty_query)


def request_show_counterparty(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(config, "GET", f"/api/v1/counterparties/{urllib.parse.quote(args.counterparty_ref)}")


def request_create_category(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    category_payload = compact_payload({"kind": args.kind, "name": args.name, "parent_id": args.parent_id})
    return requester(config, "POST", "/api/v1/categories", category_payload, key=command_idempotency_key(args, "category-create"))


def request_ensure_category(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    category_payload = compact_payload({"kind": args.kind, "path": args.path})
    return requester(config, "POST", "/api/v1/categories/ensure-path", category_payload, key=command_idempotency_key(args, "category-ensure"))


def request_query_categories(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    if getattr(args, "path", None):
        category_path_query = with_query("/api/v1/categories/by-path", {"kind": args.kind, "path": args.path})
        return requester(config, "GET", category_path_query)
    category_query = with_query("/api/v1/categories", {"kind": args.kind, "name": args.name, "parent_id": args.parent_id})
    return requester(config, "GET", category_query)


def request_show_category(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(config, "GET", f"/api/v1/categories/{urllib.parse.quote(args.category_id)}")


def request_update_category(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    category_payload = compact_payload(
        {"name": args.name, "parent_id": args.parent_id, "icon": args.icon, "color": args.color, "sort_order": args.sort_order, "status": args.status}
    )
    category_id = urllib.parse.quote(args.category_id)
    return requester(config, "PATCH", f"/api/v1/categories/{category_id}", category_payload, key=command_idempotency_key(args, "category-update"))


def request_list_credit_cards(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(config, "GET", "/api/v1/credit-cards")


def request_show_credit_card(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(config, "GET", f"/api/v1/credit-cards/{urllib.parse.quote(args.account_id)}")


def request_update_credit_card(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    credit_card_payload = compact_payload(
        {
            "credit_limit": args.credit_limit,
            "available_credit": args.available_credit,
            "statement_day": args.statement_day,
            "due_day": args.due_day,
            "annual_fee": args.annual_fee,
        }
    )
    account_id = urllib.parse.quote(args.account_id)
    return requester(config, "PATCH", f"/api/v1/credit-cards/{account_id}", credit_card_payload, key=command_idempotency_key(args, "credit-card-update"))


def request_list_financial_accounts(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    financial_account_query = with_query(
        "/api/v1/financial-accounts",
        {
            "q": args.q,
            "type": args.type,
            "currency": args.currency,
            "institution_type": args.institution_type,
            "subtype": args.subtype,
            "institution": args.institution,
            "status": args.status,
            "include": "balance" if args.include_balance else None,
        },
    )
    return requester(config, "GET", financial_account_query)


def request_show_financial_account(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    financial_account_query = with_query(
        f"/api/v1/financial-accounts/{urllib.parse.quote(args.account_id)}",
        {"include": "balance" if args.include_balance else None},
    )
    return requester(config, "GET", financial_account_query)


def request_financial_account_balance(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(config, "GET", f"/api/v1/financial-accounts/{urllib.parse.quote(args.account_id)}/balance")


def request_summary_accounts(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    summary_query = with_query(
        "/api/v1/summary/accounts",
        {
            "group_by": args.group_by,
            "currency": args.currency,
            "institution_type": args.institution_type,
            "include_system": "true" if args.include_system else None,
        },
    )
    return requester(config, "GET", summary_query)


def request_summary_categories(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    category_summary_query = with_query("/api/v1/summary/categories", {"kind": args.kind, "currency": args.currency})
    return requester(config, "GET", category_summary_query)


def request_query_accounts(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    account_query = with_query(
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
    return requester(config, "GET", account_query)


def request_show_account(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(config, "GET", f"/api/v1/accounts/{urllib.parse.quote(args.account_id)}")


def request_update_account(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    account_payload = compact_payload({"institution_type": args.institution_type, "subtype": args.subtype, "institution": args.institution})
    return requester(config, "PATCH", f"/api/v1/accounts/{urllib.parse.quote(args.account_id)}", account_payload, key=command_idempotency_key(args, "account-update"))


def request_create_account(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    account_payload = compact_payload(
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
    return requester(config, "POST", "/api/v1/accounts", account_payload, key=command_idempotency_key(args, "account-create"))


CATALOG_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "user.create": request_create_user,
    "user.list": request_list_users,
    "counterparty.ensure": request_ensure_counterparty,
    "counterparty.create": request_create_counterparty,
    "counterparty.list": request_list_counterparties,
    "counterparty.show": request_show_counterparty,
    "category.create": request_create_category,
    "category.ensure": request_ensure_category,
    "category.find": request_query_categories,
    "category.list": request_query_categories,
    "category.show": request_show_category,
    "category.update": request_update_category,
    "credit_card.list": request_list_credit_cards,
    "credit_card.show": request_show_credit_card,
    "credit_card.update": request_update_credit_card,
    "financial_account.list": request_list_financial_accounts,
    "financial_account.show": request_show_financial_account,
    "financial_account.balance": request_financial_account_balance,
    "summary.accounts": request_summary_accounts,
    "summary.categories": request_summary_categories,
    "account.create": request_create_account,
    "account.find": request_query_accounts,
    "account.list": request_query_accounts,
    "account.show": request_show_account,
    "account.update": request_update_account,
}
