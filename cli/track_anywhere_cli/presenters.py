from __future__ import annotations

from .commands import PUBLIC_COMMAND_PATHS
from .presenter_base import Presenter
from .presenter_operations import generic_payload_panel


_LOCAL_UNSUPPORTED_PATHS = {
    "auth.dev_token",
    "data.backup",
    "payment.instrument.create",
    "payment.instrument.list",
    "payment.instrument.show",
    "payment.profile.create",
    "payment.profile.list",
    "payment.profile.status",
    "recurring.create",
    "recurring.draft_due",
    "recurring.list",
    "recurring.reminders",
    "recurring.show",
    "recurring.update",
}


PRESENTERS: dict[str, Presenter] = {
    command_path: generic_payload_panel(f"{command_path} response")
    for command_path in (*PUBLIC_COMMAND_PATHS, *_LOCAL_UNSUPPORTED_PATHS)
}


def presenter_for(command_path: str) -> Presenter:
    return PRESENTERS[command_path]
