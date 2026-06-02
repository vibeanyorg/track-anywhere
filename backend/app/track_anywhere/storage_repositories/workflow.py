from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import delete, select

from ..accounting import (
    PostingSide,
    debit_credit_balanced,
    legacy_signed_amount_to_debit_credit,
    STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING,
    storage_posting_amount_semantics,
    validate_posting_semantic_shape,
)
from ..drafts import DraftTransaction
from ..errors import NotFound, ValidationError
from ..ledger import Posting
from ..recurring import Recurrence, RecurringItem
from ..storage_json import to_jsonable
from ..storage_models import AccountRecord, AttachmentRecord, DraftPostingRecord, DraftRecord, RecurringItemRecord


class DraftRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def get_draft(self, draft_id: str) -> DraftTransaction | None:
        row = self.session.get(DraftRecord, draft_id)
        if row is None:
            return None
        postings = [
            Posting(
                posting.account_id,
                Decimal(posting.amount),
                posting.currency,
                side=getattr(posting, "side", None),
                amount_semantics=storage_posting_amount_semantics(
                    getattr(posting, "amount_semantics", STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING)
                ),
            )
            for posting in self.session.scalars(
                select(DraftPostingRecord)
                .where(DraftPostingRecord.draft_id == draft_id)
                .order_by(DraftPostingRecord.position, DraftPostingRecord.id)
            )
        ]
        return draft_from_record(row, postings)

    def save(self, drafts: Iterable[Any], *, allow_legacy_signed: bool = False) -> None:
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
            self._replace_draft_postings(draft, allow_legacy_signed=allow_legacy_signed)

    def _replace_draft_postings(self, draft, *, allow_legacy_signed: bool = False) -> None:
        _validate_new_draft_postings(draft.proposed_postings, allow_legacy_signed=allow_legacy_signed)
        self.session.execute(delete(DraftPostingRecord).where(DraftPostingRecord.draft_id == draft.draft_id))
        for index, posting in enumerate(draft.proposed_postings):
            self.session.add(
                DraftPostingRecord(
                    draft_id=draft.draft_id,
                    position=index,
                    account_id=posting.account_id,
                    side=_storage_side_for_posting(self.session, posting),
                    amount_semantics=posting.amount_semantics,
                    amount=str(posting.amount),
                    currency=posting.currency,
                )
            )


def _validate_new_draft_postings(postings: Iterable[Posting], *, allow_legacy_signed: bool) -> None:
        semantics: set[str] = set()
        legacy_totals: dict[str, Decimal] = {}
        debit_credit_totals: dict[str, dict[PostingSide, Decimal]] = {}
        for posting in postings:
            _validate_new_draft_posting(posting, allow_legacy_signed=allow_legacy_signed)
            semantics.add(posting.amount_semantics)
            if posting.amount_semantics == "debit_credit":
                if posting.side is None:
                    raise ValidationError("debit/credit posting requires side")
                side_totals = debit_credit_totals.setdefault(
                    posting.currency,
                    {"debit": Decimal("0"), "credit": Decimal("0")},
                )
                side_totals[posting.side] += posting.amount
            if posting.amount_semantics == "legacy_signed":
                legacy_totals[posting.currency] = legacy_totals.get(posting.currency, Decimal("0")) + posting.amount
        if len(semantics) > 1:
            raise ValidationError("draft postings must not mix legacy signed and debit/credit semantics")
        if any(total != Decimal("0") for total in legacy_totals.values()):
            raise ValidationError("legacy signed draft postings must balance by currency")
        if debit_credit_balanced(debit_credit_totals):
            raise ValidationError("draft postings must balance by currency under debit_credit semantics")


def draft_from_record(row: DraftRecord, postings: list[Posting]) -> DraftTransaction:
    return DraftTransaction(
        draft_id=row.draft_id,
        memo=row.memo,
        state=row.state,
        proposed_postings=postings,
        missing_fields=list(row.missing_fields),
        source=row.source,
        confidence=row.confidence,
        book_id=row.book_id,
        version=row.version,
        attachment_id=row.attachment_id,
        category_id=row.category_id,
        metadata=dict(row.metadata_json or {}),
    )


def _validate_new_draft_posting(posting: Posting, *, allow_legacy_signed: bool) -> None:
    validate_posting_semantic_shape(
        side=posting.side,
        amount=posting.amount,
        amount_semantics=posting.amount_semantics,
    )
    if posting.amount_semantics == "legacy_signed" and not allow_legacy_signed:
        raise ValidationError("new draft postings must use debit_credit semantics")


def _legacy_side_for_posting(session, posting: Posting) -> str | None:
    account_type = session.scalar(select(AccountRecord.type).where(AccountRecord.account_id == posting.account_id))
    if account_type is None:
        return None
    side, _amount = legacy_signed_amount_to_debit_credit(account_type, posting.amount)
    return side


def _storage_side_for_posting(session, posting: Posting) -> str | None:
    if posting.side is not None or posting.amount_semantics != "legacy_signed":
        return posting.side
    return _legacy_side_for_posting(session, posting)


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
