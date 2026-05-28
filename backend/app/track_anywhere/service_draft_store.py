from __future__ import annotations

from .errors import NotFound


class DraftStoreUseCases:
    def _stored_draft(self, draft_id: str):
        draft = self._get_draft_from_storage(draft_id)
        if draft is None:
            raise NotFound(f"draft not found: {draft_id}")
        return draft

    def _get_draft_from_storage(self, draft_id: str):
        with self.storage.unit_of_work() as uow:
            return uow.drafts.get_draft(draft_id)
