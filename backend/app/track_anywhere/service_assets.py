from __future__ import annotations

from .assets import DEFAULT_ASSETS, AssetDefinition


class AssetUseCases:
    def list_assets(self, token: str, *, status: str | None = "active") -> list[AssetDefinition]:
        actor = self.actor_from_token(token, "account:read")
        assets = self._visible_assets_for_actor(actor)
        if status is not None:
            assets = [asset for asset in assets if asset.status == status]
        return sorted(assets, key=lambda asset: (asset.kind, asset.asset_code))

    def _visible_assets_for_actor(self, actor) -> list[AssetDefinition]:
        if actor.actor_id == "owner":
            return list(self.assets.assets.values())
        visible_codes = set(DEFAULT_ASSETS)
        book_ids = {
            book.book_id
            for book in self.books.books.values()
            if self.books.has_access(book.book_id, actor, "account:read")
        }
        for book_id in book_ids:
            visible_codes.add(self.books.books[book_id].base_currency)
        for account in self.storage.list_accounts(book_id=None):
            if account.book_id in book_ids:
                visible_codes.add(account.currency)
        for book_id in book_ids:
            for transaction in self.storage.list_all_confirmed_transactions(book_id=book_id):
                visible_codes.update(posting.currency for posting in transaction.postings)
                visible_codes.update(line.currency for line in transaction.lines)
        for item in self.recurring.items.values():
            if item.book_id in book_ids and item.currency is not None:
                visible_codes.add(item.currency)
        for fund in self.budgets.funds.values():
            if fund.book_id in book_ids:
                visible_codes.add(fund.currency)
        for budget in self.budgets.budgets.values():
            if budget.book_id in book_ids:
                visible_codes.add(budget.currency)
        for event in self.investments.events.values():
            if event.book_id in book_ids:
                visible_codes.add(event.currency)
        for valuation in self.investments.valuations.values():
            if valuation.book_id in book_ids:
                visible_codes.add(valuation.currency)
        return [self.assets.ensure(code) for code in visible_codes]
