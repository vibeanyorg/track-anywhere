from __future__ import annotations

from .errors import NotFound


class DraftStoreUseCases:
    def _stored_draft(self, draft_id: str):
        draft = self.storage.get_draft(draft_id)
        if draft is None:
            raise NotFound(f"draft not found: {draft_id}")
        return draft
