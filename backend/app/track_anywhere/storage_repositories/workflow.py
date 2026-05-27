from __future__ import annotations

from typing import Any, Iterable

from ..storage_models import AttachmentRecord


class DraftRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, drafts: Iterable[Any]) -> None:
        self.storage._save_drafts(self.session, drafts)


class RecurringRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_items(self, items: Iterable[Any]) -> None:
        self.storage._save_recurring_items(self.session, items)


class AttachmentRepository:
    def __init__(self, session) -> None:
        self.session = session

    def save(self, attachments: Iterable[Any]) -> None:
        for attachment in attachments:
            self.session.merge(
                AttachmentRecord(
                    attachment_id=attachment.attachment_id,
                    storage_key=attachment.storage_key,
                    content_hash=attachment.content_hash,
                    mime_type=attachment.mime_type,
                    original_filename=attachment.original_filename,
                    scanner_status=attachment.scanner_status,
                )
            )
