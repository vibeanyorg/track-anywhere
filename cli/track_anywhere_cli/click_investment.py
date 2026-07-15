from __future__ import annotations

import click

from .click_common import common_args, output_options, pass_state, run_api
from .command_investment import request_acquire_lot, request_dispose_lot


def register(root: click.Group) -> None:
    @root.group()
    def investment() -> None:
        """Acquire and dispose V2 investment lots."""

    @investment.command("acquire")
    @click.argument("book_id")
    @click.argument("transaction_id")
    @click.argument("lot_id")
    @click.option("--command-id", required=True)
    @click.option("--instrument-asset-code", required=True)
    @click.option("--settlement-asset-code", required=True)
    @click.option("--quantity-units", required=True)
    @click.option("--cost-units", required=True)
    @click.option("--effective-at", required=True)
    @click.option("--fee-units")
    @click.option("--expected-stream-version", type=int, default=0)
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def acquire(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="investment",
            investment_command="acquire",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=request_acquire_lot.command_path,
        )

    @investment.command("dispose")
    @click.argument("book_id")
    @click.argument("transaction_id")
    @click.option("--command-id", required=True)
    @click.option("--instrument-asset-code", required=True)
    @click.option("--settlement-asset-code", required=True)
    @click.option("--quantity-units", required=True)
    @click.option("--proceeds-units", required=True)
    @click.option(
        "--allocation-method",
        type=click.Choice(("fifo", "specific_id")),
        required=True,
    )
    @click.option("--effective-at", required=True)
    @click.option("--fee-units")
    @click.option(
        "--specific-lot",
        multiple=True,
        metavar="LOT_ID:QUANTITY_UNITS",
    )
    @click.option("--expected-stream-version", type=int, default=0)
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def dispose(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="investment",
            investment_command="dispose",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=request_dispose_lot.command_path,
        )
