from __future__ import annotations

import click

from .click_common import common_args, output_options, pass_state, run_api


def register(root: click.Group) -> None:
    _capture(root)
    _draft_confirm(root)
    _transactions(root)
    _expense_income(root)
    _payments(root)
    _balances(root)


def _capture(root: click.Group) -> None:
    @root.command("capture")
    @click.argument("memo")
    @click.option("--amount")
    @click.option("--source-account-id")
    @click.option("--expense-account-id")
    @click.option("--currency", default="CNY")
    @click.option("--idempotency-key")
    @click.option("--dry-run", is_flag=True)
    @output_options
    @pass_state
    def capture(state, json_mode, no_color, **kwargs):
        if kwargs["dry_run"]:
            payload = {
                "memo": kwargs["memo"],
                "amount": kwargs["amount"],
                "currency": kwargs["currency"],
                "source_account_id": kwargs["source_account_id"],
                "expense_account_id": kwargs["expense_account_id"],
            }
            from .renderers import emit_result

            emit_result(
                {"dry_run": True, "policy_decision": "would_create_draft", "payload": payload},
                json_mode=state.json_mode or json_mode,
                no_color=state.no_color or no_color,
                command_path="capture",
            )
            return 0
        args = common_args(state, json_mode, no_color, command="capture", **kwargs)
        return run_api(args, state=state, command_path="capture")


def _draft_confirm(root: click.Group) -> None:
    @root.command("draft-confirm")
    @click.argument("draft_id")
    @click.option("--expected-version", type=int, required=True)
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def draft_confirm(state, json_mode, no_color, draft_id, expected_version, idempotency_key):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="draft-confirm",
            draft_id=draft_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )
        return run_api(args, state=state, command_path="draft.confirm")


def _transactions(root: click.Group) -> None:
    @root.group()
    def tx():
        """Manage transactions."""

    _record_command(tx, command="tx", tx_command="record")

    @tx.command("list")
    @click.option("--account-id")
    @click.option("--category-id")
    @click.option("--limit", type=int, default=20)
    @output_options
    @pass_state
    def list_tx(state, json_mode, no_color, account_id, category_id, limit):
        args = common_args(state, json_mode, no_color, command="tx", tx_command="list", account_id=account_id, category_id=category_id, limit=limit)
        return run_api(args, state=state, command_path="tx.list")

    @tx.command("show")
    @click.argument("transaction_id")
    @output_options
    @pass_state
    def show_tx(state, json_mode, no_color, transaction_id):
        args = common_args(state, json_mode, no_color, command="tx", tx_command="show", transaction_id=transaction_id)
        return run_api(args, state=state, command_path="tx.show")

    @tx.command("snapshot")
    @click.argument("transaction_id")
    @click.option("--output", type=click.Path(dir_okay=False))
    @output_options
    @pass_state
    def snapshot_tx(state, json_mode, no_color, transaction_id, output):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="tx",
            tx_command="snapshot",
            transaction_id=transaction_id,
            output=output,
        )
        return run_api(args, state=state, command_path="tx.snapshot")

    @tx.command("reverse")
    @click.argument("transaction_id")
    @click.option("--memo", required=True)
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def reverse_tx(state, json_mode, no_color, transaction_id, memo, idempotency_key):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="tx",
            tx_command="reverse",
            transaction_id=transaction_id,
            memo=memo,
            idempotency_key=idempotency_key,
        )
        return run_api(args, state=state, command_path="tx.reverse")

    @tx.command("reclassify")
    @click.argument("transaction_id")
    @click.option("--category-id", required=True)
    @click.option("--line-id")
    @click.option("--memo", default="")
    @click.option("--backup-before", is_flag=True)
    @click.option("--backup-dir", type=click.Path(file_okay=False))
    @click.option("--backup-label")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def reclassify_tx(
        state,
        json_mode,
        no_color,
        transaction_id,
        category_id,
        line_id,
        memo,
        backup_before,
        backup_dir,
        backup_label,
        idempotency_key,
    ):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="tx",
            tx_command="reclassify",
            transaction_id=transaction_id,
            category_id=category_id,
            line_id=line_id,
            memo=memo,
            backup_before=backup_before,
            backup_dir=backup_dir,
            backup_label=backup_label,
            idempotency_key=idempotency_key,
        )
        return run_api(args, state=state, command_path="tx.reclassify")

    _record_command(root, name="record", command="record")


