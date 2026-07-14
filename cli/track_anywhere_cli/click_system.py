from __future__ import annotations

import click

from .click_common import common_args, output_options, pass_state, run_api


def register(root: click.Group) -> None:
    @root.group()
    def system() -> None:
        """Inspect V2 runtime health and readiness."""

    @system.command("health")
    @output_options
    @pass_state
    def health(state, json_mode, no_color):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="system",
            system_command="health",
        )
        return run_api(args, state=state, command_path="system.health")

    @system.command("ready")
    @output_options
    @pass_state
    def ready(state, json_mode, no_color):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="system",
            system_command="ready",
        )
        return run_api(args, state=state, command_path="system.ready")
