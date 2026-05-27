from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import delete

from ..storage_json import to_jsonable
from ..storage_models import AttachmentRecord, DraftPostingRecord, DraftRecord, RecurringItemRecord


class DraftRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def save(self, drafts: Iterable[Any]) -> None:
        for draft in drafts:
            self.session.merge(
                DraftRecord(
                    draft_id=draft.draft_id,
                    memo=draft.memo,
                    state=draft.state,
                    book_id=draft.book_id,
                    missing_fields=list(draft.missing_fields),
                    source=draft.source,
                    confidence=draft.confidence,
                    version=draft.version,
                    attachment_id=draft.attachment_id,
                    category_id=draft.category_id,
                    metadata_json=to_jsonable(draft.metadata or {}),
                )
            )
            self._replace_draft_postings(draft)

    def _replace_draft_postings(self, draft) -> None:
        self.session.execute(delete(DraftPostingRecord).where(DraftPostingRecord.draft_id == draft.draft_id))
        for index, posting in enumerate(draft.proposed_postings):
            self.session.add(
                DraftPostingRecord(
                    draft_id=draft.draft_id,
                    position=index,
                    account_id=posting.account_id,
                    amount=str(posting.amount),
                    currency=posting.currency,
                )
            )


class RecurringRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def save_items(self, items: Iterable[Any]) -> None:
        for item in items:
            recurrence = {"type": item.recurrence.type, "day": item.recurrence.day}
            if item.recurrence.month is not None:
                recurrence["month"] = item.recurrence.month
            self.session.merge(
                RecurringItemRecord(
                    recurring_id=item.recurring_id,
                    name=item.name,
                    kind=item.kind,
                    status=item.status,
                    book_id=item.book_id,
                    amount=str(item.amount) if item.amount is not None else None,
                    currency=item.currency,
                    provider=item.provider,
                    reference=item.reference,
                    recurrence=recurrence,
                    reminder_days=list(item.reminder_days),
                    anchor_date=item.anchor_date.isoformat(),
                    source_account_id=item.source_account_id,
                    category_id=item.category_id,
                    last_draft_renewal_date=(
                        item.last_draft_renewal_date.isoformat() if item.last_draft_renewal_date else None
                    ),
                    last_draft_id=item.last_draft_id,
                    version=item.version,
                )
            )


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
