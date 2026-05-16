from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import IdempotencyConflict
from .security import Actor, hash_secret


@dataclass
class CommandReceipt:
    key_hash: str
    actor_id: str
    operation: str
    request_hash: str
    result: Any
    replay_count: int = 0


class IdempotencyStore:
    def __init__(self) -> None:
        self._receipts: dict[tuple[str, str, str], CommandReceipt] = {}

    def run(self, *, key: str, actor: Actor, operation: str, request_hash: str, fn):
        receipt_key = (hash_secret(key), actor.actor_id, operation)
        receipt = self._receipts.get(receipt_key)
        if receipt is not None:
            if receipt.request_hash != request_hash:
                raise IdempotencyConflict("idempotency key reused with different payload")
            receipt.replay_count += 1
            return receipt.result, True
        result = fn()
        self._receipts[receipt_key] = CommandReceipt(
            key_hash=hash_secret(key),
            actor_id=actor.actor_id,
            operation=operation,
            request_hash=request_hash,
            result=result,
        )
        return result, False
