from __future__ import annotations

import click

from .click_common import common_args, output_options, pass_state, run_api


def register(root: click.Group) -> None:
    _register_books(root)
    _register_assets(root)
    _register_accounts(root)
    _register_categories(root)


def _register_books(root: click.Group) -> None:
    @root.group()
    def book() -> None:
        """Create and query V2 Books."""

    @book.command("create")
    @click.argument("book_id")
    @click.option("--name", required=True)
    @click.option("--base-asset-code")
    @output_options
    @pass_state
    def create_book(state, json_mode, no_color, book_id, name, base_asset_code):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="book",
            book_command="create",
            book_id=book_id,
            name=name,
            base_asset_code=base_asset_code,
        )
        return run_api(args, state=state, command_path="book.create")

    @book.command("list")
    @output_options
    @pass_state
    def list_books(state, json_mode, no_color):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="book",
            book_command="list",
        )
        return run_api(args, state=state, command_path="book.list")

    @book.command("balances")
    @click.argument("book_id")
    @click.option("--as-of-book-position", type=int)
    @output_options
    @pass_state
    def balances(state, json_mode, no_color, book_id, as_of_book_position):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="book",
            book_command="balances",
            book_id=book_id,
            as_of_book_position=as_of_book_position,
        )
        return run_api(args, state=state, command_path="book.balances")

    @book.command("reporting-lines")
    @click.argument("book_id")
    @click.option("--as-of-book-position", type=int, required=True)
    @output_options
    @pass_state
    def reporting_lines(
        state,
        json_mode,
        no_color,
        book_id,
        as_of_book_position,
    ):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="book",
            book_command="reporting-lines",
            book_id=book_id,
            as_of_book_position=as_of_book_position,
        )
        return run_api(args, state=state, command_path="book.reporting_lines")


def _register_assets(root: click.Group) -> None:
    @root.group()
    def asset() -> None:
        """Create and query V2 assets."""

    @asset.command("create")
    @click.argument("book_id")
    @click.argument("asset_code")
    @click.option("--kind", required=True)
    @click.option("--ledger-scale", type=int, required=True)
    @click.option("--input-scale", type=int, required=True)
    @click.option("--display-scale", type=int, required=True)
    @click.option("--name", required=True)
    @output_options
    @pass_state
    def create_asset(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="asset",
            asset_command="create",
            **values,
        )
        return run_api(args, state=state, command_path="asset.create")

    @asset.command("list")
    @click.argument("book_id")
    @output_options
    @pass_state
    def list_assets(state, json_mode, no_color, book_id):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="asset",
            asset_command="list",
            book_id=book_id,
        )
        return run_api(args, state=state, command_path="asset.list")


def _register_accounts(root: click.Group) -> None:
    @root.group()
    def account() -> None:
        """Create, query, close, and reopen V2 accounts."""

    @account.command("create")
    @click.argument("book_id")
    @click.argument("account_id")
    @click.option("--asset-code", required=True)
    @click.option("--type", "account_type", required=True)
    @click.option(
        "--account-subtype",
        help="Optional lowercase subtype slug, for example credit_card.",
    )
    @click.option("--name", required=True)
    @click.option("--system-role")
    @output_options
    @pass_state
    def create_account(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="account",
            account_command="create",
            **values,
        )
        return run_api(args, state=state, command_path="account.create")

    @account.command("list")
    @click.argument("book_id")
    @click.option(
        "--type",
        "account_type",
        type=click.Choice(
            (
                "asset",
                "liability",
                "equity",
                "income",
                "expense",
                "fund",
                "system",
            )
        ),
    )
    @click.option("--subtype", "account_subtype")
    @click.option("--status", type=click.Choice(("active", "closed")))
    @click.option("--asset-code")
    @click.option("--name", help="Case-insensitive current-name substring.")
    @output_options
    @pass_state
    def list_accounts(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="account",
            account_command="list",
            **values,
        )
        return run_api(args, state=state, command_path="account.list")

    @account.command("show")
    @click.argument("book_id")
    @click.argument("account_id")
    @output_options
    @pass_state
    def show_account(state, json_mode, no_color, book_id, account_id):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="account",
            account_command="show",
            book_id=book_id,
            account_id=account_id,
        )
        return run_api(args, state=state, command_path="account.show")

    @account.command("balance")
    @click.argument("book_id")
    @click.argument("account_id")
    @output_options
    @pass_state
    def account_balance(state, json_mode, no_color, book_id, account_id):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="account",
            account_command="balance",
            book_id=book_id,
            account_id=account_id,
        )
        return run_api(args, state=state, command_path="account.balance")

    @account.command("close")
    @click.argument("book_id")
    @click.argument("account_id")
    @output_options
    @pass_state
    def close_account(state, json_mode, no_color, book_id, account_id):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="account",
            account_command="close",
            book_id=book_id,
            account_id=account_id,
        )
        return run_api(args, state=state, command_path="account.close")

    @account.command("reopen")
    @click.argument("book_id")
    @click.argument("account_id")
    @output_options
    @pass_state
    def reopen_account(state, json_mode, no_color, book_id, account_id):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="account",
            account_command="reopen",
            book_id=book_id,
            account_id=account_id,
        )
        return run_api(args, state=state, command_path="account.reopen")


def _register_categories(root: click.Group) -> None:
    @root.group()
    def category() -> None:
        """Create and query V2 reporting categories."""

    @category.command("create")
    @click.argument("book_id")
    @click.argument("category_id")
    @click.option("--category-version-id", required=True)
    @click.option("--name", required=True)
    @click.option("--parent-category-id")
    @click.option("--change-reason-code", required=True)
    @output_options
    @pass_state
    def create_category(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="category",
            category_command="create",
            **values,
        )
        return run_api(args, state=state, command_path="category.create")

    @category.command("list")
    @click.argument("book_id")
    @output_options
    @pass_state
    def list_categories(state, json_mode, no_color, book_id):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="category",
            category_command="list",
            book_id=book_id,
        )
        return run_api(args, state=state, command_path="category.list")
