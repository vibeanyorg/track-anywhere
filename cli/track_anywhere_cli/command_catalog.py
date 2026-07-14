from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable
from urllib.parse import quote

from .config import CliConfig
from .http import with_query


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
    if command == "book" and subcommand in {"create", "balances", "reporting-lines"}:
        return f"book.{subcommand.replace('-', '_')}"
    if command == "asset" and subcommand == "create":
        return "asset.create"
    if command == "account" and subcommand in {"create", "close"}:
        return f"account.{subcommand}"
    if command == "category" and subcommand == "create":
        return "category.create"
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


def request_create_account(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    payload = compact_payload(
        {
            "account_id": args.account_id,
            "asset_code": args.asset_code,
            "account_type": args.account_type,
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


CATALOG_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "book.create": request_create_book,
    "asset.create": request_create_asset,
    "account.create": request_create_account,
    "account.close": request_close_account,
    "category.create": request_create_category,
    "book.balances": request_book_balances,
    "book.reporting_lines": request_book_reporting_lines,
}
