from __future__ import annotations

from .storage_audit_idempotency_writers import AuditIdempotencyStorageWriters
from .storage_upsert_writers import StorageUpsertWriters


class StorageWriters(
    AuditIdempotencyStorageWriters,
    StorageUpsertWriters,
):
    pass
