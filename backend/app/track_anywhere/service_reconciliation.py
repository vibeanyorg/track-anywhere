from __future__ import annotations

from typing import Any

from . import commands


class ReconciliationUseCases:
    def record_reconciliation_action(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "ledger:confirm")
        command = commands.ReconciliationActionCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            action = {
                "reconciliation_id": f"recon_{len(self.reconciliation_actions) + 1}",
                "summary": command.summary,
                "version": 1,
            }
            self.reconciliation_actions.append(action)
            self.audit.record(
                operation="reconciliation.record",
                actor=actor,
                entity_ref=action["reconciliation_id"],
                details=action,
            )
            return action

        action, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="reconciliation.record",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_finance_change(actions=(action,)))
        return action, replay
