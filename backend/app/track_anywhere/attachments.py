from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from .errors import SecurityPreconditionFailed, ValidationError
from .security import DeploymentSecurityConfig


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
PDF_MAGIC = b"%PDF-"
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024


@dataclass
class Attachment:
    attachment_id: str
    storage_key: str
    content_hash: str
    mime_type: str
    original_filename: str
    scanner_status: str


class AttachmentIntake:
    def __init__(self, config: DeploymentSecurityConfig) -> None:
        self.config = config
        self.attachments: dict[str, Attachment] = {}

    def ingest(self, *, filename: str, mime_type: str, content: bytes, scanner_available: bool) -> Attachment:
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise ValidationError("attachment exceeds size limit")
        detected_ext = self._detect_extension(content)
        allowed = {
            ("image/png", ".png"),
            ("image/jpeg", ".jpg"),
            ("image/jpeg", ".jpeg"),
        }
        if (mime_type, detected_ext) not in allowed:
            raise ValidationError("unsupported or mismatched attachment type")
        if Path(filename).name != filename:
            raise ValidationError("attachment filename must not contain path separators")
        if not scanner_available:
            if self.config.mode != "local" or not self.config.local_dev_no_scan:
                raise SecurityPreconditionFailed("scanner unavailable; attachment intake is fail-closed")
        content_hash = sha256(content).hexdigest()
        attachment = Attachment(
            attachment_id=f"att_{uuid4().hex}",
            storage_key=f"{content_hash}{detected_ext}",
            content_hash=content_hash,
            mime_type=mime_type,
            original_filename=filename,
            scanner_status="skipped-local-dev" if not scanner_available else "accepted",
        )
        self.attachments[attachment.attachment_id] = attachment
        return attachment

    @staticmethod
    def _detect_extension(content: bytes) -> str:
        if content.startswith(PNG_MAGIC):
            return ".png"
        if content.startswith(JPEG_MAGIC):
            return ".jpg"
        if content.startswith(PDF_MAGIC):
            return ".pdf"
        raise ValidationError("unknown attachment signature")

