from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import uuid4

from .books import DEFAULT_BOOK_ID
from .errors import NotFound, StaleVersion, ValidationError
from .ledger import Posting


@dataclass
class DraftTransaction:
    draft_id: str
    memo: str
    state: str
    proposed_postings: list[Posting]
    missing_fields: list[str]
    source: str
    confidence: float
    book_id: str = DEFAULT_BOOK_ID
    version: int = 1
    attachment_id: str | None = None
    category_id: str | None = None
    metadata: dict[str, Any] | None = None


class DraftBook:
    def __init__(self) -> None:
        self.drafts: dict[str, DraftTransaction] = {}

    def create(
        self,
        *,
        memo: str,
        proposed_postings: list[Posting],
        missing_fields: list[str],
        source: str,
        confidence: float,
        attachment_id: str | None = None,
        category_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        book_id: str = DEFAULT_BOOK_ID,
    ) -> DraftTransaction:
        state = "ready_to_confirm" if not missing_fields and proposed_postings else "needs_review"
        draft = DraftTransaction(
            draft_id=f"draft_{uuid4().hex}",
            memo=memo,
            state=state,
            proposed_postings=proposed_postings,
            missing_fields=missing_fields,
            source=source,
            confidence=confidence,
            book_id=book_id,
            attachment_id=attachment_id,
            category_id=category_id,
            metadata=metadata or {},
        )
        self.drafts[draft.draft_id] = draft
        return draft

    def get(self, draft_id: str) -> DraftTransaction | None:
        return self.drafts.get(draft_id)

    def require_current(self, draft_id: str, expected_version: int) -> DraftTransaction:
        draft = self.get(draft_id)
        if draft is None:
            raise NotFound(f"draft not found: {draft_id}")
        if draft.version != expected_version:
            raise StaleVersion("draft version conflict")
        return draft

    def reject(self, draft_id: str, expected_version: int) -> DraftTransaction:
        draft = self.require_current(draft_id, expected_version)
        if draft.state in {"confirmed", "rejected", "superseded"}:
            raise ValidationError(f"draft cannot be rejected from state: {draft.state}")
        draft.state = "rejected"
        draft.version += 1
        return draft

    def supersede(self, draft_id: str, expected_version: int, replacement: DraftTransaction) -> DraftTransaction:
        draft = self.require_current(draft_id, expected_version)
        if draft.state in {"confirmed", "rejected", "superseded"}:
            raise ValidationError(f"draft cannot be superseded from state: {draft.state}")
        draft.state = "superseded"
        draft.version += 1
        return replacement

    def projected_impact(self, account_id: str) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for draft in self.drafts.values():
            if draft.state not in {"ready_to_confirm", "needs_review", "parsed", "captured"}:
                continue
            for posting in draft.proposed_postings:
                if posting.account_id == account_id:
                    totals[posting.currency] = totals.get(posting.currency, Decimal("0")) + posting.amount
        return totals
