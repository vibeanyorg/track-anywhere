from __future__ import annotations

import click

from .click_common import common_args, output_options, pass_state, run_api


def register(root: click.Group) -> None:
    @root.group()
    def recurring():
        """Manage recurring subscriptions and renewal reminders."""

    @recurring.command("create")
    @click.option("--name", required=True)
    @click.option("--kind", type=click.Choice(["paid", "reminder_only"]), required=True)
    @click.option("--amount")
    @click.option("--currency")
    @click.option("--provider")
    @click.option("--reference")
    @click.option("--monthly-day", type=int)
    @click.option("--yearly-date")
    @click.option("--anchor-date", required=True)
    @click.option("--remind", "reminder_days", type=int, multiple=True, required=True)
    @click.option("--source-account-id")
    @click.option("--category-id")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def create(state, json_mode, no_color, **kwargs):
        if (kwargs["monthly_day"] is None) == (kwargs["yearly_date"] is None):
            raise click.UsageError("provide exactly one of --monthly-day or --yearly-date")
        args = common_args(
            state,
            json_mode,
            no_color,
            command="recurring",
            recurring_command="create",
            reminder_days=list(kwargs.pop("reminder_days")),
            **kwargs,
        )
        return run_api(args, state=state, command_path="recurring.create")

    @recurring.command("list")
    @click.option("--status")
    @click.option("--kind", type=click.Choice(["paid", "reminder_only"]))
    @output_options
    @pass_state
    def list_items(state, json_mode, no_color, status, kind):
        args = common_args(state, json_mode, no_color, command="recurring", recurring_command="list", status=status, kind=kind)
        return run_api(args, state=state, command_path="recurring.list")

    @recurring.command("show")
    @click.argument("recurring_id")
    @output_options
    @pass_state
    def show(state, json_mode, no_color, recurring_id):
        args = common_args(state, json_mode, no_color, command="recurring", recurring_command="show", recurring_id=recurring_id)
        return run_api(args, state=state, command_path="recurring.show")

    @recurring.command("update")
    @click.argument("recurring_id")
    @click.option("--status", type=click.Choice(["active", "paused", "cancelled"]))
    @click.option("--remind", "reminder_days", type=int, multiple=True)
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def update(state, json_mode, no_color, recurring_id, status, reminder_days, idempotency_key):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="recurring",
            recurring_command="update",
            recurring_id=recurring_id,
            status=status,
            reminder_days=list(reminder_days),
            idempotency_key=idempotency_key,
        )
        return run_api(args, state=state, command_path="recurring.update")

    @recurring.command("reminders")
    @click.option("--as-of")
    @click.option("--window-days", type=int, default=0)
    @output_options
    @pass_state
    def reminders(state, json_mode, no_color, as_of, window_days):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="recurring",
            recurring_command="reminders",
            as_of=as_of,
            window_days=window_days,
        )
        return run_api(args, state=state, command_path="recurring.reminders")

    @recurring.command("draft-due")
    @click.option("--as-of")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def draft_due(state, json_mode, no_color, as_of, idempotency_key):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="recurring",
            recurring_command="draft-due",
            as_of=as_of,
            idempotency_key=idempotency_key,
        )
        return run_api(args, state=state, command_path="recurring.draft_due")
