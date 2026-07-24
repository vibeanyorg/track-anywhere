from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ..idempotency import CommandActor
from ..ledger_committer import LedgerCommitter
from ..privacy.service import ProtectedContentService
from ...infrastructure.crypto import DuplicateDetectionKeyProvider
from .commit import EntryCommitRuntime, commit_entry
from .contracts import (
    CommitEntryInput,
    CommittedEntry,
    EverydayEntryInput,
    PreparedEntry,
)
from .prepare import EntryPreparationRuntime, UnitOfWorkFactory, prepare_entry


@dataclass(frozen=True, slots=True)
class RequestScopedEverydayEntryService:
    """Authenticated application facade shared by REST, MCP, and CLI adapters."""

    actor: CommandActor
    uow_factory: UnitOfWorkFactory
    ledger_committer: LedgerCommitter
    protected_content_service: ProtectedContentService | None
    duplicate_key_provider: DuplicateDetectionKeyProvider | None

    def prepare(
        self,
        *,
        book_id: UUID,
        entry: EverydayEntryInput,
    ) -> PreparedEntry:
        return prepare_entry(
            book_id=book_id,
            entry=entry,
            runtime=EntryPreparationRuntime(
                actor=self.actor,
                uow_factory=self.uow_factory,
                protected_content_service=self.protected_content_service,
                duplicate_key_provider=self.duplicate_key_provider,
            ),
        )

    def commit(
        self,
        *,
        book_id: UUID,
        command: CommitEntryInput,
    ) -> CommittedEntry:
        return commit_entry(
            book_id=book_id,
            command=command,
            runtime=EntryCommitRuntime(
                actor=self.actor,
                uow_factory=self.uow_factory,
                ledger_committer=self.ledger_committer,
                protected_content_service=self.protected_content_service,
            ),
        )


__all__ = ["RequestScopedEverydayEntryService"]
