from __future__ import annotations

from .storage_audit_idempotency_writers import AuditIdempotencyStorageWriters
from .storage_finance_writers import FinanceStorageWriters
from .storage_upsert_writers import StorageUpsertWriters


class StorageWriters(
    FinanceStorageWriters,
    AuditIdempotencyStorageWriters,
    StorageUpsertWriters,
):
    pass
