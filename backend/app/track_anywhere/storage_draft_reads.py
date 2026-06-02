from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import func, select

from .accounting import (
    STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING,
    posting_balance_delta,
    storage_posting_amount_semantics_or_dirty,
)
from .drafts import DraftTransaction
from .errors import ValidationError
from .ledger import Posting
from .storage_models import AccountRecord
from .storage_models import DraftPostingRecord, DraftRecord


OPEN_DRAFT_STATES = {"ready_to_confirm", "needs_review", "parsed", "captured"}


class DraftReadStorage:
    def get_draft(self, draft_id: str) -> DraftTransaction | None:
        cached = self._cached_get("drafts", draft_id)
        if cached is not None or self._cache_loaded("drafts"):
            return cached
        with self.session_factory() as session:
            row = session.get(DraftRecord, draft_id)
            if row is None:
                return None
            postings = self._draft_postings_by_id(session, [draft_id])
            return _draft_from_row(row, postings.get(draft_id, []))

    def list_drafts(
        self,
        *,
        book_id: str | None = None,
        states: Iterable[str] | None = None,
        account_id: str | None = None,
    ) -> list[DraftTransaction]:
        drafts = self._cached_values("drafts")
        if drafts is None:
            with self.session_factory() as session:
                rows = list(session.scalars(select(DraftRecord)))
                postings = self._draft_postings_by_id(session, [row.draft_id for row in rows])
                drafts = [_draft_from_row(row, postings.get(row.draft_id, [])) for row in rows]
        state_filter = set(states) if states is not None else None
        if book_id is not None:
            drafts = [draft for draft in drafts if draft.book_id == book_id]
        if state_filter is not None:
            drafts = [draft for draft in drafts if draft.state in state_filter]
        if account_id is not None:
            drafts = [
                draft
                for draft in drafts
                if any(posting.account_id == account_id for posting in draft.proposed_postings)
            ]
        return sorted(drafts, key=lambda draft: draft.draft_id)

    def draft_count(self) -> int:
        cached = getattr(self, "_read_drafts", None)
        if cached is not None:
            return len(cached)
        with self.session_factory() as session:
            return int(session.scalar(select(func.count(DraftRecord.draft_id))) or 0)

    def draft_projection_for_account(self, account_id: str) -> tuple[dict[str, Decimal], list[str], int]:
        totals: dict[str, Decimal] = {}
        included_draft_ids: list[str] = []
        account_type = self._account_type_for_draft_projection(account_id)
        if account_type is None:
            return totals, included_draft_ids, self.draft_count()
        for draft in self.list_drafts(states=OPEN_DRAFT_STATES, account_id=account_id):
            included = False
            for posting in draft.proposed_postings:
                if posting.account_id != account_id:
                    continue
                try:
                    amount = posting_balance_delta(
                        account_type,
                        side=posting.side,
                        amount=posting.amount,
                        amount_semantics=posting.amount_semantics,
                    )
                except ValidationError:
                    continue
                totals[posting.currency] = totals.get(posting.currency, Decimal("0")) + amount
                included = True
            if included:
                included_draft_ids.append(draft.draft_id)
        return totals, included_draft_ids, self.draft_count()

    def _account_type_for_draft_projection(self, account_id: str) -> str | None:
        cached_accounts = getattr(self, "_read_accounts", None)
        if cached_accounts is not None and account_id in cached_accounts:
            return cached_accounts[account_id].type
        with self.session_factory() as session:
            account_type = session.scalar(select(AccountRecord.type).where(AccountRecord.account_id == account_id))
        return account_type

    def _draft_postings_by_id(self, session, draft_ids: Iterable[str]) -> dict[str, list[Posting]]:
        ids = list(dict.fromkeys(draft_ids))
        if not ids:
            return {}
        rows = session.scalars(
            select(DraftPostingRecord)
            .where(DraftPostingRecord.draft_id.in_(ids))
            .order_by(DraftPostingRecord.draft_id, DraftPostingRecord.position, DraftPostingRecord.id)
        )
        postings: dict[str, list[Posting]] = {}
        for row in rows:
            postings.setdefault(row.draft_id, []).append(
                Posting(
                    row.account_id,
                    Decimal(row.amount),
                    row.currency,
                    side=getattr(row, "side", None),
                    amount_semantics=storage_posting_amount_semantics_or_dirty(
                        getattr(row, "amount_semantics", STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING)
                    ),
                )
            )
        return postings


def _draft_from_row(row: DraftRecord, postings: list[Posting]) -> DraftTransaction:
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
