from __future__ import annotations

from datetime import timedelta

from .errors import PolicyDenied
from .service_auth import OWNER_SCOPES


class OwnerCredentialBootstrap:
    def _ensure_owner_credential(self) -> None:
        try:
            actor = self.credentials.verify(self.owner_token)
            if OWNER_SCOPES.issubset(actor.scopes):
                return
        except PolicyDenied:
            pass
        self.credentials.issue(
            actor_id="owner",
            actor_type="human",
            scopes=set(OWNER_SCOPES),
            ttl=timedelta(days=30),
            token=self.owner_token,
            auth_kind="owner",
            name="Owner credential",
        )
