from __future__ import annotations

from ..storage_changes import BookChanges, BudgetChanges, CategoryHistoryChanges, WriteMetadata


class ServiceChangeSetCollectors:
    def _write_metadata(self) -> WriteMetadata:
        return WriteMetadata(
            credentials=tuple(self.credentials.dirty_credentials()),
            audit_events=tuple(self.audit.pending_events()),
            idempotency_receipts=tuple(self.idempotency.dirty_receipts()),
        )

    def _category_history_changes(self) -> CategoryHistoryChanges:
        aliases, versions, events = self.categories.dirty_history()
        return CategoryHistoryChanges(
            aliases=tuple(aliases),
            versions=tuple(versions),
            events=tuple(events),
        )

    def _book_changes(self) -> BookChanges:
        return BookChanges(
            books=tuple(self.books.dirty_books()),
            members=tuple(self.books.dirty_members()),
        )

    def _budget_changes(self) -> BudgetChanges:
        return BudgetChanges(
            budgets=tuple(self.budgets.dirty_budgets()),
            targets=tuple(self.budgets.dirty_targets()),
        )
