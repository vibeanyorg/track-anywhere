from __future__ import annotations

import click

from .click_common import common_args, output_options, pass_state, run_api


def register(root: click.Group) -> None:
    @root.group()
    def system():
        """Inspect the running API service."""

    @system.command("status")
    @click.option("--include-counts", is_flag=True)
    @output_options
    @pass_state
    def system_status(state, json_mode, no_color, include_counts):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="system",
            system_command="status",
            include_counts=include_counts,
        )
        return run_api(args, state=state, command_path="system.status")
