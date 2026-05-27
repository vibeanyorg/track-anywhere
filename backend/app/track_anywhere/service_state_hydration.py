from __future__ import annotations

from .storage_snapshot import StorageSnapshot


class ServiceStateHydration:
    def _apply_storage_snapshot(self, snapshot: StorageSnapshot) -> None:
        self.books.books = snapshot.books
        self.books.members = snapshot.book_members
        self.books.mark_clean()
        self.assets.assets.update(snapshot.assets)
        self.assets.ensure_defaults()
        self.users.users = snapshot.users
        self.auth_identities.identities = snapshot.auth_identities
        self.drafts.drafts = snapshot.drafts
        self.recurring.items = snapshot.recurring_items
        self.budgets.funds = snapshot.budget_funds
        self.budgets.budgets = snapshot.budgets
        self.budgets.targets = snapshot.budget_targets
        self.budgets.mark_clean()
        self.counterparties.counterparties = snapshot.counterparties
        self.counterparties.mark_clean()
        self.payment_profiles.profiles = snapshot.payment_profiles
        self.payment_profiles.mark_clean()
        self.payment_instruments.instruments = snapshot.payment_instruments
        self.payment_instruments.mark_clean()
        self.investments.events = snapshot.investment_events
        self.investments.valuations = snapshot.investment_valuations
        self.categories.categories = snapshot.categories
        self.categories.aliases = snapshot.category_aliases
        self.categories.versions = snapshot.category_versions
        self.categories.events = snapshot.classification_events
        self.categories.mark_clean()
        self.credit_cards.profiles = snapshot.credit_card_profiles
        self.attachments.attachments = snapshot.attachments
        self.credentials._credentials = snapshot.credentials
        self.credentials.mark_clean()
        self.audit.events = snapshot.audit_events
        self.audit.mark_persisted()
        self.idempotency._receipts = snapshot.idempotency_receipts
        self.idempotency.mark_clean()
        self.reconciliation_actions = snapshot.reconciliation_actions
        self.adjustment_account_ids = snapshot.adjustment_account_ids
        if snapshot.owner_token is not None:
            self.owner_token = snapshot.owner_token
            self._startup_persist_required = True
