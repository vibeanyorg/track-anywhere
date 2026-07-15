from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable
from urllib.parse import quote

from .config import CliConfig
from .http import with_query
from .structured_inputs import validate_account_semantics


Requester = Callable[
    [CliConfig, str, str, dict[str, Any] | None, str | None],
    tuple[int, Any],
]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


def compact_payload(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def handle_catalog_command(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any] | None:
    command_path = infer_catalog_command_path(args)
    if command_path is None:
        return None
    handler = CATALOG_COMMAND_HANDLERS.get(command_path)
    if handler is None:
        return None
    return handler(args, config, requester)


def infer_catalog_command_path(args: Namespace) -> str | None:
    command = getattr(args, "command", None)
    subcommand = getattr(args, f"{command}_command", None)
    if command == "book" and subcommand in {
        "create",
        "list",
        "balances",
        "reporting-lines",
    }:
        return f"book.{subcommand.replace('-', '_')}"
    if command == "asset" and subcommand in {"create", "list"}:
        return f"asset.{subcommand}"
    if command == "account" and subcommand in {
        "create",
        "list",
        "show",
        "balance",
        "close",
        "reopen",
    }:
        return f"account.{subcommand}"
    if command == "category" and subcommand in {"create", "list"}:
        return f"category.{subcommand}"
    return None


def request_create_book(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    payload = compact_payload(
        {
            "book_id": args.book_id,
            "current_name": args.name,
            "base_asset_code": args.base_asset_code,
        }
    )
    return requester(config, "POST", "/api/v2/books", payload, None)


def request_list_books(
    _args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(config, "GET", "/api/v2/books", None, None)


def request_create_asset(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    payload = {
        "asset_code": args.asset_code,
        "kind": args.kind,
        "ledger_scale": args.ledger_scale,
        "input_scale": args.input_scale,
        "display_scale": args.display_scale,
        "current_name": args.name,
    }
    return requester(
        config,
        "POST",
        f"/api/v2/books/{_path(args.book_id)}/assets",
        payload,
        None,
    )


def request_list_assets(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(
        config,
        "GET",
        f"/api/v2/books/{_path(args.book_id)}/assets",
        None,
        None,
    )


def request_create_account(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    account_subtype = getattr(args, "account_subtype", None)
    try:
        validate_account_semantics(args.account_type, account_subtype)
    except ValueError as error:
        return _invalid_input(error)

    payload = compact_payload(
        {
            "account_id": args.account_id,
            "asset_code": args.asset_code,
            "account_type": args.account_type,
            "account_subtype": account_subtype,
            "current_name": args.name,
            "system_role": args.system_role,
        }
    )
    return requester(
        config,
        "POST",
        f"/api/v2/books/{_path(args.book_id)}/accounts",
        payload,
        None,
    )


def request_list_accounts(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    path = with_query(
        f"/api/v2/books/{_path(args.book_id)}/accounts",
        {
            "account_type": args.account_type,
            "account_subtype": args.account_subtype,
            "status": args.status,
            "asset_code": args.asset_code,
            "name": args.name,
        },
    )
    return requester(config, "GET", path, None, None)


def request_show_account(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(
        config,
        "GET",
        f"/api/v2/books/{_path(args.book_id)}/accounts/{_path(args.account_id)}",
        None,
        None,
    )


def request_account_balance(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(
        config,
        "GET",
        (
            f"/api/v2/books/{_path(args.book_id)}/accounts/"
            f"{_path(args.account_id)}/balance"
        ),
        None,
        None,
    )


def request_close_account(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(
        config,
        "POST",
        (
            f"/api/v2/books/{_path(args.book_id)}/accounts/"
            f"{_path(args.account_id)}/close"
        ),
        None,
        None,
    )


def request_reopen_account(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(
        config,
        "POST",
        (
            f"/api/v2/books/{_path(args.book_id)}/accounts/"
            f"{_path(args.account_id)}/reopen"
        ),
        None,
        None,
    )


def request_create_category(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    payload = compact_payload(
        {
            "category_id": args.category_id,
            "category_version_id": args.category_version_id,
            "name": args.name,
            "parent_category_id": args.parent_category_id,
            "change_reason_code": args.change_reason_code,
        }
    )
    return requester(
        config,
        "POST",
        f"/api/v2/books/{_path(args.book_id)}/categories",
        payload,
        None,
    )


def request_list_categories(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(
        config,
        "GET",
        f"/api/v2/books/{_path(args.book_id)}/categories",
        None,
        None,
    )


def request_book_balances(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    path = with_query(
        f"/api/v2/books/{_path(args.book_id)}/balances",
        {"as_of_book_position": args.as_of_book_position},
    )
    return requester(config, "GET", path, None, None)


def request_book_reporting_lines(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    path = with_query(
        f"/api/v2/books/{_path(args.book_id)}/reporting-lines",
        {"as_of_book_position": args.as_of_book_position},
    )
    return requester(config, "GET", path, None, None)


def _path(value: object) -> str:
    return quote(str(value), safe="")


def _invalid_input(error: ValueError) -> tuple[int, dict[str, Any]]:
    return 422, {
        "detail": str(error),
        "error": {
            "code": "invalid_v2_cli_input",
            "category": "validation",
            "message": str(error),
            "retryable": False,
        },
    }


CATALOG_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "book.create": request_create_book,
    "book.list": request_list_books,
    "asset.create": request_create_asset,
    "asset.list": request_list_assets,
    "account.create": request_create_account,
    "account.list": request_list_accounts,
    "account.show": request_show_account,
    "account.balance": request_account_balance,
    "account.close": request_close_account,
    "account.reopen": request_reopen_account,
    "category.create": request_create_category,
    "category.list": request_list_categories,
    "book.balances": request_book_balances,
    "book.reporting_lines": request_book_reporting_lines,
}
