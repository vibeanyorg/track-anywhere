from __future__ import annotations

from .storage_audit_idempotency_writers import AuditIdempotencyStorageWriters
from .storage_finance_writers import FinanceStorageWriters
from .storage_ledger_writers import LedgerStorageWriters
from .storage_upsert_writers import StorageUpsertWriters
from .storage_workflow_writers import WorkflowStorageWriters


class StorageWriters(
    LedgerStorageWriters,
    WorkflowStorageWriters,
    FinanceStorageWriters,
    AuditIdempotencyStorageWriters,
    StorageUpsertWriters,
):
    pass
