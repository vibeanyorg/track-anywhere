from __future__ import annotations

from ..storage_changes import AttachmentChanges, CreditCardProfileChanges, PaymentProfileChanges


class ProfileChangeStorageWriters:
    def save_credit_card_profile_change(self, changes: CreditCardProfileChanges) -> None:
        with self.unit_of_work() as uow:
            uow.credit_cards.save_profiles(changes.profiles)
            self._save_write_metadata(uow, changes.metadata)
        self.update_read_cache(credit_card_profiles=changes.profiles)

    def save_payment_profile_change(self, changes: PaymentProfileChanges) -> None:
        with self.unit_of_work() as uow:
            uow.payment_profiles.save(changes.profiles)
            self._save_write_metadata(uow, changes.metadata)
        self.update_read_cache(payment_profiles=changes.profiles)

    def save_attachment_change(self, changes: AttachmentChanges) -> None:
        with self.unit_of_work() as uow:
            uow.attachments.save(changes.attachments)
            uow.drafts.save(changes.drafts)
            self._save_write_metadata(uow, changes.metadata)
        self.update_read_cache(drafts=changes.drafts)
