from __future__ import annotations

import click

from .click_common import common_args, output_options, pass_state, run_api
from .command_credit_card import (
    request_charge,
    request_configure_card,
    request_fee,
    request_list_configured_cards,
    request_payment,
    request_refund,
)
from .command_definition import CommandDefinition


def register(root: click.Group) -> None:
    @root.group()
    def card() -> None:
        """Configure payment cards or use low-level credit-card operations."""

    _register_configure(card)
    _register_list_configured(card)
    _register_charge(card)
    _register_payment(card)
    _register_refund(card)
    _register_fee(card)


def _register_configure(card: click.Group) -> None:
    @card.command("configure")
    @click.argument("book_id")
    @click.option(
        "--request-id",
        type=click.UUID,
        help="UUID reused for an exact retry.",
    )
    @click.option("--name", required=True)
    @click.option(
        "--form-factor",
        type=click.Choice(("virtual", "physical", "single_use")),
        required=True,
    )
    @click.option(
        "--network",
        type=click.Choice(("mastercard", "visa", "amex", "unionpay", "other")),
        required=True,
    )
    @click.option("--provider-code", required=True)
    @click.option(
        "--settlement-policy",
        type=click.Choice(("immediate", "prepaid", "statement")),
        required=True,
        help=(
            "immediate/prepaid bind an asset account; statement binds a "
            "credit-card liability account."
        ),
    )
    @click.option("--settlement-account-id", required=True)
    @click.option("--asset-code", required=True)
    @click.option("--last4")
    @click.option("--effective-from")
    @output_options
    @pass_state
    def configure(state, json_mode, no_color, **values):
        """Configure how a physical or virtual card reaches the ledger."""
        args = common_args(
            state,
            json_mode,
            no_color,
            command="card",
            card_command="configure",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=request_configure_card.command_path,
        )


def _register_list_configured(card: click.Group) -> None:
    @card.command("list-configured")
    @click.argument("book_id")
    @click.option("--status", default="active", show_default=True)
    @click.option("--asset-code")
    @click.option("--name")
    @output_options
    @pass_state
    def list_configured(state, json_mode, no_color, **values):
        """List configured payment cards and their account bindings."""
        args = common_args(
            state,
            json_mode,
            no_color,
            command="card",
            card_command="list-configured",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=request_list_configured_cards.command_path,
        )


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


def _run_card(
    state,
    json_mode,
    no_color,
    definition: CommandDefinition,
    values: dict,
):
    args = common_args(
        state,
        json_mode,
        no_color,
        command="card",
        card_command=definition.command_path.removeprefix("card."),
        **values,
    )
    return run_api(args, state=state, command_path=definition.command_path)


def _register_charge(card: click.Group) -> None:
    @card.command("charge")
    @click.option("--expense-account-id", required=True)
    @_common_card_options
    @output_options
    @pass_state
    def charge(state, json_mode, no_color, **values):
        """Record a card purchase without exposing debit or credit sides."""
        return _run_card(state, json_mode, no_color, request_charge, values)


def _register_payment(card: click.Group) -> None:
    @card.command("payment")
    @click.option("--source-account-id", required=True)
    @_common_card_options
    @output_options
    @pass_state
    def payment(state, json_mode, no_color, **values):
        """Pay down a card from an asset account."""
        return _run_card(state, json_mode, no_color, request_payment, values)


def _register_refund(card: click.Group) -> None:
    @card.command("refund")
    @click.option("--original-transaction-id", required=True)
    @_common_card_options
    @output_options
    @pass_state
    def refund(state, json_mode, no_color, **values):
        """Refund part or all of an existing card charge."""
        return _run_card(state, json_mode, no_color, request_refund, values)


def _register_fee(card: click.Group) -> None:
    @card.command("fee")
    @click.option("--expense-account-id", required=True)
    @_common_card_options
    @output_options
    @pass_state
    def fee(state, json_mode, no_color, **values):
        """Record a card fee as a separate expense."""
        return _run_card(state, json_mode, no_color, request_fee, values)
