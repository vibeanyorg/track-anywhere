from __future__ import annotations

import click

from .click_common import common_args, output_options, pass_state, run_api


def register(root: click.Group) -> None:
    @root.group()
    def counterparty():
        """Manage transaction counterparties."""

    @counterparty.command("ensure")
    @click.argument("name")
    @click.option("--kind", default="merchant", show_default=True)
    @click.option("--slug")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def ensure_counterparty(state, json_mode, no_color, name, kind, slug, idempotency_key):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="counterparty",
            counterparty_command="ensure",
            name=name,
            kind=kind,
            slug=slug,
            idempotency_key=idempotency_key,
        )
        return run_api(args, state=state, command_path="counterparty.ensure")

    @counterparty.command("create")
    @click.argument("name")
    @click.option("--kind", default="merchant", show_default=True)
    @click.option("--slug")
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def create_counterparty(state, json_mode, no_color, name, kind, slug, idempotency_key):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="counterparty",
            counterparty_command="create",
            name=name,
            kind=kind,
            slug=slug,
            idempotency_key=idempotency_key,
        )
        return run_api(args, state=state, command_path="counterparty.create")

    @counterparty.command("list")
    @click.option("--kind")
    @click.option("--status", default="active")
    @click.option("--name")
    @output_options
    @pass_state
    def list_counterparties(state, json_mode, no_color, kind, status, name):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="counterparty",
            counterparty_command="list",
            kind=kind,
            status=status,
            name=name,
        )
        return run_api(args, state=state, command_path="counterparty.list")

    @counterparty.command("show")
    @click.argument("counterparty_ref")
    @output_options
    @pass_state
    def show_counterparty(state, json_mode, no_color, counterparty_ref):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="counterparty",
            counterparty_command="show",
            counterparty_ref=counterparty_ref,
        )
        return run_api(args, state=state, command_path="counterparty.show")
