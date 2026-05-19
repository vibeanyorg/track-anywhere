from __future__ import annotations

from collections.abc import Callable
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
    stored_result: Any | None = None
    replay_count: int = 0

    def __post_init__(self) -> None:
        if self.stored_result is None:
            self.stored_result = self.result


class IdempotencyStore:
    def __init__(self) -> None:
        self._receipts: dict[tuple[str, str, str], CommandReceipt] = {}

    def run(
        self,
        *,
        key: str,
        actor: Actor,
        operation: str,
        request_hash: str,
        fn: Callable[[], Any],
        stored_result_factory: Callable[[Any], Any] | None = None,
    ):
        key_hash = hash_secret(key)
        receipt_key = (key_hash, actor.actor_id, operation)
        receipt = self._receipts.get(receipt_key)
        if receipt is not None:
            if receipt.request_hash != request_hash:
                raise IdempotencyConflict("idempotency key reused with different payload")
            receipt.replay_count += 1
            return receipt.result, True
        result = fn()
        stored_result = stored_result_factory(result) if stored_result_factory is not None else result
        self._receipts[receipt_key] = CommandReceipt(
            key_hash=key_hash,
            actor_id=actor.actor_id,
            operation=operation,
            request_hash=request_hash,
            result=result,
            stored_result=stored_result,
        )
        return result, False
