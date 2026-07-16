from __future__ import annotations

import click

from . import command_archive as archive_commands
from .click_common import common_args, output_options, pass_state, run_api


def register(root: click.Group) -> None:
    @root.group()
    def archive() -> None:
        """Inspect protected import archives as the Book owner."""

    @archive.command("list")
    @click.argument("book_id")
    @output_options
    @pass_state
    def list_archives(state, json_mode, no_color, book_id):
        """List protected import archive metadata for a Book."""
        args = common_args(
            state,
            json_mode,
            no_color,
            command="archive",
            archive_command="list",
            book_id=book_id,
        )
        return run_api(
            args,
            state=state,
            command_path=(
                archive_commands.request_list_import_archives.command_path
            ),
        )

    @archive.command("export")
    @click.argument("book_id")
    @click.argument("archive_id")
    @output_options
    @pass_state
    def export_archive(state, json_mode, no_color, book_id, archive_id):
        """Explicitly decrypt one protected archive as the Book owner."""
        args = common_args(
            state,
            json_mode,
            no_color,
            command="archive",
            archive_command="export",
            book_id=book_id,
            archive_id=archive_id,
        )
        return run_api(
            args,
            state=state,
            command_path=(
                archive_commands.request_export_import_archive.command_path
            ),
        )


__all__ = ["register"]
