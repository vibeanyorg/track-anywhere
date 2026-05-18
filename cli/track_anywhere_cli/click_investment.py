from __future__ import annotations

import click

from .click_common import common_args, output_options, pass_state, run_api


def register(root: click.Group) -> None:
    @root.group()
    def investment():
        """Manage investment events and reports."""

    @investment.command("event")
    @click.argument("account_id")
    @click.option("--type", "event_type", type=click.Choice(["buy", "add", "sell", "income"]), required=True)
    @click.option("--amount", required=True)
    @click.option("--currency", default="CNY")
    @click.option("--occurred-at")
    @click.option("--memo")
    @click.option("--units")
    @click.option("--nav")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def event(state, json_mode, no_color, account_id, **kwargs):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="investment",
            investment_command="event",
            account_id=account_id,
            **kwargs,
        )
        return run_api(args, state=state, command_path="investment.event")

    @investment.command("performance")
    @click.argument("account_id")
    @click.option("--as-of")
    @output_options
    @pass_state
    def performance(state, json_mode, no_color, account_id, as_of):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="investment",
            investment_command="performance",
            account_id=account_id,
            as_of=as_of,
        )
        return run_api(args, state=state, command_path="investment.performance")
