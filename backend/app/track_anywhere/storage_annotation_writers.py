from __future__ import annotations

from .domain_storage_models import TransactionLineRecord
from .errors import NotFound
from .ledger import Transaction, TransactionLine
from .storage_json import to_jsonable


class AnnotationStorageWriters:
    def save_reclassification_change(self, service, transaction: Transaction, line_id: str) -> None:
        line = _line_by_id(transaction, line_id)
        dirty_aliases, dirty_versions, dirty_events = service.categories.dirty_history()
        pending_events = service.audit.pending_events()
        dirty_receipts = service.idempotency.dirty_receipts()
        with self.session_factory.begin() as session:
            self._upsert_transaction_line(session, line)
            self._save_category_history(
                session,
                service.categories,
                aliases=dirty_aliases,
                versions=dirty_versions,
                events=dirty_events,
            )
            self._save_audit_events(session, pending_events)
            self._save_idempotency_receipts(session, dirty_receipts)
        service.categories.mark_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def _upsert_transaction_line(self, session, line: TransactionLine) -> None:
        self._upsert_record(
            session,
            TransactionLineRecord,
            {
                "line_id": line.line_id,
                "transaction_id": line.transaction_id,
                "position": line.position,
                "line_type": line.line_type,
                "amount": str(line.amount),
                "currency": line.currency,
                "book_id": line.book_id,
                "category_id": line.category_id,
                "category_version_id": line.category_version_id,
                "category_path_snapshot": to_jsonable(line.category_path_snapshot),
                "merchant_id": line.merchant_id,
                "project_id": line.project_id,
                "necessity": line.necessity,
                "reimbursement_status": line.reimbursement_status,
                "memo": line.memo,
                "version": line.version,
            },
            ["line_id"],
        )


def _line_by_id(transaction: Transaction, line_id: str) -> TransactionLine:
    for line in transaction.lines:
        if line.line_id == line_id:
            return line
    raise NotFound(f"transaction line not found: {line_id}")
