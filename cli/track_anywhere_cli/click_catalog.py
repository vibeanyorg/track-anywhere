from __future__ import annotations

import click

from .click_common import common_args, output_options, pass_state, run_api


def register(root: click.Group) -> None:
    _register_summary(root)
    _register_user(root)
    _register_category(root)
    _register_credit_card(root)
    _register_account(root)


def _register_summary(root: click.Group) -> None:
    @root.group()
    def summary():
        """Show account and category summaries."""

    @summary.command("accounts")
    @click.option("--group-by", default="subtype")
    @click.option("--currency")
    @click.option("--institution-type")
    @click.option("--include-system", is_flag=True)
    @output_options
    @pass_state
    def accounts(state, json_mode, no_color, **kwargs):
        args = common_args(state, json_mode, no_color, command="summary", summary_command="accounts", **kwargs)
        return run_api(args, state=state, command_path="summary.accounts")

    @summary.command("categories")
    @click.option("--kind", type=click.Choice(["income", "expense"]))
    @click.option("--currency")
    @output_options
    @pass_state
    def categories(state, json_mode, no_color, kind, currency):
        args = common_args(state, json_mode, no_color, command="summary", summary_command="categories", kind=kind, currency=currency)
        return run_api(args, state=state, command_path="summary.categories")


def _register_user(root: click.Group) -> None:
    @root.group()
    def user():
        """Manage users."""

    @user.command("create")
    @click.argument("username")
    @click.option("--display-name")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def create_user(state, json_mode, no_color, username, display_name, idempotency_key):
        args = common_args(state, json_mode, no_color, command="user", user_command="create", username=username, display_name=display_name, idempotency_key=idempotency_key)
        return run_api(args, state=state, command_path="user.create")

    @user.command("list")
    @output_options
    @pass_state
    def list_users(state, json_mode, no_color):
        args = common_args(state, json_mode, no_color, command="user", user_command="list")
        return run_api(args, state=state, command_path="user.list")


def _register_category(root: click.Group) -> None:
    @root.group()
    def category():
        """Manage categories."""

    for name in ("list", "find"):
        _category_query_command(category, name)

    @category.command("ensure")
    @click.option("--kind", type=click.Choice(["income", "expense"]), required=True)
    @click.option("--path", "category_path", required=True)
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def ensure_category(state, json_mode, no_color, kind, category_path, idempotency_key):
        args = common_args(state, json_mode, no_color, command="category", category_command="ensure", kind=kind, path=category_path, idempotency_key=idempotency_key)
        return run_api(args, state=state, command_path="category.ensure")

    @category.command("create")
    @click.argument("name")
    @click.option("--kind", type=click.Choice(["income", "expense"]), required=True)
    @click.option("--parent-id")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def create_category(state, json_mode, no_color, name, kind, parent_id, idempotency_key):
        args = common_args(state, json_mode, no_color, command="category", category_command="create", name=name, kind=kind, parent_id=parent_id, idempotency_key=idempotency_key)
        return run_api(args, state=state, command_path="category.create")

    @category.command("show")
    @click.argument("category_id")
    @output_options
    @pass_state
    def show_category(state, json_mode, no_color, category_id):
        args = common_args(state, json_mode, no_color, command="category", category_command="show", category_id=category_id)
        return run_api(args, state=state, command_path="category.show")

    @category.command("update")
    @click.argument("category_id")
    @click.option("--name")
    @click.option("--parent-id")
    @click.option("--icon")
    @click.option("--color")
    @click.option("--sort-order", type=int)
    @click.option("--status", type=click.Choice(["active", "hidden", "archived"]))
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def update_category(state, json_mode, no_color, category_id, **kwargs):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="category",
            category_command="update",
            category_id=category_id,
            **kwargs,
        )
        return run_api(args, state=state, command_path="category.update")


def _category_query_command(group: click.Group, command_name: str) -> None:
    @group.command(command_name)
    @click.option("--kind", type=click.Choice(["income", "expense"]), required=command_name == "find")
    @click.option("--name")
    @click.option("--path", "category_path")
    @click.option("--parent-id")
    @output_options
    @pass_state
    def query_category(state, json_mode, no_color, kind, name, category_path, parent_id):
        if command_name == "find" and not name and not category_path:
            raise click.UsageError("category find requires --name or --path")
        args = common_args(state, json_mode, no_color, command="category", category_command=command_name, kind=kind, name=name, path=category_path, parent_id=parent_id)
        return run_api(args, state=state, command_path=f"category.{command_name}")


