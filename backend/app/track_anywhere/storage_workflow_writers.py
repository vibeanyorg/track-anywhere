from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .drafts import DraftTransaction
from .storage_json import to_jsonable
from .storage_models import DraftPostingRecord, DraftRecord, RecurringItemRecord


class WorkflowStorageWriters:
    def _replace_draft_postings(self, session: Session, draft: DraftTransaction) -> None:
        session.execute(delete(DraftPostingRecord).where(DraftPostingRecord.draft_id == draft.draft_id))
        for index, posting in enumerate(draft.proposed_postings):
            session.add(
                DraftPostingRecord(
                    draft_id=draft.draft_id,
                    position=index,
                    account_id=posting.account_id,
                    amount=str(posting.amount),
                    currency=posting.currency,
                )
            )

    def _save_drafts(self, session: Session, drafts) -> None:
        for draft in drafts:
            session.merge(
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
            self._replace_draft_postings(session, draft)

    def _save_recurring_items(self, session: Session, items) -> None:
        for item in items:
            recurrence = {"type": item.recurrence.type, "day": item.recurrence.day}
            if item.recurrence.month is not None:
                recurrence["month"] = item.recurrence.month
            session.merge(
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
