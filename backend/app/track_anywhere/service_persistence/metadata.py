from __future__ import annotations

from ..storage_changes import AuditChanges, CredentialChanges, IdempotencyChanges, WriteMetadata


class ServiceMetadataPersistence:
    def _idempotency_metadata(self) -> WriteMetadata:
        return WriteMetadata(
            credentials=tuple(self.credentials.dirty_credentials()),
            idempotency_receipts=tuple(self.idempotency.dirty_receipts()),
        )

    def _mark_metadata_committed(self, metadata: WriteMetadata) -> None:
        if metadata.credentials:
            self.credentials.mark_clean()
        if metadata.audit_events:
            self.audit.mark_persisted()
        if metadata.idempotency_receipts:
            self.idempotency.mark_clean()

    def _commit_idempotency(self) -> None:
        metadata = self._idempotency_metadata()
        self.storage.save_idempotency(IdempotencyChanges(metadata=metadata))
        self._mark_metadata_committed(metadata)

    def _commit_credential_change(self) -> None:
        metadata = self._write_metadata()
        self.storage.save_credential_change(CredentialChanges(metadata=metadata))
        self._mark_metadata_committed(metadata)

    def _commit_audit_event(self, event) -> None:
        metadata = WriteMetadata(audit_events=(event,))
        self.storage.save_audit_change(AuditChanges(metadata=metadata))
        self._mark_metadata_committed(metadata)

    def _commit_replay_or(self, replay: bool, commit) -> None:
        if replay:
            self._commit_idempotency()
        else:
            commit()
