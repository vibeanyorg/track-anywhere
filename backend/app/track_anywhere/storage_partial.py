from __future__ import annotations

from .storage_change_writers import (
    CatalogChangeStorageWriters,
    DirectoryChangeStorageWriters,
    FinanceChangeStorageWriters,
    LedgerChangeStorageWriters,
    ProfileChangeStorageWriters,
    StorageMetadataWriters,
    WorkflowChangeStorageWriters,
)


class PartialStorageWriters(
    StorageMetadataWriters,
    CatalogChangeStorageWriters,
    LedgerChangeStorageWriters,
    DirectoryChangeStorageWriters,
    WorkflowChangeStorageWriters,
    FinanceChangeStorageWriters,
    ProfileChangeStorageWriters,
):
    pass
