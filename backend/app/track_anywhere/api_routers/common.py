from __future__ import annotations

from typing import Any

from fastapi import UploadFile
from pydantic import ValidationError as PydanticValidationError

from ..api_dependencies import SessionGuard
from ..attachments import MAX_ATTACHMENT_BYTES
from ..commands import StrictCommand
from ..errors import TrackAnywhereError, ValidationError


COMMAND_ERRORS = (TrackAnywhereError, PydanticValidationError)
protected = [SessionGuard]


def command_payload(command: StrictCommand) -> dict[str, Any]:
    return command.model_dump(mode="python")


async def read_upload_with_limit(file: UploadFile) -> bytes:
    content = bytearray()
    while chunk := await file.read(1024 * 1024):
        content.extend(chunk)
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise ValidationError("attachment exceeds size limit")
    return bytes(content)
