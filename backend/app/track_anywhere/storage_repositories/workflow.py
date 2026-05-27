from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import delete, select

from ..errors import NotFound
from ..recurring import Recurrence, RecurringItem
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

    def list_items(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        book_id: str | None = None,
    ) -> list[RecurringItem]:
        statement = select(RecurringItemRecord)
        if book_id is not None:
            statement = statement.where(RecurringItemRecord.book_id == book_id)
        if status is not None:
            statement = statement.where(RecurringItemRecord.status == status)
        if kind is not None:
            statement = statement.where(RecurringItemRecord.kind == kind)
        items = [recurring_item_from_record(row) for row in self.session.scalars(statement)]
        return sorted(items, key=lambda item: (item.status, item.name, item.recurring_id))

    def get_item(self, recurring_id: str) -> RecurringItem:
        row = self.session.get(RecurringItemRecord, recurring_id)
        if row is None:
            raise NotFound(f"recurring item not found: {recurring_id}")
        return recurring_item_from_record(row)

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


def recurring_item_from_record(row: RecurringItemRecord) -> RecurringItem:
    return RecurringItem(
        recurring_id=row.recurring_id,
        name=row.name,
        kind=row.kind,
        status=row.status,
        book_id=row.book_id,
        amount=Decimal(row.amount) if row.amount is not None else None,
        currency=row.currency,
        provider=row.provider,
        reference=row.reference,
        recurrence=Recurrence(**row.recurrence),
        reminder_days=list(row.reminder_days),
        anchor_date=date.fromisoformat(row.anchor_date),
        source_account_id=row.source_account_id,
        category_id=row.category_id,
        last_draft_renewal_date=(
            date.fromisoformat(row.last_draft_renewal_date) if row.last_draft_renewal_date else None
        ),
        last_draft_id=row.last_draft_id,
        version=row.version,
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
