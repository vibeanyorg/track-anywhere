from __future__ import annotations

import click

from . import command_ledger as ledger_commands
from .click_common import common_args, output_options, pass_state, run_api


TRANSACTION_KINDS = (
    "standard",
    "opening",
    "adjustment",
    "transfer",
    "fx",
    "investment_cash",
)
REVERSAL_REASONS = (
    "user_correction",
    "duplicate",
    "import_correction",
    "provider_reversal",
)


def register(root: click.Group) -> None:
    @root.group()
    def tx() -> None:
        """Post and query V2 journal transactions."""

    _register_record(tx)
    _register_list(tx)
    _register_show(tx)
    _register_reverse(tx)
    _register_correct(tx)
    _register_correct_reference(tx)
    _register_fx(tx)
    _register_classification(tx)


def _register_record(tx: click.Group) -> None:
    @tx.command("record")
    @click.argument("book_id")
    @click.argument("transaction_id")
    @click.option("--command-id", required=True)
    @click.option("--expected-stream-version", type=int, required=True)
    @click.option("--kind", type=click.Choice(TRANSACTION_KINDS), required=True)
    @click.option("--effective-at", required=True)
    @click.option("--description-ref")
    @click.option(
        "--external-reference",
        multiple=True,
        metavar="PROVIDER:KIND:REFERENCE",
    )
    @click.option(
        "--posting",
        multiple=True,
        required=True,
        metavar="POSTING_ID:ACCOUNT_ID:ASSET:SIDE:AMOUNT",
        help="Repeat for each posting. AMOUNT is sent as the exact input string.",
    )
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def record(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="tx",
            tx_command="record",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=ledger_commands.request_record_transaction.command_path,
        )


def _register_list(tx: click.Group) -> None:
    @tx.command("list")
    @click.argument("book_id")
    @click.option("--limit", type=click.IntRange(1, 100), default=50)
    @click.option("--cursor")
    @click.option("--as-of-book-position", type=int)
    @output_options
    @pass_state
    def list_transactions(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="tx",
            tx_command="list",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=ledger_commands.request_list_transactions.command_path,
        )


def _register_show(tx: click.Group) -> None:
    @tx.command("show")
    @click.argument("book_id")
    @click.argument("transaction_id")
    @click.option("--as-of-book-position", type=int)
    @output_options
    @pass_state
    def show_transaction(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="tx",
            tx_command="show",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=ledger_commands.request_show_transaction.command_path,
        )


def _register_reverse(tx: click.Group) -> None:
    @tx.command("reverse")
    @click.argument("book_id")
    @click.argument("transaction_id")
    @click.option("--command-id", required=True)
    @click.option("--reversal-transaction-id", required=True)
    @click.option("--expected-stream-version", type=int, default=0)
    @click.option(
        "--reason-code",
        type=click.Choice(REVERSAL_REASONS),
        required=True,
    )
    @click.option("--effective-at", required=True)
    @click.option("--description-ref")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def reverse(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="tx",
            tx_command="reverse",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=ledger_commands.request_reverse_transaction.command_path,
        )


def _register_correct(tx: click.Group) -> None:
    @tx.command("correct")
    @click.argument("book_id")
    @click.argument("transaction_id")
    @click.option("--command-id", required=True)
    @click.option("--reversal-transaction-id", required=True)
    @click.option("--expected-reversal-stream-version", type=int, default=0)
    @click.option(
        "--reason-code",
        type=click.Choice(REVERSAL_REASONS),
        required=True,
    )
    @click.option("--reversal-effective-at", required=True)
    @click.option("--reversal-description-ref")
    @click.option("--replacement-transaction-id", required=True)
    @click.option("--replacement-expected-stream-version", type=int, default=0)
    @click.option(
        "--replacement-kind",
        type=click.Choice(TRANSACTION_KINDS),
        required=True,
    )
    @click.option("--replacement-effective-at", required=True)
    @click.option("--replacement-description-ref")
    @click.option(
        "--replacement-external-reference",
        multiple=True,
        metavar="PROVIDER:KIND:REFERENCE",
    )
    @click.option(
        "--replacement-posting",
        multiple=True,
        required=True,
        metavar="POSTING_ID:ACCOUNT_ID:ASSET:SIDE:AMOUNT",
    )
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def correct(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="tx",
            tx_command="correct",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=ledger_commands.request_correct_transaction.command_path,
        )


def _register_correct_reference(tx: click.Group) -> None:
    @tx.command("correct-reference")
    @click.argument("book_id")
    @click.argument("transaction_id")
    @click.option("--command-id", required=True)
    @click.option("--provider-code", required=True)
    @click.option(
        "--reference-kind",
        type=click.Choice(
            (
                "provider_transaction",
                "bank_transaction",
                "card_transaction",
                "broker_trade",
            )
        ),
        required=True,
    )
    @click.option("--corrected-reference", required=True)
    @click.option("--expected-stream-version", type=int, required=True)
    @click.option("--effective-at", required=True)
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def correct_reference(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="tx",
            tx_command="correct-reference",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=(
                ledger_commands.request_correct_external_reference.command_path
            ),
        )


def _register_fx(tx: click.Group) -> None:
    @tx.command("fx")
    @click.argument("book_id")
    @click.argument("transaction_id")
    @click.option("--command-id", required=True)
    @click.option("--expected-stream-version", type=int, required=True)
    @click.option("--source-account-id", required=True)
    @click.option("--source-trading-account-id", required=True)
    @click.option("--source-asset-code", required=True)
    @click.option("--source-amount", required=True)
    @click.option("--target-trading-account-id", required=True)
    @click.option("--target-account-id", required=True)
    @click.option("--target-asset-code", required=True)
    @click.option("--target-amount", required=True)
    @click.option("--effective-at", required=True)
    @click.option("--description-ref")
    @click.option(
        "--external-reference",
        multiple=True,
        metavar="PROVIDER:KIND:REFERENCE",
    )
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def fx(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="tx",
            tx_command="fx",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=ledger_commands.request_record_fx.command_path,
        )


def _register_classification(tx: click.Group) -> None:
    @tx.command("classify")
    @click.argument("book_id")
    @click.argument("transaction_id")
    @click.option("--command-id", required=True)
    @click.option("--expected-revision", type=int, required=True)
    @click.option("--effective-at", required=True)
    @click.option(
        "--line",
        multiple=True,
        required=True,
        metavar=(
            "LINE_ID:LINE_VERSION_ID:CATALOG_ID:ASSET:UNITS:"
            "LINE_KIND:DIMENSION[:DIMENSION_ID[:DESCRIPTION_REF]]"
        ),
    )
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def classify(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="tx",
            tx_command="classify",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=ledger_commands.request_assign_reporting_lines.command_path,
        )

    @tx.command("clear-classification")
    @click.argument("book_id")
    @click.argument("transaction_id")
    @click.option("--command-id", required=True)
    @click.option("--expected-revision", type=int, required=True)
    @click.option("--effective-at", required=True)
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def clear_classification(state, json_mode, no_color, **values):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="tx",
            tx_command="clear-classification",
            **values,
        )
        return run_api(
            args,
            state=state,
            command_path=ledger_commands.request_clear_reporting_lines.command_path,
        )
