from __future__ import annotations

from argparse import Namespace
from typing import Any
from urllib.parse import quote

from .command_definition import CommandDefinition, Requester, api_command
from .config import CliConfig


@api_command("archive.list")
def request_list_import_archives(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(
        config,
        "GET",
        f"/api/v2/books/{_path(args.book_id)}/import-archives",
        None,
        None,
    )


@api_command("archive.export")
def request_export_import_archive(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(
        config,
        "GET",
        (
            f"/api/v2/books/{_path(args.book_id)}/import-archives/"
            f"{_path(args.archive_id)}/export"
        ),
        None,
        None,
    )


def _path(value: object) -> str:
    return quote(str(value), safe="")


ARCHIVE_COMMAND_DEFINITIONS: tuple[CommandDefinition, ...] = (
    request_list_import_archives,
    request_export_import_archive,
)


__all__ = ["ARCHIVE_COMMAND_DEFINITIONS"]
