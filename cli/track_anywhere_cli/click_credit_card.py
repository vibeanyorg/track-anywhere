from __future__ import annotations

import click

from .click_common import common_args, output_options, pass_state, run_api


def register(root: click.Group) -> None:
    @root.group()
    def card() -> None:
        """Record credit-card charges, payments, refunds, and fees."""

    _register_charge(card)
    _register_payment(card)
    _register_refund(card)
    _register_fee(card)


def _common_card_options(function):
    decorators = (
        click.argument("book_id"),
        click.argument("transaction_id"),
        click.option("--command-id", required=True),
        click.option(
            "--expected-stream-version", type=int, default=0, show_default=True
        ),
        click.option("--card-account-id", required=True),
        click.option("--asset-code", required=True),
        click.option(
            "--amount",
            required=True,
            help="Positive amount sent as the exact input string.",
        ),
        click.option("--effective-at", required=True),
        click.option("--description-ref"),
        click.option(
            "--external-reference",
            multiple=True,
            metavar="PROVIDER:KIND:REFERENCE",
        ),
        click.option("--idempotency-key"),
    )
    for decorator in reversed(decorators):
        function = decorator(function)
    return function


def _run_card(state, json_mode, no_color, command_path: str, values: dict):
    args = common_args(
        state,
        json_mode,
        no_color,
        command="card",
        card_command=command_path.removeprefix("card."),
        **values,
    )
    return run_api(args, state=state, command_path=command_path)


def _register_charge(card: click.Group) -> None:
    @card.command("charge")
    @click.option("--expense-account-id", required=True)
    @_common_card_options
    @output_options
    @pass_state
    def charge(state, json_mode, no_color, **values):
        """Record a card purchase without exposing debit or credit sides."""
        return _run_card(state, json_mode, no_color, "card.charge", values)


def _register_payment(card: click.Group) -> None:
    @card.command("payment")
    @click.option("--source-account-id", required=True)
    @_common_card_options
    @output_options
    @pass_state
    def payment(state, json_mode, no_color, **values):
        """Pay down a card from an asset account."""
        return _run_card(state, json_mode, no_color, "card.payment", values)


def _register_refund(card: click.Group) -> None:
    @card.command("refund")
    @click.option("--original-transaction-id", required=True)
    @_common_card_options
    @output_options
    @pass_state
    def refund(state, json_mode, no_color, **values):
        """Refund part or all of an existing card charge."""
        return _run_card(state, json_mode, no_color, "card.refund", values)


def _register_fee(card: click.Group) -> None:
    @card.command("fee")
    @click.option("--expense-account-id", required=True)
    @_common_card_options
    @output_options
    @pass_state
    def fee(state, json_mode, no_color, **values):
        """Record a card fee as a separate expense."""
        return _run_card(state, json_mode, no_color, "card.fee", values)
