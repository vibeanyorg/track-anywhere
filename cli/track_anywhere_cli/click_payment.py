from __future__ import annotations

import click

from .click_common import common_args, output_options, pass_state, run_api


def register(root: click.Group) -> None:
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
