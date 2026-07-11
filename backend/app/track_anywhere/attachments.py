from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import socket
import struct
from typing import Protocol
from uuid import uuid4

from .errors import SecurityPreconditionFailed, ValidationError
from .security import DeploymentSecurityConfig


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
PDF_MAGIC = b"%PDF-"
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024


class AttachmentScanner(Protocol):
    def scan(self, content: bytes) -> None: ...


class ClamAVScanner:
    def __init__(self, host: str, port: int = 3310, *, timeout_seconds: float = 15.0) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds

    def scan(self, content: bytes) -> None:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout_seconds) as connection:
                connection.sendall(b"zINSTREAM\0")
                for offset in range(0, len(content), 64 * 1024):
                    chunk = content[offset : offset + 64 * 1024]
                    connection.sendall(struct.pack(">I", len(chunk)))
                    connection.sendall(chunk)
                connection.sendall(struct.pack(">I", 0))
                response = _read_clamav_response(connection)
        except OSError as exc:
            raise SecurityPreconditionFailed("attachment scanner unavailable") from exc
        if "FOUND" in response:
            raise SecurityPreconditionFailed("attachment malware scan rejected the upload")
        if not response.endswith("OK"):
            raise SecurityPreconditionFailed("attachment scanner returned an invalid result")


def _read_clamav_response(connection: socket.socket) -> str:
    chunks: list[bytes] = []
    while True:
        chunk = connection.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\0" in chunk:
            break
    return b"".join(chunks).rstrip(b"\0\n").decode("utf-8", errors="replace")


@dataclass
class Attachment:
    attachment_id: str
    storage_key: str
    content_hash: str
    mime_type: str
    original_filename: str
    scanner_status: str


class AttachmentIntake:
    def __init__(self, config: DeploymentSecurityConfig, scanner: AttachmentScanner | None = None) -> None:
        self.config = config
        self.scanner = scanner
        self.attachments: dict[str, Attachment] = {}

    def ingest(self, *, filename: str, mime_type: str, content: bytes) -> Attachment:
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
        if self.scanner is None:
            if self.config.mode != "local" or not self.config.local_dev_no_scan:
                raise SecurityPreconditionFailed("attachment scanner unavailable; intake is fail-closed")
            scanner_status = "skipped-local-dev"
        else:
            self.scanner.scan(content)
            scanner_status = "accepted"
        content_hash = sha256(content).hexdigest()
        attachment = Attachment(
            attachment_id=f"att_{uuid4().hex}",
            storage_key=f"{content_hash}{detected_ext}",
            content_hash=content_hash,
            mime_type=mime_type,
            original_filename=filename,
            scanner_status=scanner_status,
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
