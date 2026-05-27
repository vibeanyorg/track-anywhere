from __future__ import annotations

from .db_migrations import current_alembic_head


class SystemStatusUseCases:
    def system_readiness(self) -> dict[str, object]:
        state = self.storage.database_readiness()
        expected_revision = current_alembic_head()
        checks = {
            "database": "ok",
            "migrations": "ok" if state["alembic_revision"] == expected_revision else "error",
        }
        return {
            "status": "ok" if all(value == "ok" for value in checks.values()) else "error",
            "api_version": "v1",
            "database": state["database"],
            "schema": state["schema"],
            "alembic_revision": state["alembic_revision"],
            "expected_revision": expected_revision,
            "checks": checks,
        }

    def system_status(self, token, *, include_counts: bool = False) -> dict[str, object]:
        actor = self.actor_from_token(token, required_scope="ledger:read")
        payload = self.system_readiness()
        payload["actor"] = {
            "actor_id": actor.actor_id,
            "actor_type": actor.actor_type,
            "scopes": sorted(actor.scopes),
        }
        if include_counts:
            payload["counts"] = self.storage.status_table_counts()
        return payload
