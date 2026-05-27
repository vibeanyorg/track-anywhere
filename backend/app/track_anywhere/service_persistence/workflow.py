from __future__ import annotations

from ..storage_changes import (
    AttachmentChanges,
    CreditCardProfileChanges,
    DraftChanges,
    PaymentProfileChanges,
    RecurringChanges,
)


class ServiceWorkflowPersistence:
    def _commit_draft_change(self, *drafts, transactions=()) -> None:
        changes = DraftChanges(drafts=tuple(drafts), transactions=tuple(transactions), metadata=self._write_metadata())
        self.storage.save_draft_change(changes)
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()
    def _commit_recurring_change(self, *items, drafts=(), accounts=()) -> None:
        changes = RecurringChanges(
            items=tuple(items),
            drafts=tuple(drafts),
            accounts=tuple(accounts),
            metadata=self._write_metadata(),
        )
        self.storage.save_recurring_change(changes)
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceProfilePersistence:
    def _commit_credit_card_profile_change(self, *profiles) -> None:
        self.storage.save_credit_card_profile_change(
            CreditCardProfileChanges(profiles=tuple(profiles), metadata=self._write_metadata())
        )
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _commit_payment_profile_change(self) -> None:
        profiles = self.payment_profiles.dirty_profiles()
        changes = PaymentProfileChanges(profiles=tuple(profiles), metadata=self._write_metadata())
        self.storage.save_payment_profile_change(changes)
        self.payment_profiles.mark_clean()
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _commit_attachment_change(self, *, attachments=(), drafts=()) -> None:
        changes = AttachmentChanges(
            metadata=self._write_metadata(),
            attachments=tuple(attachments),
            drafts=tuple(drafts),
        )
        self.storage.save_attachment_change(changes)
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()
