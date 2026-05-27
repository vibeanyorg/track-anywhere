from __future__ import annotations

from ..storage_changes import CredentialChanges, IdempotencyChanges


class ServiceMetadataPersistence:
    def _commit_idempotency(self) -> None:
        self.storage.save_idempotency(IdempotencyChanges(metadata=self._write_metadata()))
        self.credentials.mark_clean()
        self.idempotency.mark_clean()

    def _commit_credential_change(self) -> None:
        self.storage.save_credential_change(CredentialChanges(metadata=self._write_metadata()))
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _commit_replay_or(self, replay: bool, commit) -> None:
        if replay:
            self._commit_idempotency()
        else:
            commit()
