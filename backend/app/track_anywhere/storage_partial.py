from __future__ import annotations

from .storage_change_writers import (
    CatalogChangeStorageWriters,
    DirectoryChangeStorageWriters,
    FinanceChangeStorageWriters,
    LedgerChangeStorageWriters,
    PlatformAuthGrantStorageWriters,
    ProfileChangeStorageWriters,
    StorageMetadataWriters,
    WorkflowChangeStorageWriters,
)


class PartialStorageWriters(
    StorageMetadataWriters,
    CatalogChangeStorageWriters,
    LedgerChangeStorageWriters,
    DirectoryChangeStorageWriters,
    PlatformAuthGrantStorageWriters,
    WorkflowChangeStorageWriters,
    FinanceChangeStorageWriters,
    ProfileChangeStorageWriters,
):
    pass