def _register_credit_card(root: click.Group) -> None:
    @root.group("credit-card")
    def credit_card():
        """Manage credit card profiles."""

    @credit_card.command("list")
    @output_options
    @pass_state
    def list_cards(state, json_mode, no_color):
        args = common_args(state, json_mode, no_color, command="credit-card", credit_card_command="list")
        return run_api(args, state=state, command_path="credit_card.list")

    @credit_card.command("show")
    @click.argument("account_id")
    @output_options
    @pass_state
    def show_card(state, json_mode, no_color, account_id):
        args = common_args(state, json_mode, no_color, command="credit-card", credit_card_command="show", account_id=account_id)
        return run_api(args, state=state, command_path="credit_card.show")

    @credit_card.command("update")
    @click.argument("account_id")
    @click.option("--credit-limit")
    @click.option("--available-credit")
    @click.option("--statement-day", type=int)
    @click.option("--due-day", type=int)
    @click.option("--annual-fee")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def update_card(state, json_mode, no_color, account_id, **kwargs):
        args = common_args(state, json_mode, no_color, command="credit-card", credit_card_command="update", account_id=account_id, **kwargs)
        return run_api(args, state=state, command_path="credit_card.update")


def _register_account(root: click.Group) -> None:
    @root.group()
    def account():
        """Manage accounts."""

    _account_create_command(account)
    _account_query_command(account, "list")
    _account_query_command(account, "find")
    _account_show_update_balance(account)
    _account_create_command(root, name="account-create", command_value="account-create")


def _account_create_command(group: click.Group, name: str = "create", command_value: str = "account") -> None:
    @group.command(name)
    @click.argument("name")
    @click.option("--type", "account_type", default="asset")
    @click.option("--currency", default="CNY")
    @click.option("--opening-balance", default="0")
    @click.option("--institution-type")
    @click.option("--subtype")
    @click.option("--institution")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def create_account(state, json_mode, no_color, account_type, **kwargs):
        account_command = "create" if command_value == "account" else None
        args = common_args(
            state,
            json_mode,
            no_color,
            command=command_value,
            account_command=account_command,
            type=account_type,
            **kwargs,
        )
        return run_api(args, state=state, command_path="account.create")


def _account_query_command(group: click.Group, command_name: str) -> None:
    @group.command(command_name)
    @click.option("--name", required=command_name == "find")
    @click.option("--type", "account_type")
    @click.option("--currency")
    @click.option("--institution-type")
    @click.option("--subtype")
    @click.option("--institution")
    @output_options
    @pass_state
    def query_accounts(state, json_mode, no_color, account_type, **kwargs):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="account",
            account_command=command_name,
            type=account_type,
            **kwargs,
        )
        return run_api(args, state=state, command_path=f"account.{command_name}")


def _account_show_update_balance(group: click.Group) -> None:
    @group.command("show")
    @click.argument("account_id")
    @output_options
    @pass_state
    def show_account(state, json_mode, no_color, account_id):
        args = common_args(state, json_mode, no_color, command="account", account_command="show", account_id=account_id)
        return run_api(args, state=state, command_path="account.show")

    @group.command("update")
    @click.argument("account_id")
    @click.option("--institution-type")
    @click.option("--subtype")
    @click.option("--institution")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def update_account(state, json_mode, no_color, account_id, **kwargs):
        args = common_args(state, json_mode, no_color, command="account", account_command="update", account_id=account_id, **kwargs)
        return run_api(args, state=state, command_path="account.update")

    @group.command("balance")
    @click.argument("account_id")
    @click.option("--include-drafts", is_flag=True)
    @output_options
    @pass_state
    def account_balance(state, json_mode, no_color, account_id, include_drafts):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="account",
            account_command="balance",
            account_id=account_id,
            include_drafts=include_drafts,
        )
        return run_api(args, state=state, command_path="account.balance")

    @group.command("adjust")
    @click.argument("account_id")
    @click.option("--amount", required=True)
    @click.option("--purpose", required=True)
    @click.option("--memo", default="")
    @click.option("--occurred-at")
    @click.option("--currency", default="CNY")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def adjust_account(state, json_mode, no_color, account_id, **kwargs):
        args = common_args(state, json_mode, no_color, command="account", account_command="adjust", account_id=account_id, **kwargs)
        return run_api(args, state=state, command_path="account.adjust")
