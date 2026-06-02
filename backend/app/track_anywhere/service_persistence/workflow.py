from __future__ import annotations

from ..storage_changes import (
    AttachmentChanges,
    CreditCardProfileChanges,
    DraftChanges,
    PaymentProfileChanges,
    RecurringChanges,
)


class ServiceWorkflowPersistence:
    def _commit_draft_change(
        self,
        *drafts,
        transactions=(),
        allow_legacy_signed_postings: bool = False,
    ) -> None:
        metadata = self._write_metadata()
        changes = DraftChanges(
            drafts=tuple(drafts),
            transactions=tuple(transactions),
            allow_legacy_signed_postings=allow_legacy_signed_postings,
            metadata=metadata,
        )
        self.storage.save_draft_change(changes)
        self._mark_metadata_committed(metadata)

    def _commit_recurring_change(self, *items, drafts=(), accounts=()) -> None:
        metadata = self._write_metadata()
        changes = RecurringChanges(
            items=tuple(items),
            drafts=tuple(drafts),
            accounts=tuple(accounts),
            metadata=metadata,
        )
        self.storage.save_recurring_change(changes)
        self._mark_metadata_committed(metadata)


class ServiceProfilePersistence:
    def _commit_credit_card_profile_change(self, *profiles) -> None:
        metadata = self._write_metadata()
        changes = CreditCardProfileChanges(profiles=tuple(profiles), metadata=metadata)
        self.storage.save_credit_card_profile_change(changes)
        self._mark_metadata_committed(metadata)

    def _commit_payment_profile_change(self) -> None:
        profiles = self.payment_profiles.dirty_profiles()
        metadata = self._write_metadata()
        changes = PaymentProfileChanges(profiles=tuple(profiles), metadata=metadata)
        self.storage.save_payment_profile_change(changes)
        self._mark_metadata_committed(metadata)
        self.payment_profiles.mark_clean()

    def _commit_attachment_change(self, *, attachments=(), drafts=()) -> None:
        metadata = self._write_metadata()
        changes = AttachmentChanges(
            metadata=metadata,
            attachments=tuple(attachments),
            drafts=tuple(drafts),
        )
        self.storage.save_attachment_change(changes)
        self._mark_metadata_committed(metadata)
