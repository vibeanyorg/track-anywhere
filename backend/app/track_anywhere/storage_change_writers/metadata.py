from __future__ import annotations

from ..storage_changes import CredentialChanges, IdempotencyChanges, WriteMetadata


class StorageMetadataWriters:
    def save_idempotency(self, changes: IdempotencyChanges) -> None:
        with self.unit_of_work() as uow:
            uow.credentials.save(changes.metadata.credentials)
            uow.idempotency.save_receipts(changes.metadata.idempotency_receipts)

    def save_credential_change(self, changes: CredentialChanges) -> None:
        with self.unit_of_work() as uow:
            self._save_write_metadata(uow, changes.metadata)

    @staticmethod
    def _save_write_metadata(uow, metadata: WriteMetadata) -> None:
        uow.credentials.save(metadata.credentials)
        uow.audit.save_events(metadata.audit_events)
        uow.idempotency.save_receipts(metadata.idempotency_receipts)