def _record_command(group: click.Group, name: str = "record", command: str = "tx", tx_command: str | None = None) -> None:
    @group.command(name)
    @click.option("--amount", required=True)
    @click.option("--from-account-id", "--from", "from_account_id", required=True)
    @click.option("--to-account-id", "--to", "to_account_id", required=True)
    @click.option("--purpose", required=True)
    @click.option("--memo", default="")
    @click.option("--occurred-at")
    @click.option("--currency", default="CNY")
    @click.option("--category-id")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def record_tx(state, json_mode, no_color, **kwargs):
        args = common_args(state, json_mode, no_color, command=command, tx_command=tx_command, **kwargs)
        return run_api(args, state=state, command_path="tx.record")


def _expense_income(root: click.Group) -> None:
    _expense_group(root)
    _category_money_group(root, "income", "to_account_id", ["--to-account-id", "--to"])


def _expense_group(root: click.Group) -> None:
    @root.group("expense")
    def group():
        pass

    @group.command("record")
    @click.option("--amount", required=True)
    @click.option("--from-account-id", "--from", "from_account_id")
    @click.option("--payment")
    @click.option("--category-id", required=True)
    @click.option("--purpose", required=True)
    @click.option("--memo", default="")
    @click.option("--occurred-at")
    @click.option("--currency", default="CNY")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def record_expense(state, json_mode, no_color, **kwargs):
        if bool(kwargs.get("payment")) == bool(kwargs.get("from_account_id")):
            raise click.UsageError("expense record requires exactly one of --payment or --from-account-id")
        args = common_args(state, json_mode, no_color, command="expense", expense_command="record", **kwargs)
        return run_api(args, state=state, command_path="expense.record")


def _category_money_group(root: click.Group, group_name: str, account_dest: str, aliases: list[str]) -> None:
    @root.group(group_name)
    def group():
        pass

    @group.command("record")
    @click.option("--amount", required=True)
    @click.option(*aliases, account_dest, required=True)
    @click.option("--category-id", required=True)
    @click.option("--purpose", required=True)
    @click.option("--memo", default="")
    @click.option("--occurred-at")
    @click.option("--currency", default="CNY")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def record_category_money(state, json_mode, no_color, **kwargs):
        args = common_args(state, json_mode, no_color, command=group_name, **{f"{group_name}_command": "record"}, **kwargs)
        return run_api(args, state=state, command_path=f"{group_name}.record")


def _payments(root: click.Group) -> None:
    @root.group()
    def payment():
        """Manage payment profiles."""

    @payment.group("profile")
    def profile():
        """Configure payment aliases."""

    @profile.command("create")
    @click.argument("slug")
    @click.option("--display-name", required=True)
    @click.option("--kind", default="token-backed-card", show_default=True)
    @click.option("--instrument-account-id", required=True)
    @click.option("--backing-account-id", required=True)
    @click.option("--settlement-mode", default="immediate", show_default=True)
    @click.option("--settlement-rate", default="1", show_default=True)
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def create_payment_profile(state, json_mode, no_color, slug, **kwargs):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="payment",
            payment_command="profile",
            profile_command="create",
            slug=slug,
            **kwargs,
        )
        return run_api(args, state=state, command_path="payment.profile.create")

    @profile.command("list")
    @click.option("--status", default="active")
    @output_options
    @pass_state
    def list_payment_profiles(state, json_mode, no_color, status):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="payment",
            payment_command="profile",
            profile_command="list",
            status=status,
        )
        return run_api(args, state=state, command_path="payment.profile.list")

    @profile.command("status")
    @click.argument("payment")
    @output_options
    @pass_state
    def payment_profile_status(state, json_mode, no_color, payment):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="payment",
            payment_command="profile",
            profile_command="status",
            payment=payment,
        )
        return run_api(args, state=state, command_path="payment.profile.status")


def _balances(root: click.Group) -> None:
    @root.command("balance-adjust")
    @click.argument("account_id")
    @click.option("--amount", required=True)
    @click.option("--purpose", required=True)
    @click.option("--memo", default="")
    @click.option("--occurred-at")
    @click.option("--currency", default="CNY")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def balance_adjust(state, json_mode, no_color, account_id, **kwargs):
        args = common_args(state, json_mode, no_color, command="balance-adjust", account_id=account_id, **kwargs)
        return run_api(args, state=state, command_path="balance.adjust")

    @root.command("balance")
    @click.argument("account_id")
    @click.option("--include-drafts", is_flag=True)
    @output_options
    @pass_state
    def balance(state, json_mode, no_color, account_id, include_drafts):
        args = common_args(state, json_mode, no_color, command="balance", account_id=account_id, include_drafts=include_drafts)
        return run_api(args, state=state, command_path="balance")
