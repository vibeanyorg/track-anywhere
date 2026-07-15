from __future__ import annotations

from argparse import Namespace
from typing import Any
from urllib.parse import quote

from .command_definition import CommandDefinition, Requester, api_command
from .config import CliConfig
from .http import with_query
from .structured_inputs import validate_account_semantics


def compact_payload(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


@api_command("book.create", mutating=True)
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


@api_command("book.list")
def request_list_books(
    _args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(config, "GET", "/api/v2/books", None, None)


@api_command("asset.create", mutating=True)
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


@api_command("asset.list")
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


@api_command("account.create", mutating=True)
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


@api_command("account.list")
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


@api_command("account.show")
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


@api_command("account.balance")
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


@api_command("account.close", mutating=True)
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


@api_command("account.reopen", mutating=True)
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


@api_command("category.create", mutating=True)
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


@api_command("category.list")
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


@api_command("book.balances")
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


@api_command("book.reporting_lines")
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


CATALOG_COMMAND_DEFINITIONS: tuple[CommandDefinition, ...] = (
    request_create_book,
    request_list_books,
    request_create_asset,
    request_list_assets,
    request_create_account,
    request_list_accounts,
    request_show_account,
    request_account_balance,
    request_close_account,
    request_reopen_account,
    request_create_category,
    request_list_categories,
    request_book_balances,
    request_book_reporting_lines,
)
