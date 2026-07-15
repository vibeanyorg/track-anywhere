from __future__ import annotations

from .commands import PUBLIC_COMMAND_PATHS
from .presenter_base import Presenter
from .presenter_operations import generic_payload_panel


PRESENTERS: dict[str, Presenter] = {
    command_path: generic_payload_panel(f"{command_path} response")
    for command_path in PUBLIC_COMMAND_PATHS
}


def presenter_for(command_path: str) -> Presenter:
    return PRESENTERS[command_path]
