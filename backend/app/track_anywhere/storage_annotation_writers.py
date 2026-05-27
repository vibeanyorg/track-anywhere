from __future__ import annotations

from .domain_storage_models import TransactionLineRecord
from .errors import NotFound
from .ledger import Transaction, TransactionLine
from .storage_audit_idempotency_writers import save_audit_events, save_idempotency_receipts
from .storage_changes import ReclassificationChanges
from .storage_json import to_jsonable


class AnnotationStorageWriters:
    def save_reclassification_change(self, changes: ReclassificationChanges) -> None:
        line = _line_by_id(changes.transaction, changes.line_id)
        with self.session_factory.begin() as session:
            self._upsert_transaction_line(session, line)
            self._save_category_history(
                session,
                aliases=changes.category_history.aliases,
                versions=changes.category_history.versions,
                events=changes.category_history.events,
            )
            save_audit_events(session, changes.metadata.audit_events)
            save_idempotency_receipts(session, changes.metadata.idempotency_receipts)
        self.update_read_cache(transactions=(changes.transaction,))

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
                "counterparty_id": line.counterparty_id,
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
